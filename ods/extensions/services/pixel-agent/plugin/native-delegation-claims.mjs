// A narrow truth check for an explicitly requested native delegation. These
// predicates grant no tool authority and do not decide how work is performed.
function prose(value) {
  if (typeof value !== 'string' || value.length > 256 * 1024) return '';
  return value.replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, ' ')
    .replace(/^\s*>[^\n]*/gm, ' ')
    .replace(/"[^"\n]*"|`[^`\n]*`|(?<!\w)'[^'\n]*'(?!\w)|“[^”\n]*”/g, ' ')
    .replace(/\*+/g, '');
}

export function requestsNativeDelegation(value) {
  const clauses = prose(value).split(/[.!?;\n]+/);
  return clauses.some(clause => /^(?:\s*)(?:please\s+|(?:can|could|would)\s+you\s+(?:please\s+)?)?(?:delegate|spawn|start|use)\b/i.test(clause)
    && /\b(?:native\s+(?:subagents?|subagent\s+tools?)|sessions_spawn)\b/i.test(clause));
}

export function claimsNativeDelegation(value) {
  return prose(value).split(/[.!?;\n]+/).some(clause => {
    if (/\b(?:yesterday|previous(?:ly)?|last\s+(?:turn|request|session))\b/i.test(clause)) return false;
    const active = /^\s*(?:I|we)(?:['’]ve|\s+have)?\s+(?:successfully\s+)?(delegated|spawned|started)\s+(.+)$/i.exec(clause);
    if (active && !/^(?:no(?:ne)?|nothing|zero)\b/i.test(active[2])
        && (active[1].toLowerCase()==='delegated' || /^(?:(?:a|an|the|one|two|three|both|\d+)\s+)*(?:native\s+)?subagents?\b/i.test(active[2]))) return true;
    return /^\s*(?:(?:both|the|two|all)\s+)?(?:native\s+)?subagents?\s+(?:(?:has|have)\s+been\s+|(?:was|were)\s+)(?:successfully\s+)?(?:spawned|started)\b/i.test(clause)
      || /^\s*(?:(?:both|the|two|all)\s+)?(?:native\s+)?subagents?\s+(?:is|are)\s+(?:now\s+)?running\b/i.test(clause);
  });
}
