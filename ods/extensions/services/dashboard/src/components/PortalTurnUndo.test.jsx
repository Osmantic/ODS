import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import PortalTurnUndo from './PortalTurnUndo'
import {finishedRuns, restoreOptions, restoreProject} from '../lib/projectRestore'
import {ODS_HELP_DISCORD_URL} from '../lib/support'

const RUN = 'chatcmpl_11111111-2222-4333-8444-555555555555'
const UNDO = 'restore_99999999-2222-4333-8444-555555555555'
const UNDO_2 = 'restore_88888888-2222-4333-8444-555555555555'
const TOKEN = 'c'.repeat(64)
const entry = (extra = {}) => ({project: 'Playground/game', snapshot: RUN, late: false, restore: null, ...extra})
const receipt = (extra = {}) => ({schemaVersion: 2, status: 'restored', project: 'Playground/game', snapshot: RUN, written: 1, removed: 2,
  left: [], leftCount: 0, failed: [], failedCount: 0, failure: null, late: false, undo: UNDO, confirm: null, ...extra})
const answer = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => body})
const refusal = (code, status) => answer({detail: {code, message: 'host words'}}, status)
const sent = fetch => fetch.mock.calls.map(([, options]) => JSON.parse(options.body))
const PUT_BACK = 'Put back files from before this turn'

afterEach(() => vi.unstubAllGlobals())

test('one step puts the files back, says what changed, and Undo reverses it; the Undo can be reversed too', async () => {
  const fetch = vi.fn(async () => answer(receipt()))
  const changed = vi.fn()
  vi.stubGlobal('fetch', fetch)
  render(<PortalTurnUndo chatId="chat-1" entries={[entry()]} onChanged={changed}/>)
  expect(screen.getAllByRole('button')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  expect(await screen.findByText('Playground/game is back to how it was before this turn: 1 file written back and 2 files removed. '
    + 'A preview of this project still shows the earlier files until Portal publishes it again.')).toBeVisible()
  expect(fetch).toHaveBeenCalledWith('/api/pixel/projects/restore', expect.objectContaining({method: 'POST', cache: 'no-store'}))
  // A success never looks like an error.
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.queryByRole('button', {name: PUT_BACK})).toBeNull()
  expect(changed).toHaveBeenCalledTimes(1)

  fetch.mockResolvedValueOnce(answer(receipt({snapshot: UNDO, written: 3, removed: 0, undo: UNDO_2})))
  fireEvent.click(screen.getByRole('button', {name: 'Undo putting back Playground/game'}))
  expect(await screen.findByText(/^Playground\/game is back to how it was before you put the files back: 3 files written back\./)).toBeVisible()
  // The Undo's own copy puts the files from before the turn back again.
  fetch.mockResolvedValueOnce(answer(receipt({snapshot: UNDO_2, undo: 'restore_77777777-2222-4333-8444-555555555555'})))
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  await screen.findByRole('button', {name: 'Undo putting back Playground/game'})
  expect(sent(fetch)).toEqual([{chat_id: 'chat-1', project: 'Playground/game', snapshot: RUN},
    {chat_id: 'chat-1', project: 'Playground/game', snapshot: UNDO}, {chat_id: 'chat-1', project: 'Playground/game', snapshot: UNDO_2}])
  expect(changed).toHaveBeenCalledTimes(3)
})

test('after a reload the host says which step comes next', async () => {
  const fetch = vi.fn(async () => answer(receipt({snapshot: UNDO, undo: UNDO_2})))
  vi.stubGlobal('fetch', fetch)
  const {unmount} = render(<PortalTurnUndo chatId="chat-1" entries={[entry({restore: {undo: UNDO, side: 'before'}})]}/>)
  expect(screen.getByText('The files from before this turn were put back.')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name: 'Undo putting back Playground/game'}))
  await screen.findByRole('button', {name: PUT_BACK})
  expect(sent(fetch)[0].snapshot).toBe(UNDO)
  unmount()
  render(<PortalTurnUndo chatId="chat-1" entries={[entry({restore: {undo: UNDO_2, side: 'after'}})]}/>)
  fetch.mockResolvedValueOnce(answer(receipt({snapshot: UNDO_2})))
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  await screen.findByRole('button', {name: 'Undo putting back Playground/game'})
  expect(sent(fetch)[1].snapshot).toBe(UNDO_2)
})

test('changes made after the turn are named and need a yes; Cancel changes nothing', async () => {
  const asks = receipt({status: 'confirm', written: 0, removed: 0, undo: null,
    confirm: {token: TOKEN, overwrite: ['chars.js'], overwriteCount: 1, remove: ['new.js', 'a.js'], removeCount: 5}})
  const fetch = vi.fn(async () => answer(asks))
  vi.stubGlobal('fetch', fetch)
  render(<PortalTurnUndo chatId="chat-1" entries={[entry()]}/>)
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Putting back the files from before this turn will overwrite chars.js '
    + 'and remove new.js, a.js and 3 more, which may hold changes made after this turn.')
  fireEvent.click(screen.getByRole('button', {name: 'Cancel'}))
  expect(screen.getByRole('button', {name: PUT_BACK})).toBeEnabled()
  expect(screen.queryByRole('alert')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  fetch.mockResolvedValueOnce(answer(receipt()))
  fireEvent.click(await screen.findByRole('button', {name: 'Put back anyway'}))
  await screen.findByRole('button', {name: 'Undo putting back Playground/game'})
  expect(sent(fetch)).toEqual([{chat_id: 'chat-1', project: 'Playground/game', snapshot: RUN},
    {chat_id: 'chat-1', project: 'Playground/game', snapshot: RUN},
    {chat_id: 'chat-1', project: 'Playground/game', snapshot: RUN, confirm: TOKEN}])
})

test('nothing to put back is said calmly; what a copy could not hold is never called a match', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => answer(receipt({status: 'unchanged', written: 0, removed: 0, undo: null}))))
  const {unmount} = render(<PortalTurnUndo chatId="chat-1" entries={[entry()]}/>)
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  expect(await screen.findByText('Playground/game already matches how it was before this turn, so nothing changed.')).toBeVisible()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('button', {name: /Undo/})).toBeNull()
  unmount()
  vi.stubGlobal('fetch', vi.fn(async () => answer(receipt({status: 'unchanged', written: 0, removed: 0, undo: null,
    left: ['assets/song.wav'], leftCount: 1}))))
  render(<PortalTurnUndo chatId="chat-1" entries={[entry()]}/>)
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  const status = await screen.findByText(/^Nothing in Playground\/game was changed\./)
  expect(status).toHaveTextContent("ODS left assets/song.wav as it is, because its copies could not hold it (a link, a very large file, or past a copy's limits).")
  expect(screen.queryByText(/already matches/)).toBeNull()
  expect(screen.getByRole('link', {name: 'Get help on Discord'})).toHaveAttribute('href', ODS_HELP_DISCORD_URL)
})

test('files that could not be changed are named with their cause, and Try again appears only where it can work', async () => {
  const cases = [
    ['in-use', 'chars.js could not be changed because a program is using it. Close it, then try again.', true, false],
    ['changed', 'chars.js and a.js changed while ODS was working, so they were left as they are. Try again to finish.', true, false],
    ['permission', 'ODS is not allowed to change chars.js, so it was left as it is.', false, true],
    ['other', 'chars.js could not be changed.', true, true],
  ]
  for (const [failure, text, retry, help] of cases) {
    const failed = failure === 'changed' ? ['chars.js', 'a.js'] : ['chars.js']
    const fetch = vi.fn(async () => answer(receipt({failed, failedCount: failed.length, failure})))
    vi.stubGlobal('fetch', fetch)
    const {unmount} = render(<PortalTurnUndo chatId="chat-1" entries={[entry()]}/>)
    fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(Boolean(screen.queryByRole('button', {name: 'Try again'}))).toBe(retry)
    expect(Boolean(screen.queryByRole('link'))).toBe(help)
    // The part that was put back can still be undone.
    expect(screen.getByRole('button', {name: 'Undo putting back Playground/game'})).toBeEnabled()
    if (retry) {
      fireEvent.click(screen.getByRole('button', {name: 'Try again'}))
      await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
      expect(sent(fetch)[1].snapshot).toBe(RUN)
    }
    unmount()
  }
})

test('a copy made after the turn had already changed files says so', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => answer(receipt({late: true}))))
  render(<PortalTurnUndo chatId="chat-1" entries={[entry({late: true})]}/>)
  const late = 'ODS made this copy after the turn had already changed some files in Playground/game, so putting it back may not undo every change.'
  expect(screen.getByText(late)).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  expect(await screen.findByRole('status')).toHaveTextContent(late)
})

test('each refusal says why nothing changed; a copy that is gone takes its button with it', async () => {
  const cases = [
    [refusal('project-restore-busy', 409), 'Portal is still working on a reply or a command it started, in this chat or another. Nothing was changed; try again once it finishes.', false, false, true],
    [refusal('project-snapshot-pruned', 404), 'ODS no longer keeps the copy of Playground/game from before this turn (it keeps the latest few), so nothing was changed.', false, false, false],
    [refusal('project-snapshot-unavailable', 404), 'ODS did not keep the copy of Playground/game from before this turn, so nothing was changed.', false, false, false],
    [refusal('project-snapshot-damaged', 422), 'The copy of Playground/game from before this turn is damaged, so nothing was changed.', true, true, false],
    [refusal('project-folder-unusable', 422), 'Playground/game is not a folder ODS can use (it may be a link or a file), so nothing was changed.', true, true, true],
    [refusal('project-copies-unusable', 503), 'ODS cannot use the folder where it keeps project copies (its owner or permissions changed), so nothing was changed.', true, true, false],
    [refusal('project-restore-unsupported', 503), 'This ODS install cannot put project files back, so nothing was changed.', true, true, false],
    [refusal('project-restore-unavailable', 503), 'Portal could not confirm whether the files were put back. Check Playground/game before you try again.', true, true, true],
    [answer({detail: 'Not Found'}, 404), 'Portal could not confirm whether the files were put back. Check Playground/game before you try again.', true, true, true],
    [answer(receipt({project: 'Playground/other'})), 'Portal could not confirm whether the files were put back. Check Playground/game before you try again.', true, true, true],
  ]
  for (const [reply, text, alert, help, stays] of cases) {
    vi.stubGlobal('fetch', vi.fn(async () => reply))
    const {unmount} = render(<PortalTurnUndo chatId="chat-1" entries={[entry()]}/>)
    fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
    expect(await screen.findByText(text)).toBeVisible()
    expect(Boolean(screen.queryByRole('alert'))).toBe(alert)
    expect(Boolean(screen.queryByRole('link', {name: 'Get help on Discord'}))).toBe(help)
    expect(Boolean(screen.queryByRole('button', {name: PUT_BACK}))).toBe(stays)
    unmount()
  }
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network down') }))
  render(<PortalTurnUndo chatId="chat-1" entries={[entry()]}/>)
  fireEvent.click(screen.getByRole('button', {name: PUT_BACK}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal could not confirm whether the files were put back.')
})

test('the buttons wait while Portal works, and several projects are named', () => {
  render(<PortalTurnUndo chatId="chat-1" busy entries={[entry(), entry({project: 'Playground/notes'})]}/>)
  for (const name of ['Put back Playground/game from before this turn', 'Put back Playground/notes from before this turn']) {
    const button = screen.getByRole('button', {name})
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', 'Portal is still working')
  }
})

test('without a chat or an entry from the host there is nothing to offer', () => {
  const {container} = render(<PortalTurnUndo chatId="" entries={[entry()]}/>)
  expect(container).toBeEmptyDOMElement()
  render(<PortalTurnUndo chatId="chat-1" entries={[]}/>)
  expect(screen.queryByRole('button')).toBeNull()
})

test('the client accepts only exact options and receipts', async () => {
  const messages = [{role: 'user', content: 'x'}, {role: 'assistant', task: {runId: RUN}}, {role: 'assistant', task: {runId: 'cron-1'}},
    {role: 'assistant', status: 'streaming', task: {runId: 'chatcmpl_00000000-2222-4333-8444-555555555555'}}, {role: 'assistant', task: {runId: RUN}}]
  expect(finishedRuns(messages)).toEqual([RUN])
  vi.stubGlobal('fetch', vi.fn(async () => answer({schemaVersion: 1, projects: [entry({restore: {undo: UNDO, side: 'before'}})]})))
  expect(await restoreOptions([RUN])).toEqual([entry({restore: {undo: UNDO, side: 'before'}})])
  for (const projects of [[entry({snapshot: UNDO})], [entry({restore: {undo: RUN, side: 'before'}})], [entry({extra: 1})],
    [entry({project: 'Playground/a/b'})], [entry({restore: {undo: UNDO, side: 'sideways'}})]]) {
    vi.stubGlobal('fetch', vi.fn(async () => answer({schemaVersion: 1, projects})))
    await expect(restoreOptions([RUN])).rejects.toThrow()
  }
  vi.stubGlobal('fetch', vi.fn(async () => answer({detail: {code: 'project-restore-unavailable'}}, 503)))
  await expect(restoreOptions([RUN])).rejects.toThrow()
  for (const bad of [receipt({schemaVersion: 1}), receipt({undo: null}), receipt({status: 'unchanged'}), receipt({left: ['a'], leftCount: 0}),
    receipt({failure: 'disk'}), receipt({confirm: {token: TOKEN, overwrite: [], overwriteCount: 0, remove: [], removeCount: 0}})]) {
    vi.stubGlobal('fetch', vi.fn(async () => answer(bad)))
    await expect(restoreProject({chatId: 'chat-1', project: 'Playground/game', snapshot: RUN})).rejects.toMatchObject({code: 'project-restore-unavailable'})
  }
  vi.stubGlobal('fetch', vi.fn(async () => refusal('project-snapshot-pruned', 404)))
  await expect(restoreProject({chatId: 'chat-1', project: 'Playground/game', snapshot: RUN})).rejects.toMatchObject({code: 'project-snapshot-pruned'})
})
