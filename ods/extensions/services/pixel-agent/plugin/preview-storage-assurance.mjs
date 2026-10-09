// The preview inspector proves in-page behavior; it has no reload/navigation
// action. Recognize explicit unsupported storage guarantees, not arbitrary
// completion claims. Neither publication nor a new tab supplies this evidence.
export const PREVIEW_STORAGE_UNVERIFIED =
  'Reload persistence has not been verified for this preview. The in-page checks do not establish saved state after reload, and opening a separate tab does not verify it.';

export function unsupportedPreviewStorageClaim(text) {
  if (typeof text !== 'string') return false;
  const prose = text
    .replace(/```[^]*?(?:```|$)|~~~[^]*?(?:~~~|$)/g, '\n')
    .replace(/^[ \t]*>[^\n]*/gm, '\n')
    .replace(/`([^`\n]+)`/g, (_match, value) => /^(?:localStorage|sessionStorage)$/.test(value) ? value : ' ')
    .replace(/"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<![\p{L}\p{N}])'[^'\n]+'(?![\p{L}\p{N}])/gu, ' ')
    .replace(/https?:\/\/[^\s<>()]+/gi, ' ')
    .replace(/\*\*|__/g, '');
  for (const sentence of prose.split(/(?<=[.!?])\s+|[\n;]/)) {
    if (/\?\s*$/.test(sentence) || /^\s*(?:if|unless)\b/i.test(sentence)) continue;
    const attributed = /\b(?:you|the owner|the user)\s+(?:(?:independently|personally|already)\s+)?(?:verified|confirmed|reported|observed|tested)\b|\baccording to your (?:test|report|observation)\b/i;
    if (attributed.test(sentence) && !/\b(?:however|but|yet|nevertheless|so)\b/i.test(sentence)) continue;
    // A qualification in one clause must not excuse a contradictory promise
    // after "however", "but" or "so" (the recorded Tower3 failure).
    for (const clause of sentence.split(/\b(?:however|but|yet|nevertheless|so)\b|,/i)) {
      if (/\b(?:if|unless|whether)\b/i.test(clause) || attributed.test(clause)) continue;
      const ordinaryBrowser = /\b(?:actual|real|reliable|guaranteed)\s+persistence\b[^.!?]{0,120}\b(?:open|use)\b[^.!?]{0,80}\b(?:regular|normal|separate)\s+(?:browser|tab)\b/i;
      const reload = /\b(?:reloads?|refresh(?:es)?|(?:new|separate)\s+tabs?|browser\s+(?:restarts?|sessions?))\b/i;
      const assertion = /\b(?:will\s+(?:persist|survive|retain|keep|save)|persists?|survives?|(?:is|are|stays?|remains?)\s+(?:saved|retained|persistent)|(?:guarantees?|ensures?)\s+(?:\w+\s+){0,5}persistence)\b/i;
      const match = assertion.exec(clause);
      if (match && reload.test(clause)) {
        const prefix = clause.slice(0, match.index);
        if (!/\b(?:not|never|cannot|can['’]t|doesn['’]t|won['’]t|unverified|unconfirmed|unknown|may|might|could|should|would)\b|\bno\s+guarantee\b/i.test(prefix)) return true;
      }
      // Keep this two-clause idiom together: its comma is punctuation, not an
      // independent qualification. It was the earlier recorded failure.
      if (ordinaryBrowser.test(sentence) && !/\b(?:not|never|cannot|can['’]t)\b/i.test(sentence)) return true;
    }
  }
  return false;
}

export function unverifiedPreviewStorageDelivery(verification) {
  const {deliveryMode: _append, ...scoped} = verification;
  return {...scoped, status: 'failed', text: `${PREVIEW_STORAGE_UNVERIFIED}\n\n${verification.text}`};
}
