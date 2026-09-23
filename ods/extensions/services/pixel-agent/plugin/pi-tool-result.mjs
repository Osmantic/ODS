// Pi records tool failures from details, not from the top-level isError flag
// returned by an OpenClaw plugin tool. Preserve the original receipt and make
// the same failure visible to the agent transcript and activity projection.
export function preservePiToolError(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result)
      || result.isError !== true) return result;
  const details = result.details;
  const objectDetails = details && typeof details === 'object' && !Array.isArray(details);
  if (objectDetails && details.ok === false) return result;
  return {...result, details: objectDetails
    ? {...details, ok:false}
    : {...(details === undefined ? {} : {originalDetails:details}), ok:false}};
}

export function withPiToolErrorContract(tool) {
  if (!tool || typeof tool.execute !== 'function') return tool;
  return {...tool, async execute(...args) {
    return preservePiToolError(await tool.execute.apply(tool, args));
  }};
}
