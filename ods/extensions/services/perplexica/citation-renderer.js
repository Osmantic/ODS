"use strict";

// This whole function is embedded in Vane's pinned client chunk at startup.
// Keep helpers inside it so Function#toString produces a self-contained body.
function renderCitations(message, sources) {
  if (typeof message !== "string") return message;
  const refs = Array.isArray(sources) ? sources : [];
  let fence = null;
  let inline = null;
  const numeric = /^\s*\d+(?:\s*,\s*\d+)*\s*$/;
  const definitions = new Set();
  const referenceLabel = (label) => label.trim().replace(/\s+/g, " ");

  function cite(token, inner) {
    if (!numeric.test(inner)) return token;
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

  function prose(line, base, collecting) {
    if (collecting && !inline) {
      const content = line.replace(/^(?: {0,3}> ?)+/, "");
      const definition = /^ {0,3}\[([^\]\n]+)\]:\s*\S/.exec(content);
      if (definition && numeric.test(definition[1])) definitions.add(referenceLabel(definition[1]));
    }
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
        if (escapes % 2 === 0 && hasClosingTicks(base + end, marker)) inline = marker;
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
        // Undefined numeric pairs are adjacent source citations, not reference
        // links. Preserve real reference links, images, and escaped brackets.
        const nextLabel = line.slice(end + 2, linkEnd);
        const adjacent = after === "[" && linkEnd !== -1 && numeric.test(inner)
          && numeric.test(nextLabel) && !definitions.has(referenceLabel(nextLabel))
          && escapes % 2 === 0 && line[i - 1] !== "!";
        if (linkEnd !== -1 && !adjacent) {
          output += line.slice(i, linkEnd + 1);
          i = linkEnd + 1;
          continue;
        }
      }
      output += (collecting || escapes % 2 || line[i - 1] === "!" || after === ":" || inner.includes("[") || inner.includes("]"))
        ? token : cite(token, inner);
      i = end + 1;
    }
    return output;
  }

  function walk(collecting) {
    let base = 0;
    fence = null;
    inline = null;
    return message.split("\n").map((line) => {
      const lineBase = base;
      base += line.length + 1;
      if (inline) return prose(line, lineBase, collecting);
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
      if (/^(?: {4}|\t)/.test(content)) return line;
      return prose(line, lineBase, collecting);
    }).join("\n");
  }
  // Reuse the same code-aware walker so definitions inside fenced, indented,
  // or multiline inline code cannot hide legitimate citations elsewhere.
  walk(true);
  return walk(false);
}

module.exports = { renderCitations };
