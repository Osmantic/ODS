"use strict";

// This whole function is embedded in Vane's pinned client chunk at startup.
// Keep helpers inside it so Function#toString produces a self-contained body.
function renderCitations(message, sources) {
  if (typeof message !== "string") return message;
  const refs = Array.isArray(sources) ? sources : [];
  // Raw HTML needs its own parser. A tag-shaped placeholder inside Markdown
  // code is safe to escape as code, but must not disable code rendering for
  // the entire answer. Keep raw HTML outside code on the conservative path.
  const allowInlineCodeHtml = !hasRawHtmlOutsideInlineCode(message);
  let fence = null;
  let inline = null;

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

  function hasRawHtmlOutsideInlineCode(text) {
    for (const line of text.split("\n")) {
      let outside = "";
      for (let i = 0; i < line.length;) {
        if (line[i] === "`") {
          let end = i + 1;
          while (line[end] === "`") end += 1;
          let escapes = 0;
          for (let k = i - 1; k >= 0 && line[k] === "\\"; k -= 1) escapes += 1;
          if (escapes % 2 === 0) {
            const marker = line.slice(i, end);
            const close = closingTicks(line, end, marker);
            if (close !== -1) { i = close + marker.length; continue; }
          }
        }
        outside += line[i];
        i += 1;
      }
      if (/<[A-Za-z/!?]/.test(outside)) return true;
    }
    return false;
  }

  function inlineCode(value) {
    const escaped = value.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    // Vane v1.12.2 returns literal backticks for Markdown codeInline nodes.
    // not-prose prevents Typography from adding its own backtick pseudo-text.
    return `<span class="not-prose"><code style="font-family:monospace;padding:0 .2em;border-radius:.2em;background-color:rgba(127,127,127,.14)">${escaped}</code></span>`;
  }

  function prose(line, base) {
    let output = "";
    for (let i = 0; i < line.length;) {
      if (inline) {
        let close = line.indexOf(inline, i);
        while (close !== -1 && (line[close - 1] === "`" || line[close + inline.length] === "`")) {
          close = line.indexOf(inline, close + inline.length);
        }
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
        if (escapes % 2 === 0) {
          if (allowInlineCodeHtml) {
            const close = closingTicks(line, end, marker);
            if (close !== -1) {
              output += inlineCode(line.slice(end, close));
              i = close + marker.length;
              continue;
            }
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
    const marker = /^(?: {0,3}> ?)*( {0,3}(?:[-+*]|\d{1,9}[.)]) +)? {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const close = /^(?: {0,3}> ?)*([ \t]*)(`+|~+)[ \t]*\r?$/.exec(line);
      if (close && close[1].length <= fence.maxIndent && close[2][0] === fence.marker[0]
        && close[2].length >= fence.marker.length) fence = null;
      return line;
    }
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
