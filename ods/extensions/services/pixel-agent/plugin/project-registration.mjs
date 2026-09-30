import {createProjectBuildTool} from './project-build.mjs';
import {createProjectTransport} from './project-transport.mjs';
import {projectCapabilityOutputLimit} from './project-capabilities.mjs';

const portalOwner = context => context?.agentId === 'pixel'
  && typeof context.sessionKey === 'string'
  && /^agent:pixel:openai-user:ods-[a-f0-9]{64}$/.test(context.sessionKey);

export function registerProjectBuild(api, wrap = factory => factory, runControl) {
  const socketPath = api.pluginConfig?.projectBuildSocket;
  if (typeof socketPath !== 'string' || !socketPath.startsWith('/') || socketPath.includes('\0')) return;
  api.on('before_prompt_build', (_event, context) => {
    if (!portalOwner(context)) return;
    return {appendSystemContext: 'ODS provides pixel_ods_project_build for workspace npm projects (package.json and matching package-lock.json) and Python 3.11 projects (ods-project.json with runtime python, requirements.lock with exact versions and verified PyPI wheel SHA-256 hashes, main.py and unittest tests/test_*.py). When the owner requests dependency installation, project tests or generated artifacts, inspect the project and discover this tool with tool_search/tool_describe before choosing host shell commands. The managed Python path provides pip and an isolated environment even when the conversational sandbox lacks pip; do not infer global inability from a missing sandbox executable. Before choosing Python wheel hashes, call pixel_ods_project_build with action capabilities and runtime python. Use its actual installed-image Python ABI and accepted wheel tags, never the host or conversational sandbox architecture. The receipt describes this installation only; do not claim the lock is portable to other architectures. If compatibility is unavailable, do not guess; report that limitation and continue independent work. Verify dependency versions, transitive dependencies and compatible wheel hashes from registry metadata rather than guessing. Use the managed acquire/test/build workflow instead of installing on the host. Project code runs offline; source distributions, alternative package managers and arbitrary server processes are not supported by this workflow. Observe the returned jobId; do not resubmit an uncertain result. Deliver generated files, or publish the output directory and inspect the publication when a website was requested. This availability is not a permission grant: honor controller denials and report unsupported project formats or missing prerequisites without silently changing the framework or bypassing approval.'};
  });
  api.registerTool(wrap(context => {
    if (!portalOwner(context)) return null;
    const request = createProjectTransport({socketPath, sessionId: context.sessionKey});
    const capabilityMaxChars = projectCapabilityOutputLimit(api.config, context.agentId);
    const tool = createProjectBuildTool({request, capabilityMaxChars});
    if (!runControl) return tool;
    return {...tool, execute: async (id, params, signal) => {
      const bound = runControl.bind(id, params, context, request);
      return createProjectBuildTool({request: bound, capabilityMaxChars}).execute(id, params, signal);
    }};
  }), {names: ['pixel_ods_project_build']});
}
