import {createProjectBuildTool} from './project-build.mjs';
import {createProjectTransport} from './project-transport.mjs';

export function registerProjectBuild(api, wrap = factory => factory) {
  const socketPath = api.pluginConfig?.projectBuildSocket;
  if (typeof socketPath !== 'string' || !socketPath.startsWith('/') || socketPath.includes('\0')) return;
  api.registerTool(wrap(context => {
    if (context?.agentId !== 'pixel' || typeof context.sessionKey !== 'string'
        || !/^agent:pixel:openai-user:ods-[a-f0-9]{64}$/.test(context.sessionKey)) return null;
    return createProjectBuildTool({request: createProjectTransport({socketPath, sessionId: context.sessionKey})});
  }), {names: ['pixel_ods_project_build']});
}
