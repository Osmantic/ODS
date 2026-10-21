// ODS ships one finished example project, Elf of Doom, in the owner's Portal
// workspace at Playground/elf-of-doom. The host lists it while that folder
// exists, so Portal's sidebar shows it like any other Playground project.
// Opening it asks the host to publish the existing folder for the Workspace
// preview; no model runs and nothing in the workspace changes.

export const EXAMPLE_OPEN_EVENT = 'ods:pixel-open-example-project'
export const EXAMPLE_REFRESH_EVENT = 'ods:pixel-example-projects-changed'
const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

// The present examples' folder names. A failed or unexpected answer throws,
// so the sidebar keeps what it last knew: only the host's own answer that a
// folder is gone removes an example.
export async function readExampleProjects(signal) {
  const response = await fetch('/api/pixel/example-projects', {signal, cache: 'no-store'})
  if (!response.ok) throw new Error('Example projects unavailable')
  const value = await response.json()
  if (!value || value.schemaVersion !== 1 || !Array.isArray(value.projects) || value.projects.length > 8) {
    throw new Error('Invalid example projects')
  }
  return [...new Set(value.projects.filter(name => typeof name === 'string' && NAME.test(name)))]
}

// The host's verified preview receipt for Playground/<name>. The caller still
// validates it exactly like a receipt from a model run.
export async function openExampleProject(name, signal) {
  const response = await fetch('/api/pixel/example-projects/open', {
    method: 'POST', signal, cache: 'no-store',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name}),
  })
  if (!response.ok) {
    const error = new Error('Example project unavailable')
    // 404: the folder is gone. 409: the host refused files in the folder.
    // 422: a folder of that name is there but cannot open as the example
    // (its index.html is missing, or it is not the copy ODS added).
    error.code = {404: 'missing', 409: 'not-previewable', 422: 'not-playable'}[response.status] ?? 'unavailable'
    throw error
  }
  const value = await response.json()
  if (!value || value.schemaVersion !== 1 || !value.preview || typeof value.preview !== 'object') {
    const error = new Error('Invalid example project receipt')
    error.code = 'unavailable'
    throw error
  }
  return value.preview
}

// Sidebar rows for present examples, shaped like the project groups that
// conversations produce. An example that already has conversations is that
// project's own row, so it is never listed twice.
export function exampleProjectRows(names, projects) {
  const paths = new Set(projects.map(project => project.path))
  return names.filter(name => !paths.has(`Playground/${name}`))
    .map(name => ({root: 'Playground', name, path: `Playground/${name}`, chats: [], example: true}))
}
