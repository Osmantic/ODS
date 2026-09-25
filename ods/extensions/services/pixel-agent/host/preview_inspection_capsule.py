#!/usr/bin/env python3
"""Untrusted site execution lives only in the short-lived no-network capsule."""

import collections
import http.server
import itertools
import mimetypes
import os
import re
import struct
import sys
import threading
import time
import unicodedata
import urllib.parse
import zlib
from preview_inspection_protocol import (
    CSP,
    Invalid,
    KIND,
    MAX_BUNDLE,
    MAX_RESULT,
    SANDBOX,
    SCOPE,
    canonical,
    failure,
    plan_hash,
    strict_json,
    validate_bundle,
)

# Runs in a Chromium isolated world, not the site's mutable JS global realm.
OBSERVE_ELEMENT = r"""function() {
  const element = this, style = getComputedStyle(element);
  const rects = [...element.getClientRects()].filter(r => r.width > 0 && r.height > 0);
  return {count:1, visible:element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}) && rects.length > 0,
    display:style.display, visibility:style.visibility, opacity:style.opacity,
    hidden:element.hasAttribute('hidden'), hiddenUntilFound:element.getAttribute('hidden') === 'until-found',
    rectCount:rects.length};
}"""

DIAGNOSTIC = r"""function() {
  let count=0, untilFound=0;
  for (const element of document.querySelectorAll('[hidden]')) {
    if(element.getAttribute('hidden')==='until-found') { untilFound++; continue; }
    if(element.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}) && [...element.getClientRects()].some(r=>r.width>0 && r.height>0)) count++;
  }
  return {renderedHiddenAttributeCount:count, hiddenUntilFoundCount:untilFound};
}"""

# Catch only the browser's selector-parser failure in the isolated world. Do
# not infer syntax from an exception string or rewrite the owner's selector.
SELECTOR_COUNT = r"""function(selector) {
  try { return {count:document.querySelectorAll(selector).length}; }
  catch (error) {
    if (error instanceof DOMException && error.name === 'SyntaxError') return {invalidSelector:true};
    throw error;
  }
}"""


# Chromium's accessibility tree omits hidden elements and computes no name for
# them, so an exact role/name locator could never address the element an
# assert-hidden step expects to be hidden. For assert-hidden only, exact
# role/name matching also includes hidden elements, following Playwright's
# getByRole(role, {name, exact: true, includeHidden: true}) role and name rules
# (script, style, template and noscript text never contributes). It runs in the
# isolated world, so page script cannot replace the DOM or style APIs it reads.
# Chromium's own rendered matches are passed in and kept, so a rendered element
# is matched exactly as before; the union is de-duplicated by identity.
ROLE_NAME_INCLUDING_HIDDEN = r"""function(role, name, ...rendered) {
  const VALID = new Set(('alert alertdialog application article banner blockquote button caption cell checkbox code ' +
    'columnheader combobox complementary contentinfo definition deletion dialog directory document emphasis feed figure ' +
    'form generic grid gridcell group heading img insertion link list listbox listitem log main mark marquee math meter ' +
    'menu menubar menuitem menuitemcheckbox menuitemradio navigation none note option paragraph presentation progressbar ' +
    'radio radiogroup region row rowgroup rowheader scrollbar search searchbox separator slider spinbutton status strong ' +
    'subscript superscript switch tab table tablist tabpanel term textbox time timer toolbar tooltip tree treegrid treeitem').split(' '));
  const GLOBAL = ('atomic busy controls current describedby details dropeffect flowto grabbed hidden keyshortcuts label ' +
    'labelledby live owns relevant roledescription').split(' ').map(a => 'aria-' + a);
  const CONTENT = new Set(('button cell checkbox columnheader gridcell heading link menuitem menuitemcheckbox menuitemradio ' +
    'option radio row rowheader switch tab tooltip treeitem').split(' '));
  const DESCENDANT = new Set(['', ...('caption code contentinfo definition deletion emphasis insertion list listitem mark none ' +
    'paragraph presentation region row rowgroup section strong subscript superscript table term time').split(' ')]);
  const TAG = {article:'article', aside:'complementary', blockquote:'blockquote', button:'button', caption:'caption', code:'code',
    datalist:'listbox', dd:'definition', del:'deletion', details:'group', dfn:'term', dialog:'dialog', dt:'term', em:'emphasis',
    fieldset:'group', figure:'figure', h1:'heading', h2:'heading', h3:'heading', h4:'heading', h5:'heading', h6:'heading',
    hr:'separator', html:'document', ins:'insertion', li:'listitem', main:'main', mark:'mark', math:'math', menu:'list',
    meter:'meter', nav:'navigation', ol:'list', optgroup:'group', option:'option', output:'status', p:'paragraph',
    progress:'progressbar', search:'search', strong:'strong', sub:'subscript', sup:'superscript', svg:'img', table:'table',
    tbody:'rowgroup', td:'cell', textarea:'textbox', tfoot:'rowgroup', th:'columnheader', thead:'rowgroup', time:'time',
    tr:'row', ul:'list'};
  const INPUT = {button:'button', checkbox:'checkbox', image:'button', number:'spinbutton', radio:'radio', range:'slider',
    reset:'button', submit:'button'};
  const IGNORED = new Set(['script', 'style', 'template', 'noscript']);
  const LANDMARK = 'article:not([role]), aside:not([role]), main:not([role]), nav:not([role]), section:not([role]), ' +
    '[role=article], [role=complementary], [role=main], [role=navigation], [role=region]';
  const tag = e => String(e.localName || '').toLowerCase();
  const style = (e, pseudo) => { try { return getComputedStyle(e, pseudo); } catch { return null; } };
  const idRefs = (e, attribute) => {
    const value = e.getAttribute(attribute), root = e.getRootNode(), out = [];
    for (const id of (value || '').split(' ').filter(Boolean)) {
      let target = null;
      try { target = root.querySelector('#' + CSS.escape(id)); } catch {}
      if (target && !out.includes(target)) out.push(target);
    }
    return out;
  };
  const named = e => e.hasAttribute('aria-label') || e.hasAttribute('aria-labelledby');
  const implicit = e => {
    const t = tag(e);
    if (t === 'a' || t === 'area') return e.hasAttribute('href') ? 'link' : null;
    if (t === 'select') return e.hasAttribute('multiple') || e.size > 1 ? 'listbox' : 'combobox';
    if (t === 'img') return e.getAttribute('alt') === '' && !e.getAttribute('title') && !GLOBAL.some(a => e.hasAttribute(a)) &&
      Number.isNaN(Number(String(e.getAttribute('tabindex')))) ? 'presentation' : 'img';
    if (t === 'header' || t === 'footer') return e.closest(LANDMARK) ? null : t === 'header' ? 'banner' : 'contentinfo';
    if (t === 'form' || t === 'section') return named(e) ? (t === 'form' ? 'form' : 'region') : null;
    if (t === 'input') {
      const type = String(e.type).toLowerCase();
      if (type === 'search') return e.hasAttribute('list') ? 'combobox' : 'searchbox';
      if (['email', 'tel', 'text', 'url', ''].includes(type)) {
        const list = idRefs(e, 'list')[0];
        return list && tag(list) === 'datalist' ? 'combobox' : 'textbox';
      }
      if (type === 'hidden') return null;
      return type === 'file' ? 'button' : INPUT[type] || 'textbox';
    }
    return TAG[t] || null;
  };
  const focusable = e => {
    const t = tag(e);
    if (e.disabled === true) return false;
    const native = ['button', 'details', 'select', 'textarea'].includes(t) ||
      ((t === 'a' || t === 'area') && e.hasAttribute('href')) || (t === 'input' && !e.hidden);
    return native || !Number.isNaN(Number(String(e.getAttribute('tabindex'))));
  };
  const roleOf = e => {
    const explicit = (e.getAttribute('role') || '').split(' ').map(r => r.trim()).find(r => VALID.has(r)) || null;
    if (!explicit) return implicit(e);
    if ((explicit === 'none' || explicit === 'presentation') && (GLOBAL.some(a => e.hasAttribute(a)) || focusable(e)))
      return implicit(e);
    return explicit;
  };
  const unescape = s => s.replace(/\\([0-9a-fA-F]{1,6})\s?|\\([\s\S])/g, (_, hex, ch) => {
    if (!hex) return ch;
    const code = parseInt(hex, 16);
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : '�';
  });
  const cssContent = (e, pseudo) => {
    const s = style(e, pseudo), value = s && s.content;
    if (!value || value === 'none' || value === 'normal' || s.display === 'none' || s.visibility === 'hidden') return undefined;
    const tokens = [], token = /\s*(?:"((?:[^"\\]|\\[\s\S])*)"|'((?:[^'\\]|\\[\s\S])*)'|attr\(\s*([^\s()]+)\s*\)|(\/))\s*/y;
    for (let at = 0; at < value.length;) {
      token.lastIndex = at;
      const m = token.exec(value);
      if (!m) return undefined;
      at = token.lastIndex;
      tokens.push(m[4] ? {slash: true} : m[3] !== undefined ? {text: e.getAttribute(m[3]) || ''} : {text: unescape(m[1] ?? m[2])});
    }
    const slash = tokens.findIndex(t => t.slash);
    if (slash === -1 && !pseudo) return undefined;
    const parts = slash === -1 ? tokens : tokens.slice(slash + 1);
    if (parts.some(t => t.slash)) return undefined;
    const text = parts.map(t => t.text).join('');
    return pseudo && (s.display || 'inline') !== 'inline' ? ' ' + text + ' ' : text;
  };
  const labels = e => { try { return [...(e.labels || [])]; } catch { return []; } };
  const fromLabels = (list, o) =>
    list.map(label => alternative(label, {visited: o.visited, label: true})).filter(Boolean).join(' ');
  const inner = (e, o) => {
    const out = [cssContent(e, '::before') || ''], own = cssContent(e);
    const visit = node => {
      if (node.nodeType === 1) {
        const display = (style(node) || {}).display || 'inline';
        const text = alternative(node, o);
        out.push(display !== 'inline' || node.nodeName === 'BR' ? ' ' + text + ' ' : text);
      } else if (node.nodeType === 3) out.push(node.textContent || '');
    };
    if (own !== undefined) out.push(own);
    else if (tag(e) === 'slot' && e.assignedNodes().length) e.assignedNodes().forEach(visit);
    else {
      for (let child = e.firstChild; child; child = child.nextSibling) if (!child.assignedSlot) visit(child);
      if (e.shadowRoot) for (let child = e.shadowRoot.firstChild; child; child = child.nextSibling) visit(child);
      idRefs(e, 'aria-owns').forEach(visit);
    }
    out.push(cssContent(e, '::after') || '');
    return out.join('');
  };
  function alternative(e, o) {
    const visited = o.visited, t = tag(e);
    if (visited.has(e)) return '';
    if (IGNORED.has(t)) { visited.add(e); return ''; }
    const child = {...o, target: o.target === 'self' ? 'descendant' : o.target};
    const labelledBy = e.hasAttribute('aria-labelledby') ? idRefs(e, 'aria-labelledby') : [];
    if (!o.labelledBy) {
      const text = labelledBy.map(ref => alternative(ref, {visited, labelledBy: true})).join(' ');
      if (text) return text;
    }
    const r = roleOf(e) || '';
    if (o.label || o.labelledBy || o.target === 'descendant') {
      if (r === 'textbox') { visited.add(e); return t === 'input' || t === 'textarea' ? e.value : e.textContent || ''; }
      if (r === 'combobox' || r === 'listbox') {
        visited.add(e);
        if (t !== 'select') return t === 'input' ? e.value : '';
        const selected = [...e.selectedOptions];
        if (!selected.length && e.options.length) selected.push(e.options[0]);
        return selected.map(option => alternative(option, child)).join(' ');
      }
      if (['progressbar', 'scrollbar', 'slider', 'spinbutton', 'meter'].includes(r)) {
        visited.add(e);
        for (const a of ['aria-valuetext', 'aria-valuenow']) if (e.hasAttribute(a)) return e.getAttribute(a) || '';
        return e.getAttribute('value') || '';
      }
      if (r === 'menu') { visited.add(e); return ''; }
    }
    const label = e.getAttribute('aria-label') || '';
    if (label.trim()) { visited.add(e); return label; }
    if (r !== 'presentation' && r !== 'none') {
      const type = t === 'input' ? String(e.type) : '';
      if (['button', 'submit', 'reset'].includes(type)) {
        visited.add(e);
        if ((e.value || '').trim()) return e.value;
        return type === 'submit' ? 'Submit' : type === 'reset' ? 'Reset' : e.getAttribute('title') || '';
      }
      if (type === 'file' || type === 'image') {
        visited.add(e);
        if (labels(e).length && !o.labelledBy) return fromLabels(labels(e), o);
        if (type === 'file') return 'Choose File';
        for (const a of ['alt', 'title']) if ((e.getAttribute(a) || '').trim()) return e.getAttribute(a);
        return 'Submit';
      }
      if (!labelledBy.length && t === 'button') { visited.add(e); if (labels(e).length) return fromLabels(labels(e), o); }
      if (!labelledBy.length && ['textarea', 'select', 'input'].includes(t)) {
        visited.add(e);
        if (labels(e).length) return fromLabels(labels(e), o);
        const placeholder = t === 'textarea' || ['text', 'password', 'search', 'tel', 'email', 'url'].includes(type);
        const title = e.getAttribute('title') || '';
        return !placeholder || title ? title : e.getAttribute('placeholder') || '';
      }
      if (t === 'img' || t === 'area') {
        visited.add(e);
        const alt = e.getAttribute('alt') || '';
        return alt.trim() ? alt : e.getAttribute('title') || '';
      }
    }
    if (CONTENT.has(r) || (o.target === 'descendant' && DESCENDANT.has(r)) || o.labelledBy || o.label ||
        (t === 'summary' && r !== 'presentation' && r !== 'none')) {
      visited.add(e);
      const text = inner(e, child);
      if (o.target === 'self' ? text.trim() : text) return text;
    }
    visited.add(e);
    if (r !== 'presentation' && r !== 'none' || t === 'iframe') {
      const title = e.getAttribute('title') || '';
      if (title.trim()) return title;
    }
    return '';
  }
  const flat = s => s.split(' ').map(c => c.replace(/\r\n/g, '\n').replace(/[​­]/g, '')
    .replace(/\s\s*/g, ' ')).join(' ').trim();
  const normal = s => s.replace(/[​­]/g, '').trim().replace(/\s+/g, ' ');
  const want = normal(name), out = [...new Set(rendered)];
  const walk = root => {
    for (const e of root.querySelectorAll('*')) {
      if (roleOf(e) === role && !out.includes(e) &&
          normal(flat(alternative(e, {visited: new Set(), target: 'self'}))) === want) out.push(e);
      if (e.shadowRoot) walk(e.shadowRoot);
    }
  };
  walk(document);
  return out;
}"""
# Bounds Chromium matches carried into the hidden-inclusive union; more than
# one match already fails uniqueness.
MAX_RENDERED_MATCHES = 32


class InvalidSelector(Invalid):
    pass


# Uncaught page exceptions are author-controlled text. Bound the count, the
# number of distinct messages and each message, and replace every control,
# format, private-use, surrogate or unassigned code point, so the receipt
# carries inert data that cannot restructure the caller's text or its JSON.
MAX_PAGE_ERRORS = 1000
MAX_PAGE_ERROR_MESSAGES = 3
MAX_PAGE_ERROR_CHARS = 200
# Browser automation injects its own URL-less scripts into every document.
# Playwright's service-worker block (kept below) reads navigator.serviceWorker,
# which throws in every opaque-origin preview frame. An exception whose whole
# stack lies in URL-less anonymous code is therefore not attributed to the
# page. Page code runs from its document or script URL; the CSP forbids string
# evaluation. An empty stack (a top-level page exception) is attributed.
INJECTED_FRAME = re.compile(
    r"at (?:async )?(?:<anonymous>|[^()]* \(<anonymous>(?::\d+:\d+)?\))(?::\d+:\d+)?"
)


def injected_only(stack):
    frames = [
        line.strip()
        for line in str(stack or "")[:16384].splitlines()[1:]
        if line.strip().startswith("at ")
    ]
    return bool(frames) and all(INJECTED_FRAME.fullmatch(frame) for frame in frames)


def page_error_text(value):
    try:
        text = str(value)[: 16 * MAX_PAGE_ERROR_CHARS]
    except Exception:
        text = ""
    text = " ".join(
        "".join(
            " "
            if unicodedata.category(c).startswith("C")
            or unicodedata.category(c) in ("Zl", "Zp")
            else c
            for c in text
        ).split()
    )
    if len(text) > MAX_PAGE_ERROR_CHARS:
        text = text[: MAX_PAGE_ERROR_CHARS - 1].rstrip() + "…"
    return text or "(no message)"


class PageErrors:
    """Uncaught exceptions of the inspected page; never evidence of success."""

    def __init__(self):
        self.count = 0
        self.messages = []

    def record(self, error):
        # Playwright delivers this from its event loop. Never let author data
        # raise here: a failed record still counts as an uncaught exception.
        try:
            if injected_only(getattr(error, "stack", "")):
                return
        except Exception:
            pass
        self.count = min(self.count + 1, MAX_PAGE_ERRORS)
        if len(self.messages) >= MAX_PAGE_ERROR_MESSAGES:
            return
        try:
            name = str(getattr(error, "name", "") or "")
            message = str(getattr(error, "message", "") or "")
            raw = (
                f"{name}: {message}"
                if name and message and not message.startswith(name + ":")
                else message or name
            )
        except Exception:
            raw = ""
        text = page_error_text(raw)
        if text not in self.messages:
            self.messages.append(text)

    def receipt(self):
        # Absent means none was observed; older capsules also omit it.
        if not self.count:
            return {}
        return {"pageErrors": {"count": self.count, "messages": list(self.messages)}}


# Rendered palette: the painted colors of the page as first loaded at a fixed
# desktop viewport, by share of that viewport's area. It is captured in its own
# disposable context of the same browser, through the same loopback server and
# request guard, so it neither observes nor changes the inspected steps. The
# device scale renders 320x180 device pixels (one per 4x4 CSS pixels): large
# painted regions keep their exact color, while small text and edges blend.
PALETTE_VIEWPORT = {"width": 1280, "height": 720}
PALETTE_DEVICE_SCALE = 0.25
PALETTE_SETTLE_MS = 100
PALETTE_TIMEOUT_MS = 3000
# The broker allows the whole capsule 45 seconds. A capture (at most two
# timeouts) starts only while it cannot push a slow run past that deadline.
PALETTE_START_BUDGET_S = 30
MAX_PALETTE_COLORS = 6
MAX_PALETTE_PIXELS = 1920 * 1920
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Hue families (HSL degrees, upper bound exclusive). Achromatic colors are
# named by lightness instead; see palette_bucket.
HUE_FAMILIES = (
    (12, "red"),
    (36, "orange"),
    (50, "amber"),
    (68, "yellow"),
    (165, "green"),
    (195, "teal"),
    (255, "blue"),
    (290, "purple"),
    (345, "pink"),
    (360, "red"),
)
GRAY_LEVELS = ((0.13, "black"), (0.40, "dark gray"), (0.72, "gray"), (0.94, "light gray"))
PALETTE_NAMES = (
    "white", "light gray", "gray", "dark gray", "black", "red", "orange",
    "amber", "yellow", "green", "teal", "blue", "purple", "pink", "brown",
)


def png_colors(data):
    """Count the exact RGB colors of a non-interlaced 8-bit RGB/RGBA PNG.

    Standard library only; alpha is ignored (browser screenshots are opaque).
    """
    if not isinstance(data, (bytes, bytearray)) or data[:8] != PNG_SIGNATURE:
        raise Invalid("invalid png")
    position, header, compressed = 8, None, []
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        body = data[position + 8 : position + 8 + length]
        if len(body) != length:
            raise Invalid("invalid png")
        position += 12 + length
        if kind == b"IHDR":
            if length != 13:
                raise Invalid("invalid png")
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            compressed.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise Invalid("invalid png")
    width, height, depth, color_type, _, _, interlace = header
    if (
        depth != 8
        or color_type not in (2, 6)
        or interlace
        or not 0 < width * height <= MAX_PALETTE_PIXELS
    ):
        raise Invalid("unsupported png")
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    expected = (stride + 1) * height
    try:
        raw = zlib.decompressobj().decompress(b"".join(compressed), expected)
    except zlib.error:
        raise Invalid("invalid png") from None
    if len(raw) != expected:
        raise Invalid("invalid png")
    counts = collections.Counter()
    previous = bytearray(stride)
    add = lambda a, b: (a + b) & 255
    for row in range(height):
        start = row * (stride + 1)
        kind = raw[start]
        line = bytearray(raw[start + 1 : start + 1 + stride])
        if kind == 1:
            for channel in range(channels):
                line[channel::channels] = bytes(
                    itertools.accumulate(line[channel::channels], add)
                )
        elif kind == 2:
            line = bytearray(map(add, line, previous))
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + predictor) & 255
        elif kind != 0:
            raise Invalid("invalid png")
        counts.update(zip(line[0::channels], line[1::channels], line[2::channels]))
        previous = line
    return counts


def palette_bucket(r, g, b):
    """A fixed perceptual bucket (HSL family and lightness band) for one color."""
    high, low = max(r, g, b), min(r, g, b)
    delta = high - low
    lightness = (high + low) / 510
    # Chroma under 10% reads as neutral (tinted whites, grays and near-blacks).
    if delta < 26:
        name = next((n for limit, n in GRAY_LEVELS if lightness < limit), "white")
        return name, 0
    if high == r:
        hue = (60 * (g - b) / delta) % 360
    elif high == g:
        hue = 60 * (b - r) / delta + 120
    else:
        hue = 60 * (r - g) / delta + 240
    saturation = delta / (high + low) if lightness <= 0.5 else delta / (510 - high - low)
    name = next(n for limit, n in HUE_FAMILIES if hue < limit)
    if name == "red" and lightness >= 0.75:
        name = "pink"
    elif 12 <= hue < 50 and (lightness < 0.35 or (saturation < 0.5 and lightness < 0.7)):
        # Dark or muted orange and amber hues read as brown.
        name = "brown"
    return name, 0 if lightness < 0.35 else 1 if lightness < 0.65 else 2


def rendered_palette(counts, limit=MAX_PALETTE_COLORS):
    """Top painted buckets by area: name, most frequent exact hex, percent."""
    total = sum(counts.values())
    if total <= 0:
        raise Invalid("empty capture")
    buckets = {}
    for color, count in counts.items():
        entry = buckets.setdefault(palette_bucket(*color), [0, color, 0])
        entry[0] += count
        if count > entry[2] or (count == entry[2] and color < entry[1]):
            entry[1], entry[2] = color, count
    colors = []
    for (name, _band), (count, color, _) in sorted(
        buckets.items(), key=lambda item: (-item[1][0], item[0])
    )[:limit]:
        percent = (200 * count + total) // (2 * total)
        if percent < 1:
            break
        colors.append({"name": name, "hex": "#%02x%02x%02x" % color, "percent": percent})
    return colors


def wrapper_document(prefix):
    # Fixed and script-free: page script exceptions therefore come only from
    # the sandboxed preview frame or a frame the preview itself created.
    return (
        f"<!doctype html><style>html,body{{margin:0;height:100%;overflow:hidden}}iframe{{border:0;width:100%;height:100%}}</style>"
        f'<iframe name="inspection" sandbox="{SANDBOX}" src="{prefix}"></iframe>'
    ).encode()


def guard_requests(context, page, origin, prefix, blocked):
    """Allow only GETs of the wrapper and this site's files from the loopback
    server, and at most the wrapper and site-entry navigations. Everything
    else, popups, downloads and websockets are recorded (bounded) and stopped."""
    navigation_count = 0

    def route_handler(route):
        nonlocal navigation_count
        req = route.request
        parsed = urllib.parse.urlsplit(req.url)
        safe = (
            req.method == "GET"
            and parsed.scheme == "http"
            and "http://" + parsed.netloc == origin
            and (
                parsed.path.startswith(prefix)
                or parsed.path == "/__ods_inspection__.html"
            )
        )
        if req.is_navigation_request():
            navigation_count += 1
            safe = (
                safe
                and navigation_count <= 2
                and req.url
                in (origin + "/__ods_inspection__.html", origin + prefix)
            )
        if safe:
            route.continue_()
        else:
            if len(blocked) < 32:
                blocked.append(
                    "navigation" if req.is_navigation_request() else "network"
                )
            route.abort()

    context.route("**/*", route_handler)
    context.on(
        "page",
        lambda popup: (
            blocked.append("popup") if len(blocked) < 32 else None,
            popup.close(),
        ),
    )
    page.on(
        "download",
        lambda download: (
            blocked.append("download") if len(blocked) < 32 else None,
            download.cancel(),
        ),
    )
    page.on(
        "websocket",
        lambda _: blocked.append("websocket") if len(blocked) < 32 else None,
    )


def capture_palette(browser, origin, prefix):
    """Rendered colors of a fresh load at the fixed desktop viewport, or None.

    Its own context: the step context and its page-error listener never see
    this load, and a request blocked here voids only the palette."""
    blocked = []
    context = browser.new_context(
        viewport=dict(PALETTE_VIEWPORT),
        device_scale_factor=PALETTE_DEVICE_SCALE,
        service_workers="block",
        accept_downloads=False,
    )
    try:
        page = context.new_page()
        page.set_default_timeout(PALETTE_TIMEOUT_MS)
        guard_requests(context, page, origin, prefix, blocked)
        page.goto(
            origin + "/__ods_inspection__.html",
            wait_until="load",
            timeout=PALETTE_TIMEOUT_MS,
        )
        frame = page.frame(name="inspection")
        if frame is None or frame.url != origin + prefix:
            return None
        page.wait_for_timeout(PALETTE_SETTLE_MS)
        image = page.screenshot(type="png", scale="device", timeout=PALETTE_TIMEOUT_MS)
        if blocked:
            return None
        return {"viewport": dict(PALETTE_VIEWPORT), "colors": rendered_palette(png_colors(image))}
    finally:
        context.close()


# Requested text: whether each text the owner asked for is visible when the
# page loads, anywhere on the page (not only the first viewport). A fresh load
# at the fixed desktop viewport, in its own context, like the palette. One
# measurement right after load decides it when every located text is visible.
# Otherwise the page is scrolled through once so scroll-triggered reveals get
# their chance, and a text counts as visible if any later sample shows it.
TEXT_VIEWPORT = {"width": 1280, "height": 720}
TEXT_SETTLE_MS = 100
TEXT_TIMEOUT_MS = 3000
TEXT_SCROLL_STEPS = 12
TEXT_SCROLL_WAIT_MS = 150
TEXT_FINAL_WAIT_MS = 400
TEXT_SCROLL_BUDGET_S = 3
TEXT_START_BUDGET_S = 30
REQUESTED_TEXT_STATUSES = ("visible", "hidden", "absent", "unmeasured")
REQUESTED_TEXT_REASONS = (
    "display-none", "visibility-hidden", "content-hidden", "transparent", "zero-size",
    "clipped", "off-page", "same-color", "transparent-text",
)

# Runs in the isolated world. Each text is folded like the plugin's
# canonicalText (NFKC, quotes, dashes, whitespace) and matched caselessly
# against rendered DOM text: text nodes, joined and space-separated at element
# boundaries, plus button input values. Script, style, form-option, media and
# embedded-frame content is never searched. A text found nowhere is "absent";
# past the traversal bounds it is "unmeasured". Neither is a visibility claim.
# Each deepest element containing the text is judged in turn; the first
# visible one decides, otherwise the first one found is reported with the
# first reason that applies. Element names are tag, id or up to two classes,
# restricted to [A-Za-z0-9_-]: page data, never instructions.
REQUESTED_TEXT = r"""function(texts) {
  const MAX_ELEMENTS = 20000, MAX_CHARS = 4000000, MAX_DEPTH = 256, MAX_OCCURRENCES = 8, MAX_OVERLAP_SCAN = 5000;
  const SKIP = new Set(('script style template noscript title head select datalist option optgroup textarea ' +
    'canvas object embed iframe video audio desc metadata').split(' '));
  const CLIPS = /^(?:hidden|clip)$/, SCROLLS = /^(?:auto|scroll)$/, HIDDEN = /^(?:hidden|collapse)$/;
  const HTML = 'http://www.w3.org/1999/xhtml';
  const fold = s => s.normalize('NFKC').replace(/[​-‍⁠﻿­]/g, '')
    .replace(/[‘’‚‛′]/g, "'").replace(/[“-‟″«»]/g, '"')
    .replace(/[‐-―−]/g, '-').replace(/\s+/g, ' ').trim().toLowerCase();
  const wanted = texts.map(fold), found = wanted.map(() => []);
  let elements = 0, chars = 0, over = false;
  const visit = (e, depth) => {
    if (++elements > MAX_ELEMENTS || depth > MAX_DEPTH) { over = true; return ['', '', 0]; }
    let joined = '', spaced = '', below = 0;
    if (e.localName === 'input' && /^(?:button|submit|reset)$/i.test(e.type)) joined = spaced = e.value || '';
    for (let n = e.firstChild; n && !over; n = n.nextSibling) {
      if (n.nodeType === 3) { joined += n.data; spaced += n.data; }
      else if (n.nodeType === 1 && !SKIP.has(n.localName)) {
        const [j, s, m] = visit(n, depth + 1);
        joined += j; spaced += ' ' + s + ' '; below |= m;
      }
    }
    chars += joined.length + spaced.length;
    if (over || chars > MAX_CHARS) { over = true; return ['', '', 0]; }
    const a = fold(joined), b = fold(spaced);
    let mask = 0;
    wanted.forEach((w, i) => {
      if (!w || (!a.includes(w) && !b.includes(w))) return;
      mask |= 1 << i;
      if (!(below & (1 << i)) && found[i].length < MAX_OCCURRENCES) found[i].push(e);
    });
    return [joined, spaced, mask];
  };
  if (document.body) visit(document.body, 0);

  const root = document.documentElement, body = document.body;
  const style = (el, pseudo) => getComputedStyle(el, pseudo);
  const up = el => el.parentElement || (el.parentNode && el.parentNode.host) || null;
  const lineage = el => { const out = []; for (let x = el; x && out.length < 1024; x = up(x)) out.push(x); return out; };
  const describe = el => {
    const name = String(el.localName || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 32);
    const tag = /^[a-z]/.test(name) ? name : 'element', id = el.getAttribute('id') || '';
    if (/^[A-Za-z_-][A-Za-z0-9_-]{0,63}$/.test(id)) return tag + '#' + id;
    return tag + [...el.classList].filter(c => /^[A-Za-z_-][A-Za-z0-9_-]{0,63}$/.test(c)).slice(0, 2).map(c => '.' + c).join('');
  };
  const rgba = value => {
    const m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+)(%?))?\s*\)$/.exec(value || '');
    if (!m) return null;
    const a = m[4] === undefined ? 1 : parseFloat(m[4]) / (m[5] ? 100 : 1);
    return {r: +m[1], g: +m[2], b: +m[3], a};
  };
  const blend = (top, under) => ({r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a), a: 1});
  const hex = c => '#' + [c.r, c.g, c.b].map(v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('');
  const luminance = c => [c.r, c.g, c.b].map(v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  const contrast = (x, y) => { const a = luminance(x), b = luminance(y); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
  const paintsPseudo = el => ['::before', '::after'].some(p => {
    const s = style(el, p);
    return s.content !== 'none' && s.content !== 'normal' && (s.backgroundImage !== 'none' || (rgba(s.backgroundColor) || {a: 1}).a > 0);
  });
  const rootStyle = style(root), propagated = rootStyle.overflowX === 'visible' && rootStyle.overflowY === 'visible';
  const viewportStyle = propagated && body ? style(body) : rootStyle;
  const scroller = document.scrollingElement || root;

  // Anything other than its own ancestors that paints where the text is makes
  // its backdrop unknown: an image, a background, or a decorated pseudo-element.
  const overlapped = (chain, rects) => {
    const all = body ? body.getElementsByTagName('*') : [];
    if (all.length > MAX_OVERLAP_SCAN) return true;
    const own = new Set(chain);
    const [x0, y0, x1, y1] = rects.reduce((u, r) => [Math.min(u[0], r[0]), Math.min(u[1], r[1]),
      Math.max(u[2], r[2]), Math.max(u[3], r[3])], [Infinity, Infinity, -Infinity, -Infinity]);
    for (const x of all) {
      if (own.has(x)) continue;
      const box = x.getBoundingClientRect();
      if (box.width < 1 || box.height < 1 || box.right <= x0 || box.left >= x1 || box.bottom <= y0 || box.top >= y1) continue;
      const s = style(x);
      if ((/^(?:img|svg|video|canvas|picture|iframe|object|embed|input)$/.test(x.localName) || s.backgroundImage !== 'none' ||
          (rgba(s.backgroundColor) || {a: 1}).a > 0 || paintsPseudo(x)) &&
          x.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return true;
    }
    return false;
  };

  // null when the text in e is visible; otherwise the first reason that applies.
  const verdict = e => {
    const chain = lineage(e);
    if (style(e).display !== 'contents' && !e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) {
      const none = chain.find(x => style(x).display === 'none');
      if (none) return {reason: 'display-none', culprit: none};
      if (HIDDEN.test(style(e).visibility)) {
        let origin = e;
        for (const x of chain.slice(1)) { if (HIDDEN.test(style(x).visibility)) origin = x; else break; }
        return {reason: 'visibility-hidden', culprit: origin};
      }
      const clear = chain.find(x => parseFloat(style(x).opacity) === 0);
      if (clear) return {reason: 'transparent', culprit: clear};
      return {reason: 'content-hidden', culprit: chain.find(x => style(x).contentVisibility === 'hidden' ||
        (x !== e && x.localName === 'details' && !x.open)) || e};
    }
    let opacity = 1, faint = e, faintest = 2;
    for (const x of chain) {
      const o = parseFloat(style(x).opacity);
      if (!(o >= 0)) continue;
      opacity *= o;
      if (o < faintest) { faintest = o; faint = x; }
    }
    if (opacity <= 0.05) return {reason: 'transparent', culprit: faint};
    let rects;
    if (e.localName === 'input') rects = [...e.getClientRects()];
    else { const range = document.createRange(); range.selectNodeContents(e); rects = [...range.getClientRects()]; }
    rects = rects.filter(r => r.width >= 1 && r.height >= 1).map(r => [r.left, r.top, r.right, r.bottom]);
    if (!rects.length) return {reason: 'zero-size', culprit: e};
    for (const x of chain) {
      if (x === root || (x === body && propagated)) continue;
      const s = style(x), box = x.getBoundingClientRect();
      let l = -Infinity, t = -Infinity, r = Infinity, b = Infinity;
      if (x.namespaceURI === HTML && !/^(?:inline|contents)$/.test(s.display)) {
        if (CLIPS.test(s.overflowX)) { l = box.left + x.clientLeft; r = l + x.clientWidth; }
        if (CLIPS.test(s.overflowY)) { t = box.top + x.clientTop; b = t + x.clientHeight; }
      }
      if (/^(?:absolute|fixed)$/.test(s.position) && /^rect\(/.test(s.clip)) {
        const v = s.clip.slice(5, -1).split(/[\s,]+/).filter(Boolean);
        if (v.length === 4) {
          const at = (k, base, auto) => v[k] === 'auto' ? auto : base + parseFloat(v[k]);
          l = Math.max(l, at(3, box.left, box.left)); t = Math.max(t, at(0, box.top, box.top));
          r = Math.min(r, at(1, box.left, box.right)); b = Math.min(b, at(2, box.top, box.bottom));
        }
      }
      const inset = /^inset\(([^)]*)\)/.exec(s.clipPath || '');
      const sides = inset ? inset[1].split(/\s+round\s+/)[0].trim().split(/\s+/) : [];
      if (sides.length >= 1 && sides.length <= 4 && sides.every(p => /^-?[\d.]+(?:px|%)?$/.test(p))) {
        const [T, R = T, B = T, L = R] = sides;
        const size = (p, whole) => p.endsWith('%') ? parseFloat(p) / 100 * whole : parseFloat(p);
        l = Math.max(l, box.left + size(L, box.width)); r = Math.min(r, box.right - size(R, box.width));
        t = Math.max(t, box.top + size(T, box.height)); b = Math.min(b, box.bottom - size(B, box.height));
      }
      if (l !== -Infinity || t !== -Infinity || r !== Infinity || b !== Infinity) {
        rects = rects.map(([x0, y0, x1, y1]) => [Math.max(x0, l), Math.max(y0, t), Math.min(x1, r), Math.min(y1, b)])
          .filter(([x0, y0, x1, y1]) => x1 - x0 >= 2 && y1 - y0 >= 2);
        if (!rects.length) return {reason: 'clipped', culprit: x};
      }
      // Scrolling a container brings its content into the container's box.
      if (x.namespaceURI === HTML && (SCROLLS.test(s.overflowX) || SCROLLS.test(s.overflowY)) &&
          x.clientWidth >= 2 && x.clientHeight >= 2) {
        const left = box.left + x.clientLeft, top = box.top + x.clientTop;
        rects = [[left, top, left + x.clientWidth, top + x.clientHeight]];
      }
    }
    // Text inside its own scroll container is reachable by scrolling it.
    if (!chain.some(x => x !== root && x !== body && (SCROLLS.test(style(x).overflowX) || SCROLLS.test(style(x).overflowY)))) {
      const fixed = chain.some(x => style(x).position === 'fixed');
      const width = CLIPS.test(viewportStyle.overflowX) ? innerWidth : Math.max(scroller.scrollWidth, innerWidth);
      const height = CLIPS.test(viewportStyle.overflowY) ? innerHeight : Math.max(scroller.scrollHeight, innerHeight);
      const [dx, dy, w, h] = fixed ? [0, 0, innerWidth, innerHeight] : [scrollX, scrollY, width, height];
      if (!rects.some(([x0, y0, x1, y1]) => x1 + dx > 1 && y1 + dy > 1 && x0 + dx < w - 1 && y0 + dy < h - 1))
        return {reason: 'off-page', culprit: chain.find(x => style(x).transform !== 'none') ||
          chain.find(x => style(x).position !== 'static') || e};
    }
    const own = style(e), fill = rgba(own.webkitTextFillColor) || rgba(own.color);
    const stroked = parseFloat(own.webkitTextStrokeWidth) > 0 && (rgba(own.webkitTextStrokeColor) || {a: 1}).a > 0;
    const shadowed = own.textShadow !== 'none';
    if (!fill) return null;
    if (fill.a <= 0.05) {
      const painted = chain.slice(0, 8).some(x => { const s = style(x);
        return (s.backgroundClip === 'text' || s.webkitBackgroundClip === 'text') && s.backgroundImage !== 'none'; });
      return painted || stroked || shadowed ? null : {reason: 'transparent-text', culprit: e};
    }
    if (fill.a < 0.5 || stroked || shadowed) return null;
    // Same color: only a plain backdrop of solid ancestor backgrounds is judged.
    const layers = [];
    for (const x of chain) {
      const s = style(x);
      if (s.backgroundImage !== 'none' || s.filter !== 'none' || s.mixBlendMode !== 'normal' ||
          (s.backdropFilter || 'none') !== 'none' || paintsPseudo(x)) return null;
      const bg = rgba(s.backgroundColor);
      if (!bg) return null;
      if (bg.a > 0) { layers.push([bg, x]); if (bg.a >= 0.99) break; }
    }
    // With no opaque background the page shows the default white canvas,
    // unless it asks for a dark color scheme.
    if (!layers.some(([bg]) => bg.a >= 0.99) && (/dark/.test(rootStyle.colorScheme || '') ||
        [...document.querySelectorAll('meta[name="color-scheme" i]')].some(m => /dark/i.test(m.content || '')))) return null;
    let backdrop = {r: 255, g: 255, b: 255, a: 1};
    for (const [bg] of layers.slice().reverse()) backdrop = blend(bg, backdrop);
    const ink = blend(fill, backdrop);
    if (contrast(ink, backdrop) >= 1.05 || overlapped(chain, rects)) return null;
    return {reason: 'same-color', culprit: layers.length ? layers[0][1] : root, colors: [hex(ink), hex(backdrop)]};
  };

  const results = wanted.map((_, i) => {
    if (!found[i].length) return {status: over ? 'unmeasured' : 'absent'};
    let first = null;
    for (const e of found[i]) {
      const v = verdict(e);
      if (!v) return {status: 'visible'};
      first = first || {status: 'hidden', element: describe(e), reason: v.reason,
        ...(v.culprit && v.culprit !== e ? {culprit: describe(v.culprit)} : {}), ...(v.colors ? {colors: v.colors} : {})};
    }
    return first;
  });
  return {results, viewport: innerHeight,
    maxScroll: CLIPS.test(viewportStyle.overflowY) ? 0 : Math.max(0, Math.floor(scroller.scrollHeight - innerHeight))};
}"""

# Instant, so a page's smooth scroll-behavior cannot stretch the pass.
SCROLL_TO = r"""function(top) { window.scrollTo({top, left: 0, behavior: 'instant'}); return scrollY; }"""


ELEMENT_NAME = re.compile(
    r"[a-z][a-z0-9-]{0,31}(?:#[A-Za-z_-][A-Za-z0-9_-]{0,63}|(?:\.[A-Za-z_-][A-Za-z0-9_-]{0,63}){0,2})"
)


def requested_text_entry(text, observed):
    """One receipt entry from an isolated-world observation, shape-checked so
    a malformed observation voids only this evidence, never the receipt."""
    status = observed.get("status") if isinstance(observed, dict) else None
    if status not in REQUESTED_TEXT_STATUSES:
        raise Invalid("invalid text observation")
    if status != "hidden":
        return {"text": text, "status": status}
    entry = {"text": text, "status": status, "element": observed.get("element"), "reason": observed.get("reason")}
    for key in ("culprit", "colors"):
        if key in observed:
            entry[key] = observed[key]
    names = [entry["element"], *([entry["culprit"]] if "culprit" in entry else [])]
    colors = entry.get("colors")
    if (
        entry["reason"] not in REQUESTED_TEXT_REASONS
        or not all(isinstance(name, str) and ELEMENT_NAME.fullmatch(name) for name in names)
        or (entry["reason"] == "same-color") != (colors is not None)
        or (
            colors is not None
            and not (
                isinstance(colors, list)
                and len(colors) == 2
                and all(isinstance(c, str) and re.fullmatch("#[0-9a-f]{6}", c) for c in colors)
            )
        )
    ):
        raise Invalid("invalid text observation")
    return entry


def check_requested_text(browser, origin, prefix, texts):
    """Requested-text visibility of a fresh desktop load across the whole
    page, or None. Its own context: the steps and their page-error listener
    never see this load, and a request blocked here voids only this check."""
    blocked = []
    context = browser.new_context(
        viewport=dict(TEXT_VIEWPORT), service_workers="block", accept_downloads=False
    )
    try:
        page = context.new_page()
        page.set_default_timeout(TEXT_TIMEOUT_MS)
        guard_requests(context, page, origin, prefix, blocked)
        page.goto(
            origin + "/__ods_inspection__.html", wait_until="load", timeout=TEXT_TIMEOUT_MS
        )
        frame = page.frame(name="inspection")
        if frame is None or frame.url != origin + prefix:
            return None
        cdp = context.new_cdp_session(page)
        tree = cdp.send("Page.getFrameTree")["frameTree"]
        frame_id = next(
            child["frame"]["id"]
            for child in tree.get("childFrames", [])
            if child["frame"].get("name") == "inspection"
        )
        world = cdp.send(
            "Page.createIsolatedWorld",
            {"frameId": frame_id, "worldName": "ods-requested-text", "grantUniveralAccess": False},
        )["executionContextId"]

        def call(function, argument):
            if blocked:
                raise Invalid("preview navigation or request blocked")
            result = cdp.send(
                "Runtime.callFunctionOn",
                {
                    "executionContextId": world,
                    "functionDeclaration": function,
                    "arguments": [{"value": argument}],
                    "returnByValue": True,
                },
            )
            if result.get("exceptionDetails"):
                raise Invalid("requested text check failed")
            return result["result"].get("value")

        page.wait_for_timeout(TEXT_SETTLE_MS)
        first = call(REQUESTED_TEXT, list(texts))
        outcome = [requested_text_entry(t, o) for t, o in zip(texts, first["results"])]
        if len(outcome) != len(texts):
            raise Invalid("invalid text observation")
        pending = [i for i, entry in enumerate(outcome) if entry["status"] == "hidden"]

        def sample():
            observed = call(REQUESTED_TEXT, [texts[i] for i in pending])["results"]
            for i, value in zip(list(pending), observed):
                entry = requested_text_entry(texts[i], value)
                # A later "absent" never erases a text already seen hidden.
                if entry["status"] in ("visible", "hidden"):
                    outcome[i] = entry
            pending[:] = [i for i in pending if outcome[i]["status"] == "hidden"]

        scrolled = bool(pending)
        if pending:
            maximum, height = int(first["maxScroll"]), int(first["viewport"])
            step = max(height * 0.8, maximum / TEXT_SCROLL_STEPS, 1)
            tops, top = [], 0
            while top < maximum and len(tops) < TEXT_SCROLL_STEPS:
                top = min(maximum, top + step)
                tops.append(round(top))
            deadline, moved = time.monotonic() + TEXT_SCROLL_BUDGET_S, False
            for top in tops:
                if not pending or time.monotonic() > deadline:
                    break
                call(SCROLL_TO, top)
                moved = True
                page.wait_for_timeout(TEXT_SCROLL_WAIT_MS)
                sample()
            # Finite load and reveal transitions get time to finish.
            if pending:
                page.wait_for_timeout(TEXT_FINAL_WAIT_MS)
                sample()
            if pending and moved:
                call(SCROLL_TO, 0)
                page.wait_for_timeout(TEXT_SCROLL_WAIT_MS)
                sample()
        if blocked:
            return None
        return {"viewport": dict(TEXT_VIEWPORT), "scrolled": scrolled, "texts": outcome}
    finally:
        context.close()


def observe_until_stable(once, wait):
    # Keep the 100ms fast path. A finite transition may need more samples,
    # but changing observations never become a passing assertion on timeout.
    # The broker's independent 45s capsule deadline still bounds all steps.
    deadline = time.monotonic() + 1.5
    previous = once()
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 0.1:
            return previous, False
        wait(100)
        current = once()
        if time.monotonic() > deadline:
            return current, False
        if current == previous:
            return current, True
        previous = current


def run_browser(bundle, playwright_factory=None, checkpoint=None):
    started = time.monotonic()
    checkpoint = checkpoint or (lambda _result: None)
    request, files = validate_bundle(bundle)
    prefix = "/" + request["siteId"] + "/"
    blocked = []
    page_errors = PageErrors()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/__ods_inspection__.html":
                body = wrapper_document(prefix)
                mime = "text/html"
            elif path.startswith(prefix):
                name = urllib.parse.unquote(path[len(prefix) :]) or "index.html"
                if name not in files:
                    self.send_error(404)
                    return
                body = files[name]
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            else:
                self.send_error(404)
                return
            if path.endswith((".md", ".markdown")):
                mime = "text/plain"
            if mime.startswith("text/") or mime == "application/javascript":
                try:
                    body.decode("utf-8")
                    mime += "; charset=utf-8"
                except UnicodeDecodeError:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
            )
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright

        playwright_factory = sync_playwright
    try:
        with playwright_factory() as p:
            # The outer Docker capsule is the mandatory sandbox; this code is
            # never offered as an in-process host browser fallback.
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                viewport=request["viewport"],
                service_workers="block",
                accept_downloads=False,
            )
            page = context.new_page()
            page.set_default_timeout(2000)
            guard_requests(context, page, origin, prefix, blocked)
            # Registered before navigation so startup exceptions are included.
            # Page-scoped (not context-wide): blocked popups are never recorded.
            page.on("pageerror", page_errors.record)
            page.goto(
                origin + "/__ods_inspection__.html", wait_until="load", timeout=8000
            )
            frame = page.frame(name="inspection")
            if frame is None or frame.url != origin + prefix:
                raise Invalid("preview frame unavailable")
            # Isolated world prevents author JS overriding querySelector,
            # getComputedStyle, checkVisibility, or receipt construction.
            cdp = context.new_cdp_session(page)
            tree = cdp.send("Page.getFrameTree")["frameTree"]
            frame_id = next(
                child["frame"]["id"]
                for child in tree.get("childFrames", [])
                if child["frame"].get("name") == "inspection"
            )
            world = cdp.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "ods-inspection",
                    "grantUniveralAccess": False,
                },
            )["executionContextId"]

            def evaluate(function, arguments=None):
                if blocked:
                    raise Invalid("preview navigation or request blocked")
                result = cdp.send(
                    "Runtime.callFunctionOn",
                    {
                        "executionContextId": world,
                        "functionDeclaration": function,
                        "arguments": [{"value": arg} for arg in (arguments or [])],
                        "returnByValue": True,
                    },
                )
                if result.get("exceptionDetails"):
                    raise Invalid("invalid selector")
                return result["result"].get("value")

            # Sample in the isolated world; disagreement gets a bounded chance
            # to settle without changing page styles or animation state.
            document_id = cdp.send(
                "Runtime.evaluate", {"expression": "document", "contextId": world}
            )["result"]["objectId"]

            def including_hidden(locator, nodes, owned):
                # Carry Chromium's rendered matches into the isolated world and
                # add hidden-inclusive exact role/name matches by identity.
                unresolved = max(0, len(nodes) - MAX_RENDERED_MATCHES)
                rendered = []
                for n in nodes[:MAX_RENDERED_MATCHES]:
                    try:
                        rendered.append(cdp.send(
                            "DOM.resolveNode",
                            {"backendNodeId": n["backendDOMNodeId"], "executionContextId": world},
                        )["object"]["objectId"])
                    except Exception:
                        # Not in this frame's world (e.g. a nested frame):
                        # still a match for uniqueness, never an observation.
                        unresolved += 1
                owned.extend(rendered)
                result = cdp.send(
                    "Runtime.callFunctionOn",
                    {
                        "executionContextId": world,
                        "functionDeclaration": ROLE_NAME_INCLUDING_HIDDEN,
                        "arguments": [{"value": locator["role"]}, {"value": locator["name"]},
                                      *({"objectId": h} for h in rendered)],
                        "returnByValue": False,
                    },
                )
                if result.get("exceptionDetails"):
                    raise Invalid("inspection failed")
                matches = result["result"]["objectId"]
                owned.append(matches)
                count = unresolved + cdp.send(
                    "Runtime.callFunctionOn",
                    {"objectId": matches, "functionDeclaration": "function(){return this.length}",
                     "returnByValue": True},
                )["result"]["value"]
                if count != 1:
                    return count, None
                if unresolved:
                    raise Invalid("preview element unavailable")
                node = cdp.send(
                    "Runtime.callFunctionOn",
                    {"objectId": matches, "functionDeclaration": "function(){return this[0]}",
                     "returnByValue": False},
                )["result"]["objectId"]
                return 1, node

            def once(locator, include_hidden=False):
                owned = []
                try:
                    return measure(locator, include_hidden, owned)
                finally:
                    for object_id in dict.fromkeys(owned):
                        try:
                            cdp.send("Runtime.releaseObject", {"objectId": object_id})
                        except Exception:
                            pass

            def measure(locator, include_hidden, owned):
                if blocked:
                    raise Invalid("preview navigation or request blocked")
                if "selector" in locator:
                    selection = evaluate(
                        SELECTOR_COUNT,
                        [locator["selector"]],
                    )
                    if selection == {"invalidSelector": True}:
                        raise InvalidSelector("invalid CSS syntax")
                    count = selection["count"]
                    if count != 1:
                        return {"count": count}
                    node = cdp.send(
                        "Runtime.callFunctionOn",
                        {
                            "executionContextId": world,
                            "functionDeclaration": "function(s){return document.querySelector(s)}",
                            "arguments": [{"value": locator["selector"]}],
                            "returnByValue": False,
                        },
                    )["result"]["objectId"]
                else:
                    nodes = cdp.send(
                        "Accessibility.queryAXTree",
                        {
                            "objectId": document_id,
                            "accessibleName": locator["name"],
                            "role": locator["role"],
                        },
                    )["nodes"]
                    nodes = [
                        n
                        for n in nodes
                        if not n.get("ignored")
                        and n.get("role", {}).get("value") == locator["role"]
                        and n.get("name", {}).get("value") == locator["name"]
                        and n.get("backendDOMNodeId")
                    ]
                    if include_hidden:
                        count, node = including_hidden(locator, nodes, owned)
                        if count != 1:
                            return {"count": count}
                    elif len(nodes) != 1:
                        return {"count": len(nodes)}
                    else:
                        node = cdp.send(
                            "DOM.resolveNode",
                            {
                                "backendNodeId": nodes[0]["backendDOMNodeId"],
                                "executionContextId": world,
                            },
                        )["object"]["objectId"]
                owned.append(node)
                result = cdp.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": node,
                        "functionDeclaration": OBSERVE_ELEMENT,
                        "returnByValue": True,
                    },
                )
                if result.get("exceptionDetails"):
                    raise Invalid("inspection failed")
                return result["result"]["value"]

            def observe(locator, include_hidden=False):
                return observe_until_stable(
                    lambda: once(locator, include_hidden), page.wait_for_timeout
                )

            page.wait_for_timeout(100)
            diagnostics = evaluate(DIAGNOSTIC)
            results = []
            for index, step in enumerate(request["steps"]):
                try:
                    # Only a hidden assertion may address a hidden element by
                    # role/name; assert-visible and click stay rendered-only.
                    before, stable = observe(
                        step["locator"], step["action"] == "assert-hidden"
                    )
                except InvalidSelector:
                    # No DOM observation exists for invalid syntax. Preserve
                    # prior evidence and the exact failing step, then stop.
                    results.append({"index": index, **step, "stable": False,
                                    "status": "failed", "errorCode": "invalid_selector"})
                    break
                item = {
                    "index": index,
                    **step,
                    "before": before,
                    "stable": stable,
                    "status": "failed",
                }
                if before.get("count") == 0:
                    item["errorCode"] = "no_match"
                elif before.get("count") != 1:
                    item["errorCode"] = "selector_not_unique"
                elif not stable:
                    item["errorCode"] = "unstable"
                elif step["action"] == "click":
                    try:
                        (
                            frame.locator("css=" + step["locator"]["selector"])
                            if "selector" in step["locator"]
                            else frame.get_by_role(
                                step["locator"]["role"],
                                name=step["locator"]["name"],
                                exact=True,
                            )
                        ).click(timeout=2000)
                        if blocked:
                            raise Invalid("preview navigation or request blocked")
                        after, after_stable = observe(step["locator"])
                        item.update(
                            after=after,
                            stable=after_stable,
                            status="passed" if after_stable else "failed",
                        )
                    except Exception:
                        item["errorCode"] = "click_failed"
                else:
                    expected = step["action"] == "assert-visible"
                    item["status"] = (
                        "passed" if before.get("visible") is expected else "failed"
                    )
                    if item["status"] == "failed":
                        item["errorCode"] = "visibility_mismatch"
                results.append(item)
                if item["status"] == "failed":
                    break
            # Context is never reused. A click-only receipt proves dispatch,
            # not the post-click condition; callers must assert that condition.
            # Page exceptions are separate evidence: they never change a step
            # or receipt status, and callers must not treat them as verified.
            result = {
                "schemaVersion": 1,
                "kind": KIND,
                "status": "passed"
                if len(results) == len(request["steps"])
                and all(v["status"] == "passed" for v in results)
                and not blocked
                else "failed",
                "siteId": request["siteId"],
                "sha256": request["sha256"],
                "planSha256": plan_hash(request),
                "viewport": request["viewport"],
                "steps": results,
                "diagnostics": diagnostics,
                "blockedRequests": blocked,
                **page_errors.receipt(),
                "scope": SCOPE,
            }
            context.close()
            checkpoint(result)
            # After the step context is closed, so its receipt is final. The
            # palette is separate evidence: it never changes a step or status,
            # and it is omitted (as by older capsules) when capture fails.
            palette = None
            if time.monotonic() - started < PALETTE_START_BUDGET_S:
                try:
                    palette = capture_palette(browser, origin, prefix)
                except Exception:
                    pass
            if palette:
                result["renderedColors"] = palette
                checkpoint(result)
            # Requested-text visibility is separate evidence too: it never
            # changes a step or the receipt status, and it is omitted when the
            # request names no text or the check fails.
            visibility = None
            if request.get("texts") and time.monotonic() - started < TEXT_START_BUDGET_S:
                try:
                    visibility = check_requested_text(browser, origin, prefix, request["texts"])
                except Exception:
                    pass
            if visibility:
                result["requestedText"] = visibility
            browser.close()
            return result
    finally:
        server.shutdown()
        server.server_close()


# Once the step receipt is final, the palette and requested-text loads can only
# add evidence. Page script can stall them (a scroll handler that never
# returns blocks every isolated-world call), so by this deadline the capsule
# writes the last final receipt without the unfinished evidence and exits,
# instead of losing the receipt to the broker's 45-second limit.
EVIDENCE_DEADLINE_S = 40
EVIDENCE_BUDGET_S = 12


class ReceiptOutput:
    """Writes exactly one receipt line: the finished result, or at the
    evidence deadline the latest final receipt that run_browser checkpointed."""

    def __init__(self, stream, started):
        self.stream, self.started = stream, started
        self.lock, self.timer, self.saved, self.written = threading.Lock(), None, None, False

    def emit(self, result, request):
        encoded = canonical(result)
        if len(encoded) > MAX_RESULT:
            encoded = canonical(failure("output_limit", request))
        self.stream.write(encoded + b"\n")
        self.stream.flush()
        self.written = True

    def checkpoint(self, result, request):
        with self.lock:
            self.saved = copy_json(result)
            if self.timer is None:
                now = time.monotonic()
                delay = min(self.started + EVIDENCE_DEADLINE_S, now + EVIDENCE_BUDGET_S) - now
                self.timer = threading.Timer(max(0.0, delay), self.expire, (request,))
                self.timer.daemon = True
                self.timer.start()

    def expire(self, request):
        with self.lock:
            if self.written:
                return
            self.emit(self.saved, request)
            # The main thread may be blocked inside the browser; the capsule
            # container ends with this process.
            os._exit(0)

    def write(self, result, request):
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
            if not self.written:
                self.emit(result, request)


def copy_json(value):
    return strict_json(canonical(value))


def main():
    request = None
    output = ReceiptOutput(sys.stdout.buffer, time.monotonic())
    try:
        raw = sys.stdin.buffer.read(MAX_BUNDLE + 1)
        if len(raw) > MAX_BUNDLE:
            raise Invalid("bundle too large")
        bundle = strict_json(raw)
        request, _ = validate_bundle(bundle)
        result = run_browser(
            bundle, checkpoint=lambda value: output.checkpoint(value, request)
        )
    except Exception:
        result = failure("unavailable", request)
    output.write(result, request)


if __name__ == "__main__":
    main()
