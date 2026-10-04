"use strict";

// This whole function is embedded in Vane's pinned client chunk at startup.
// Keep helpers inside it so Function#toString produces a self-contained body.
function renderCitations(message, sources) {
  if (typeof message !== "string") return message;
  const refs = Array.isArray(sources) ? sources : [];
  const numericCitation = /^\s*\d+(?:\s*,\s*\d+)*\s*$/;
  const referenceDefinitions = new Set();
  const listMarker = /(?:[-+*]|\d{1,9}[.)])[ \t]+/y;
  const openingFence = /^(?: {0,3}> ?)*( {0,3}(?:[-+*]|\d{1,9}[.)]) +)? {0,3}(`{3,}|~{3,})/;
  const closingFence = /^(?: {0,3}> ?)*([ \t]*)(`+|~+)[ \t]*\r?$/;
  function commentStartOutsideCode(line) {
    for (let i = 0; i < line.length;) {
      if (line[i] === "`") {
        let end = i + 1;
        while (line[end] === "`") end += 1;
        const marker = line.slice(i, end);
        const close = closingTicks(line, end, marker);
        if (close !== -1) { i = close + marker.length; continue; }
      }
      if (line.startsWith("<!--", i)) return i;
      i += 1;
    }
    return -1;
  }
  let referenceFence = null;
  let htmlComment = false;
  let listContentIndent = 0;
  const lines = message.split("\n");
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    if (referenceFence) {
      const close = closingFence.exec(line);
      if (close && close[1].length <= referenceFence.maxIndent
        && close[2][0] === referenceFence.marker[0]
        && close[2].length >= referenceFence.marker.length) referenceFence = null;
      continue;
    }
    if (htmlComment) {
      if (line.includes("-->")) htmlComment = false;
      continue;
    }
    const commentStart = commentStartOutsideCode(line);
    if (commentStart !== -1) {
      htmlComment = line.indexOf("-->", commentStart + 4) === -1;
      if (!line.slice(0, commentStart).trim()) continue;
    }
    const fenceStart = openingFence.exec(line);
    if (fenceStart) {
      referenceFence = { marker: fenceStart[2], maxIndent: fenceStart[1] ? fenceStart[1].length + 3 : 3 };
      continue;
    }
    // A real Markdown reference can be indented inside repeated list and
    // blockquote containers. Indented code outside a list is not a reference.
    let at = 0;
    let directList = false;
    for (;;) {
      const start = at;
      while (line[at] === " " || line[at] === "\t") at += 1;
      if (line[at] === ">") {
        at += 1;
        if (line[at] === " " || line[at] === "\t") at += 1;
        continue;
      }
      listMarker.lastIndex = at;
      if (listMarker.test(line)) {
        at = listMarker.lastIndex;
        listContentIndent = at;
        directList = true;
        continue;
      }
      at = start;
      break;
    }
    const content = line.slice(at);
    if (!content.trim()) continue;
    let indent = 0;
    for (const char of content) {
      if (char === " ") indent += 1;
      else if (char === "\t") indent += 4 - (indent % 4);
      else break;
    }
    if (!directList && indent < listContentIndent) listContentIndent = 0;
    if (indent - (directList ? 0 : listContentIndent) >= 4) continue;
    const definition = /^[ \t]*\[([0-9]+(?:[ \t]*,[ \t]*[0-9]+)*)\]:[ \t]*(.*)$/.exec(content);
    if (definition) {
      // A label and colon alone are paragraph text. A destination may start
      // on the next line, as in a real CommonMark reference definition.
      const nextLine = lines[lineIndex + 1] || "";
      const nextCommentStart = commentStartOutsideCode(nextLine);
      const nextDestination = !openingFence.test(nextLine)
        && !(nextCommentStart !== -1 && !nextLine.slice(0, nextCommentStart).trim())
        ? nextLine.trim() : "";
      const destination = definition[2].trim() || nextDestination;
      if (/^(?:<[^<>\r\n]*>|[^\s<>"']+)/.test(destination)) {
        referenceDefinitions.add(definition[1].replace(/\s+/g, ""));
      }
    }
  }
  // Raw HTML needs its own parser. A tag-shaped placeholder inside Markdown
  // code is safe to escape as code, but must not disable code rendering for
  // the entire answer. Keep raw HTML outside code on the conservative path.
  const allowInlineCodeHtml = !hasRawHtmlOutsideInlineCode(message);
  let fence = null;
  let inline = null;

  function cite(token, inner) {
    if (!numericCitation.test(inner)) return token;
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
    let htmlFence = null;
    for (const line of text.split("\n")) {
      if (htmlFence) {
        const close = closingFence.exec(line);
        if (close && close[1].length <= htmlFence.maxIndent
          && close[2][0] === htmlFence.marker[0]
          && close[2].length >= htmlFence.marker.length) htmlFence = null;
        continue;
      }
      const marker = openingFence.exec(line);
      if (marker) {
        htmlFence = { marker: marker[2], maxIndent: marker[1] ? marker[1].length + 3 : 3 };
        continue;
      }
      const content = line.replace(/^(?: {0,3}> ?)+/, "");
      if (/^(?: {4}|\t)/.test(content)
        || /^(?: {0,3}(?:[-+*]|\d{1,9}[.)]) {5,})/.test(content)) continue;
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
      let escapes = 0;
      for (let k = i - 1; k >= 0 && line[k] === "\\"; k -= 1) escapes += 1;
      if (after === "(" || after === "[") {
        const linkEnd = after === "(" ? closingParen(line, end + 1) : closingBracket(line, end + 1);
        if (linkEnd !== -1) {
          if (after === "[" && refs.length > 0 && escapes % 2 === 0 && line[i - 1] !== "!"
            && numericCitation.test(inner)) {
            const nextToken = line.slice(end + 1, linkEnd + 1);
            const nextInner = nextToken.slice(1, -1);
            const firstCitation = cite(token, inner);
            if (numericCitation.test(nextInner)
              && !referenceDefinitions.has(nextInner.replace(/\s+/g, ""))
              && firstCitation !== token && cite(nextToken, nextInner) !== nextToken) {
              // Adjacent valid source markers are citations, not a Markdown
              // reference link. Leave the next marker for the next iteration.
              output += firstCitation;
              i = end + 1;
              continue;
            }
          }
          output += line.slice(i, linkEnd + 1);
          i = linkEnd + 1;
          continue;
        }
      }
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
    const marker = openingFence.exec(line);
    if (fence) {
      const close = closingFence.exec(line);
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
