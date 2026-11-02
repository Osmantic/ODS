// Shared owner prose classifier. Extracted unchanged from tool-loop-guard.mjs
// so the host ingress can apply the same unqualified no-tools directive test
// without importing the full guard. This module grants no authority.

export function ownerLaneText(text, {preserveCommands = false} = {}) {
  // Classify only current owner prose. Embedded examples cannot opt a workspace
  // turn into extension work; identifiers quoted as operands remain usable.
  return String(text ?? '')
    .replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, ' ')
    .replace(/^\s*>[^\n]*/gm, ' ')
    .replace(/"[^"\n]*"|`[^`\n]*`|(?<!\w)'[^'\n]*'(?!\w)|“[^”\n]*”/g,
      value => preserveCommands && value.startsWith('`') ? value :
        /\s/.test(value.slice(1, -1)) ? ' ' : value);
}

export function ownerForbidsTools(text) {
  const instruction = ownerLaneText(text);
  const ban = /\b(?:(?:do\s+not|don['’]t|never|must\s+not|should\s+not)\s+(?:use|call|invoke|run)|without\s+(?:using|calling|invoking|running))\s+(?:any\s+tools?|(?:the\s+)?tools)\b/gi;
  for (const match of instruction.matchAll(ban)) {
    const prefix = instruction.slice(0, match.index);
    if (!/^without\b/i.test(match[0]) &&
        !/(?:^|[.!?;,\n]\s*|\b(?:and|but|so|then)\s+)(?:please\s+|you\s+)?$/i.test(prefix)) continue;
    const qualifier = instruction.slice(match.index + match[0].length);
    // A ban on a named subset of tools still permits other tools. The
    // blanket boundary applies only to an unqualified no-tools directive.
    if (/^\s+(?:that|which|except|besides|unless|other\s+than)\b/i.test(qualifier) ||
        /^\s+to\s+(?:change|edit|write|modify|delete|create|remove|mutate)\b/i.test(qualifier) ||
        /^\s+for\s+(?:file|writing|editing|modifying|changing|mutation)\b/i.test(qualifier)) continue;
    return true;
  }
  return false;
}
