// Shared owner prose classifier for host ingress and the plugin guard.
// Only unqualified current-owner directives disable the tool surface.
// This module grants no authority.

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
  // Short imperatives must occupy a complete clause. This excludes ordinary
  // descriptions ("No tools are installed") and questions ("No tools?").
  const shorthand = /(?:^|[.!?;,\n]\s*|\b(?:and|but|so|then)\s+)\s*(?:please\s+)?no\s+(?:tools|tool\s+calls)(?:\s+please)?(?=\s*(?:[.!;,\n]|$))/gi;
  for (const match of instruction.matchAll(shorthand)) {
    const rest = instruction.slice(match.index + match[0].length);
    // A comma before an exception does not turn a subset restriction into a
    // blanket ban: "No tools, except read" still permits read.
    if (/^[\s,]*(?:please[\s,]+)?(?:that|which|except|besides|unless|other\s+than|for|to)\b/i.test(rest)) continue;
    return true;
  }
  return false;
}
