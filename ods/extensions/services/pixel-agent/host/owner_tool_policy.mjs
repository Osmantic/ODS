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

export function ownerForbidsFileMutation(text) {
  // A deliberately narrow grammar for explicit blanket instructions. A file
  // restriction is independent of a tool ban: reads and calculations remain
  // possible. Named paths, exceptions and conditional bans are not blanket.
  const verbs = /(?:create|write|edit|modify|delete|rename|remove|change|touch)/.source;
  const gerunds = /(?:creating|writing|editing|modifying|deleting|renaming|removing|changing|touching)/.source;
  const separator = /(?:\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)/.source;
  const files = /\s+(?:(?:any|all)\s+)?files\b/.source;
  const verbList = verb => `${verb}(?:${separator}${verb})*${files}`;
  const directive = /(?:^|\b(?:and|but|then)\s+)(?:please\s+)?(?:you\s+)?(?:do\s+not|don['’]t|never|must\s+not|should\s+not)\s+/.source;
  const bans = [new RegExp(directive + verbList(verbs), 'gi'),
    new RegExp(/\bwithout\s+/.source + verbList(gerunds), 'gi'),
    /^(?:please\s+)?(?:no\s+file\s+(?:writes|changes|modifications)|keep\s+(?:the\s+)?(?:filesystem|workspace)\s+read-only)\b/gi];
  for (const clause of ownerLaneText(text).split(/[.!?;\n]+/)) {
    const text = clause.trim();
    if (/^(?:if|unless|when|whether|in\s+case|assuming|provided)\b/i.test(text)) continue;
    for (const ban of bans) for (const match of text.matchAll(ban)) {
      const prefix = text.slice(0, match.index);
      if (/\b(?:says?|said|means?|meant|asks?|asked|example|quote|instruction)\b/i.test(prefix)) continue;
      const tail = text.slice(match.index + match[0].length).trim();
      // A subsequent positive read/calculation instruction is compatible;
      // arbitrary tails (paths, file lists, exceptions) are not accepted.
      if (!tail || /^(?:,\s*)?(?:and|but|then)\s+(?:read|calculate|compute|return|answer|summarize|summarise)\b/i.test(tail)) return true;
    }
  }
  return false;
}
