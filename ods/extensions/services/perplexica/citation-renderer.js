"use strict";

// This whole function is embedded in Vane's pinned client chunk at startup.
// Keep helpers inside it so Function#toString produces a self-contained body.
function renderCitations(message, sources) {
  if (typeof message !== "string") return message;
  const refs = Array.isArray(sources) ? sources : [];
  let fence = null;
  let inline = null;
  let rawTag = null;
  let rawElement = null;

  function cite(token, inner) {
    if (!/^\s*\d+(?:\s*,\s*\d+)*\s*$/.test(inner)) return token;
    // Vane removes bare [N] markers when it has no source blocks. Keep that
    // behavior in prose while preserving code and numeric list literals.
    if (refs.length === 0) return /^\[\d+\]$/.test(token) ? "" : token;

    const labels = inner.split(",").map((part) => part.trim());
    const urls = [];
    for (const label of labels) {
      const index = Number(label);
      if (!Number.isSafeInteger(index) || index < 1) return token;
      const url = refs[index - 1]?.metadata?.url;
      if (typeof url !== "string" || !/^https?:\/\/[^\s<>"']+$/i.test(url)) return token;
      urls.push(url);
    }
    return labels.map((label, i) => {
      const href = urls[i].replace(/&/g, "&amp;").replace(/"/g, "&quot;")
        .replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return `<citation href="${href}">${label}</citation>`;
    }).join("");
  }

  function closingBracket(line, start) {
    let depth = 0;
    for (let i = start; i < line.length; i += 1) {
      if (line[i] === "\\") { i += 1; continue; }
      if (line[i] === "[") depth += 1;
      if (line[i] === "]" && --depth === 0) return i;
    }
    return -1;
  }

  function closingParen(line, start) {
    let depth = 0;
    for (let i = start; i < line.length; i += 1) {
      if (line[i] === "\\") { i += 1; continue; }
      if (line[i] === "(") depth += 1;
      if (line[i] === ")" && --depth === 0) return i;
    }
    return -1;
  }

  function hasClosingTicks(from, marker) {
    let at = message.indexOf(marker, from);
    while (at !== -1) {
      if (message[at - 1] !== "`" && message[at + marker.length] !== "`") return true;
      at = message.indexOf(marker, at + marker.length);
    }
    return false;
  }

  function closingTicks(line, from, marker) {
    let at = line.indexOf(marker, from);
    while (at !== -1) {
      if (line[at - 1] !== "`" && line[at + marker.length] !== "`") return at;
      at = line.indexOf(marker, at + marker.length);
    }
    return -1;
  }

  function inlineCode(value) {
    const escaped = value.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    // Vane v1.12.2 renders Markdown codeInline nodes as literal backticks.
    // Raw HTML avoids that override. not-prose suppresses Typography's own
    // code::before/after backticks while retaining semantic code markup.
    return `<span class="not-prose"><code style="font-family:monospace;padding:0 .2em;border-radius:.2em;background-color:rgba(127,127,127,.14)">${escaped}</code></span>`;
  }

  function insideHtmlTag(line, index) {
    let open = line.indexOf("<");
    while (open !== -1 && open <= index) {
      if (/^<\/?[A-Za-z]/.test(line.slice(open))) {
        const end = htmlTagEnd(line, open, null).end;
        if (end === -1 || end >= index) return true;
        open = line.indexOf("<", end + 1);
      } else open = line.indexOf("<", open + 1);
    }
    return false;
  }

  function htmlTagEnd(line, from, initialQuote) {
    let quote = initialQuote;
    for (let i = from; i < line.length; i += 1) {
      if (quote) {
        if (line[i] === quote) quote = null;
      } else if (line[i] === '"' || line[i] === "'") {
        quote = line[i];
      } else if (line[i] === ">") {
        return { end: i, quote: null };
      }
    }
    return { end: -1, quote };
  }

  function prose(line, base) {
    let output = "";
    for (let i = 0; i < line.length;) {
      if (inline) {
        const close = closingTicks(line, i, inline);
        if (close === -1) return output + line.slice(i);
        output += line.slice(i, close + inline.length);
        i = close + inline.length;
        inline = null;
        continue;
      }
      if (line[i] === "`") {
        let end = i + 1;
        while (line[end] === "`") end += 1;
        const marker = line.slice(i, end);
        let escapes = 0;
        for (let k = i - 1; k >= 0 && line[k] === "\\"; k -= 1) escapes += 1;
        // Escaped or unmatched backticks are prose, so later [N] can cite.
        if (escapes % 2 === 0 && !insideHtmlTag(line, i)) {
          // Convert only complete same-line spans. Preserve multiline spans
          // verbatim so their Markdown line structure and citation guard stay.
          const close = closingTicks(line, end, marker);
          if (close !== -1) {
            output += inlineCode(line.slice(end, close));
            i = close + marker.length;
            continue;
          }
          if (hasClosingTicks(base + end, marker)) inline = marker;
        }
        output += marker;
        i = end;
        continue;
      }
      if (line[i] !== "[") { output += line[i]; i += 1; continue; }

      const end = closingBracket(line, i);
      if (end === -1) { output += line.slice(i); break; }
      const token = line.slice(i, end + 1);
      const inner = token.slice(1, -1);
      const after = line[end + 1];
      if (after === "(" || after === "[") {
        const linkEnd = after === "(" ? closingParen(line, end + 1) : closingBracket(line, end + 1);
        if (linkEnd !== -1) {
          output += line.slice(i, linkEnd + 1);
          i = linkEnd + 1;
          continue;
        }
      }
      let escapes = 0;
      for (let k = i - 1; k >= 0 && line[k] === "\\"; k -= 1) escapes += 1;
      output += (escapes % 2 || line[i - 1] === "!" || after === ":" || inner.includes("[") || inner.includes("]"))
        ? token : cite(token, inner);
      i = end + 1;
    }
    return output;
  }

  let base = 0;
  return message.split("\n").map((line) => {
    const lineBase = base;
    base += line.length + 1;
    if (inline) return prose(line, lineBase);
    if (fence) {
      const close = /^(?: {0,3}> ?)*([ \t]*)(`+|~+)[ \t]*\r?$/.exec(line);
      if (close && close[1].length <= fence.maxIndent && close[2][0] === fence.marker[0]
        && close[2].length >= fence.marker.length) fence = null;
      return line;
    }
    if (rawTag) {
      const tagEnd = htmlTagEnd(line, 0, rawTag.quote);
      if (tagEnd.end === -1) rawTag.quote = tagEnd.quote;
      else {
        const { name, literal } = rawTag;
        const selfClosing = /\/\s*$/.test(line.slice(0, tagEnd.end));
        if (literal && !selfClosing
          && !new RegExp(`</${name}\\s*>`, "i").test(line.slice(tagEnd.end + 1))) rawElement = name;
        rawTag = null;
      }
      return line;
    }
    // Preserve literal HTML code blocks as opaque elements. A '>' inside a
    // quoted attribute does not complete its opening tag.
    if (rawElement) {
      if (new RegExp(`</${rawElement}\\s*>`, "i").test(line)) rawElement = null;
      return line;
    }
    const rawOpen = /^(?: {0,3}> ?)* {0,3}<([A-Za-z][\w:-]*)(?:\s|\/?>|$)/i.exec(line);
    if (rawOpen) {
      const name = rawOpen[1].toLowerCase();
      const literal = /^(?:pre|code|script|style|textarea)$/.test(name);
      const voidTag = /^(?:area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)$/.test(name);
      const tagEnd = htmlTagEnd(line, line.indexOf("<"), null);
      if (tagEnd.end === -1) {
        rawTag = { name, quote: tagEnd.quote, literal };
        return line;
      }
      const closed = new RegExp(`</${name}\\s*>`, "i").test(line.slice(tagEnd.end + 1));
      const selfClosing = /\/\s*$/.test(line.slice(0, tagEnd.end));
      if (!closed && literal && !selfClosing) rawElement = name;
      if (rawElement || literal || voidTag) return line;
    }
    const marker = /^(?: {0,3}> ?)*( {0,3}(?:[-+*]|\d{1,9}[.)]) +)? {0,3}(`{3,}|~{3,})/.exec(line);
    if (marker) {
      fence = { marker: marker[2], maxIndent: marker[1] ? marker[1].length + 3 : 3 };
      return line;
    }
    const content = line.replace(/^(?: {0,3}> ?)+/, "");
    if (/^(?: {4}|\t)/.test(content)
      || /^(?: {0,3}(?:[-+*]|\d{1,9}[.)]) {5,})/.test(content)) return line;
    return prose(line, lineBase);
  }).join("\n");
}

module.exports = { renderCitations };
