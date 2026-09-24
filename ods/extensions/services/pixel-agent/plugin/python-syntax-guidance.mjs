// This is diagnostic advice only. Never change source bytes or execution truth.
export function pythonSyntaxGuidance(params, result) {
  if (typeof params?.command !== 'string' ||
      !/^\s*(?:\/[^\s'";|&]+\/)?python(?:3(?:\.\d+)?)?(?=\s|$)/.test(params.command) ||
      result?.details?.status !== 'completed' ||
      !Number.isInteger(result.details.exitCode) || result.details.exitCode === 0) return undefined;
  const text = typeof result.details.aggregated === 'string' ? result.details.aggregated
    : (result.content ?? []).filter(block => block?.type === 'text' && typeof block.text === 'string')
      .map(block => block.text).join('\n');
  if (!/^SyntaxError: unexpected character after line continuation character\s*$/m.test(text) ||
      !/^\s*File "[^"\r\n]+", line \d+/m.test(text) || !/^\s*\^+\s*$/m.test(text)) return undefined;
  return '[ODS Pixel Python syntax] Inspect the reported line and nearby source bytes with repr before editing. ' +
    'For <stdin> or <string>, inspect the submitted Python snippet. A literal backslash-n outside a string may need a real line break; ' +
    'escaped quote bytes outside a string can cause this error too. Confirm the actual bytes first. ' +
    'Preserve valid escapes inside strings; never globally replace them. Make one targeted correction, preserve existing files and assertions, ' +
    'then rerun the same failed command within the remaining repair budget. Parsing failure does not verify behavior.';
}
