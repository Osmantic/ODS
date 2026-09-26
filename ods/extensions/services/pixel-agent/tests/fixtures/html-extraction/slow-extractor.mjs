// Never finishes: the isolated extraction must be terminated at its deadline.
async function neverDone() {
  for (;;) { /* busy */ }
}
export {neverDone as t};
