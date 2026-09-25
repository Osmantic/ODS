// Test-only executable version of PROVIDER-BOOTSTRAP.md's parent review sketch.
// This does not register hooks or alter the installed parent's index.js.
export function composeManagedAdmission(accessRuntime, routing) {
  const blocked = () => ({outcome: 'block', reason: 'managed-admission-unavailable'});
  const finish = async (event, context) => {
    await routing.agentEnd(event, context);
    accessRuntime.finish({runId: event.runId}, context);
  };
  return {
    finish,
    async admit(event, context) {
      try {
        const access = await accessRuntime.admit(undefined, context);
        if (access?.outcome !== 'pass') {
          await finish(event, context);
          return access ?? blocked();
        }
        const decision = await routing.beforeAgentRun(event, context);
        if (decision?.outcome === 'block') await finish(event, context);
        return decision ?? access;
      } catch {
        try { await finish(event, context); }
        catch { /* Retain unknown cleanup in the access runtime. */ }
        return blocked();
      }
    },
  };
}
