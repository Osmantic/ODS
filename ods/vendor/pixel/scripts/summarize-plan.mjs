import { readFile } from "node:fs/promises";

const config = JSON.parse(await readFile(process.argv[2], "utf8"));
const agentId = process.env.PIXEL_AGENT_ID;
const agent = (config.agents?.list ?? []).find((item) => item.id === agentId);
const summary = {
  profiles: { deployment: process.env.PIXEL_DEPLOYMENT_PROFILE, capability: process.env.PIXEL_CAPABILITY_PROFILE },
  agent: { id: agent?.id, name: agent?.name, workspace: agent?.workspace, model: agent?.model, heartbeat: agent?.heartbeat },
  sandbox: config.agents?.defaults?.sandbox,
  memory: config.agents?.defaults?.memorySearch,
  webSearch: config.tools?.web?.search,
  pluginAllowlist: config.plugins?.allow,
  pluginLoadPaths: config.plugins?.load?.paths,
  limbs: {
    email: process.env.PIXEL_LIMB_EMAIL_ENABLED === "1",
    calendar: process.env.PIXEL_LIMB_CALENDAR_ENABLED === "1",
    social: process.env.PIXEL_LIMB_SOCIAL_ENABLED === "1",
    web: process.env.PIXEL_LIMB_WEB_ENABLED === "1",
    operations: process.env.PIXEL_LIMB_OPERATIONS_ENABLED === "1",
    frontier: process.env.PIXEL_LIMB_FRONTIER_ENABLED === "1",
  },
  sourceBrokerPolicy: { emailAndCalendar: "sanitized projections only", rawSourceContent: "unavailable to Pixel", calendarMutations: "bounded private creates and time-only reschedules may execute directly; consequential changes require separate approval", agentIsolation: agentId },
  operationsBrokerPolicy: { credentials: "unavailable to Pixel", execution: "typed immutable plans", privilegedChanges: "exact plan hash requires external approval", output: "untrusted bounded evidence" },
  frontierBrokerPolicy: { credentials: "unavailable to Pixel", egress: "sanitized typed capsules only", confidentialData: "exact payload hash requires external approval", output: "untrusted structured advice" },
};
console.log(JSON.stringify(summary, null, 2));
