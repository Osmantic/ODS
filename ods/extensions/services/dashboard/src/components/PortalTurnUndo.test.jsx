import {fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import PortalTurnUndo from './PortalTurnUndo'
import {changedProjects, restoreProject} from '../lib/projectRestore'
import {ODS_HELP_DISCORD_URL} from '../lib/support'

const RUN = 'chatcmpl_11111111-2222-4333-8444-555555555555'
const UNDO = 'restore_99999999-2222-4333-8444-555555555555'
const project = (name, observedAt) => ({schemaVersion: 1, kind: 'ods-workspace-project', relativeDirectory: `Playground/${name}`, observedAt})
// A finished turn of 12:00:00-12:05:00 that changed game; notes changed on an earlier turn.
function task(projects = [project('game', '2026-10-30T12:03:00.000Z'), project('notes', '2026-10-30T11:00:00.000Z')], extra = {}) {
  return {schemaVersion: 4, runId: RUN, startedAt: '2026-10-30T12:00:00.000Z', finishedAt: '2026-10-30T12:05:00.000Z',
    state: 'completed', calls: 1, failures: 0, blocked: 0, truncated: false, activities: [{kind: 'run', calls: 1, failures: 0, blocked: 0}],
    events: [{sequence: 1, kind: 'run', state: 'completed', startedAt: '2026-10-30T12:01:00.000Z', finishedAt: '2026-10-30T12:02:00.000Z', display: null}],
    context: null, goal: null, projects, ...extra}
}
const receipt = (extra = {}) => ({schemaVersion: 1, project: 'Playground/game', snapshot: RUN, written: 1, removed: 2,
  kept: [], keptCount: 0, failed: [], failedCount: 0, complete: true, undo: UNDO, ...extra})
const answer = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => body})

afterEach(() => vi.unstubAllGlobals())

test('only projects whose latest change happened during the turn get Put back', () => {
  expect(changedProjects(task())).toEqual(['Playground/game'])
  // A turn that is still running has no end yet.
  expect(changedProjects(task(undefined, {state: 'running', finishedAt: null}))).toEqual(['Playground/game'])
  expect(changedProjects(task([project('notes', '2026-10-30T11:00:00.000Z')]))).toEqual([])
  expect(changedProjects({...task(), schemaVersion: 3})).toEqual([])
  expect(changedProjects({...task(), runId: 'other'})).toEqual([])
  expect(changedProjects(undefined)).toEqual([])
})

test('one step puts the files back, says what changed, and Undo reverses it', async () => {
  const fetch = vi.fn(async () => answer(receipt({kept: ['node_modules', '.git'], keptCount: 2})))
  vi.stubGlobal('fetch', fetch)
  render(<PortalTurnUndo chatId="chat-1" task={task()}/>)
  expect(screen.getAllByRole('button')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', {name: 'Put back files from before this turn'}))
  expect(await screen.findByText('Playground/game is back to how it was before this turn: 1 file put back and 2 new files removed. Not copied, so left as they are: node_modules and .git.')).toBeVisible()
  expect(fetch).toHaveBeenCalledWith('/api/pixel/projects/restore', expect.objectContaining({method: 'POST', cache: 'no-store'}))
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({chat_id: 'chat-1', project: 'Playground/game', snapshot: RUN})
  // A success never looks like an error.
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.queryByRole('button', {name: 'Put back files from before this turn'})).toBeNull()

  fetch.mockResolvedValueOnce(answer(receipt({snapshot: UNDO, written: 3, removed: 0, undo: 'restore_88888888-2222-4333-8444-555555555555'})))
  fireEvent.click(screen.getByRole('button', {name: 'Undo putting back Playground/game'}))
  expect(await screen.findByText('Playground/game is back to how it was before you put the files back: 3 files put back.')).toBeVisible()
  expect(JSON.parse(fetch.mock.calls[1][1].body).snapshot).toBe(UNDO)
  // After the undo the turn's Put back is offered again.
  expect(screen.getByRole('button', {name: 'Put back files from before this turn'})).toBeEnabled()
  expect(screen.queryByRole('button', {name: /Undo/})).toBeNull()
})

test('nothing to put back is said calmly, without Undo', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => answer(receipt({written: 0, removed: 0, undo: null}))))
  render(<PortalTurnUndo chatId="chat-1" task={task()}/>)
  fireEvent.click(screen.getByRole('button', {name: 'Put back files from before this turn'}))
  expect(await screen.findByText('Playground/game already matches how it was before this turn, so nothing changed.')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('button', {name: /Undo/})).toBeNull()
})

test('a busy chat waits calmly; a real failure says why, what to do, and links help', async () => {
  const fetch = vi.fn(async () => answer({detail: 'busy'}, 409))
  vi.stubGlobal('fetch', fetch)
  const view = render(<PortalTurnUndo chatId="chat-1" task={task()} busy/>)
  const button = screen.getByRole('button', {name: 'Put back files from before this turn'})
  expect(button).toBeDisabled()
  expect(button).toHaveAttribute('title', 'Portal is still working in this chat')
  view.rerender(<PortalTurnUndo chatId="chat-1" task={task()}/>)
  fireEvent.click(button)
  expect(await screen.findByText('Portal is still working. Put the files back when it finishes.')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  for (const [status, text] of [
    [404, 'ODS no longer has a copy of Playground/game from before this turn (it keeps only recent ones), so nothing was changed. Ask Portal to fix the files instead.'],
    [422, 'The copy ODS kept of Playground/game is damaged, so nothing was changed. Ask Portal to fix the files instead.'],
    [503, 'Portal could not confirm that the files were put back. Check Playground/game, then try again.'],
  ]) {
    fetch.mockResolvedValueOnce(answer({detail: 'x'}, status))
    fireEvent.click(screen.getByRole('button', {name: 'Put back files from before this turn'}))
    const alert = await screen.findByRole('alert')
    await waitFor(() => expect(alert).toHaveTextContent(text))
    expect(within(alert).getByRole('link', {name: /Get help on Discord/})).toHaveAttribute('href', ODS_HELP_DISCORD_URL)
  }
  // An unexpected receipt is not a success.
  fetch.mockResolvedValueOnce(answer({...receipt(), project: 'Playground/other'}))
  await expect(restoreProject({chatId: 'chat-1', project: 'Playground/game', snapshot: RUN})).rejects.toMatchObject({code: 'unavailable'})
})

test('files that could not be put back are named, with Undo and Put back to try again', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => answer(receipt({failed: ['chars.js'], failedCount: 1, written: 0}))))
  render(<PortalTurnUndo chatId="chat-1" task={task()}/>)
  fireEvent.click(screen.getByRole('button', {name: 'Put back files from before this turn'}))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('chars.js could not be put back. Close any program that is using that file, then try again.')
  expect(within(alert).getByRole('link', {name: /Get help on Discord/})).toBeVisible()
  expect(screen.getByRole('button', {name: 'Put back files from before this turn'})).toBeEnabled()
  expect(screen.getByRole('button', {name: 'Undo putting back Playground/game'})).toBeEnabled()
})

test('a turn that changed two projects names each one, and a turn that changed none shows nothing', () => {
  const both = task([project('game', '2026-10-30T12:03:00.000Z'), project('notes', '2026-10-30T12:04:00.000Z')])
  const view = render(<PortalTurnUndo chatId="chat-1" task={both}/>)
  expect(screen.getByRole('button', {name: 'Put back Playground/game from before this turn'})).toBeVisible()
  expect(screen.getByRole('button', {name: 'Put back Playground/notes from before this turn'})).toBeVisible()
  view.rerender(<PortalTurnUndo chatId="chat-1" task={task([project('notes', '2026-10-30T11:00:00.000Z')])}/>)
  expect(screen.queryByRole('button')).toBeNull()
  view.rerender(<PortalTurnUndo chatId="" task={both}/>)
  expect(screen.queryByRole('button')).toBeNull()
})
