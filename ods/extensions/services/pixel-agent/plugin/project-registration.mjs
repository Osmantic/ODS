import {createProjectBuildTool} from './project-build.mjs';
import {createProjectTransport} from './project-transport.mjs';

const portalOwner = context => context?.agentId === 'pixel'
  && typeof context.sessionKey === 'string'
  && /^agent:pixel:openai-user:ods-[a-f0-9]{64}$/.test(context.sessionKey);

export function registerProjectBuild(api, wrap = factory => factory, runControl) {
  const socketPath = api.pluginConfig?.projectBuildSocket;
  if (typeof socketPath !== 'string' || !socketPath.startsWith('/') || socketPath.includes('\0')) return;
  api.on('before_prompt_build', (_event, context) => {
    if (!portalOwner(context)) return;
    return {appendSystemContext: 'ODS provides pixel_ods_project_build for existing workspace npm projects with package.json and a matching package-lock.json. When the owner requests dependency installation, project tests or a build, inspect the manifests and discover this tool with tool_search/tool_describe before choosing host shell commands. For its supported acquire/test/build workflow, use the managed tool instead of npm install, npm test or npm run build on the host. Observe the returned jobId; do not resubmit an uncertain result. Publish the returned output directory and inspect the publication separately when requested. This availability is not a permission grant: honor controller denials and report unsupported project formats or missing prerequisites without silently changing the framework or bypassing approval.'};
  });
  api.registerTool(wrap(context => {
    if (!portalOwner(context)) return null;
    const request = createProjectTransport({socketPath, sessionId: context.sessionKey});
    const tool = createProjectBuildTool({request});
    if (!runControl) return tool;
    return {...tool, execute: async (id, params, signal) => {
      const bound = runControl.bind(id, params, context, request);
      return createProjectBuildTool({request: bound}).execute(id, params, signal);
    }};
  }), {names: ['pixel_ods_project_build']});
}
