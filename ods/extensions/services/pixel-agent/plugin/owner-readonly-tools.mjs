// An explicit owner file ban permits known observation tools and isolated
// calculations. Unknown tools and delegations cannot silently inherit write
// authority. This policy does not itself create a read-only execution boundary.
const CORE_READ_TOOLS = new Set([
  'read', 'glob', 'grep', 'image', 'web_search', 'web_fetch',
  'memory_search', 'memory_get', 'sessions_list', 'sessions_history', 'session_status',
]);
const ODS_READ_TOOLS = new Set([
  'pixel_ods_readonly_exec', 'pixel_ods_image_read', 'pixel_ods_ask_user',
  'pixel_ods_history', 'pixel_ods_status', 'pixel_ods_apps_list', 'pixel_ods_extensions',
  'pixel_ods_host_observe', 'pixel_ods_web_extract', 'pixel_ods_extension_request_status',
]);

export const OWNER_FILE_MUTATION_FORBIDDEN_REASON =
  'The owner requested no file changes in this turn. Read existing files normally. For calculations or shell-based analysis, discover and use pixel_ods_readonly_exec; it protects the owner workspace from writes. Do not use ordinary exec, file-writing tools, or delegation to bypass this restriction.';

export function ownerReadonlyToolAllowed(toolName, params, internalExecution = false, requestedPreview = false) {
  // Publishing an explicitly requested existing preview reads owner files into
  // a separate immutable serving snapshot. It does not edit the source tree.
  // The ordinary preview authorization and verification gates still apply.
  const allowedOds = name => ODS_READ_TOOLS.has(name) ||
    (requestedPreview === true && name === 'pixel_ods_workspace_preview');
  // This boolean is supplied only by the guard's private execution authority.
  // It cannot authorize a deferred dispatch or another tool with a similar ID.
  if (internalExecution && (toolName === 'exec' || toolName === 'process')) return true;
  // Discovery grants no execution authority: every selected tool is checked
  // again at dispatch, including results that describe file-changing tools.
  if (toolName === 'tool_search') return true;
  if (toolName === 'tool_call') {
    const id = params?.id;
    if (typeof id !== 'string') return false;
    if (id === 'tool_search') return true;
    if (id.startsWith('openclaw:core:')) return CORE_READ_TOOLS.has(id.slice('openclaw:core:'.length));
    if (id.startsWith('openclaw:pixel-ods:')) return allowedOds(id.slice('openclaw:pixel-ods:'.length));
    return CORE_READ_TOOLS.has(id) || allowedOds(id);
  }
  return CORE_READ_TOOLS.has(toolName) || allowedOds(toolName);
}
