import {exampleProjectRows, openExampleProject, readExampleProjects} from './exampleProjects'

const json = (status, body) => ({ok: status >= 200 && status < 300, status, json: async () => body})
afterEach(() => vi.unstubAllGlobals())

test('lists only well-formed example folder names from the host', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(200, {schemaVersion: 1, projects: ['elf-of-doom', '../x', 7, 'elf-of-doom']})))
  expect(await readExampleProjects()).toEqual(['elf-of-doom'])
  expect(fetch).toHaveBeenCalledWith('/api/pixel/example-projects', expect.objectContaining({cache: 'no-store'}))
  for (const reply of [json(503, {detail: 'down'}), json(200, {schemaVersion: 2, projects: ['elf-of-doom']}), json(200, null),
    json(200, {schemaVersion: 1, projects: Array.from({length: 9}, (_, i) => `p${i}`)})]) {
    fetch.mockResolvedValueOnce(reply)
    expect(await readExampleProjects()).toEqual([])
  }
})

test('opening posts only the folder name and reports a deleted folder separately', async () => {
  const preview = {schemaVersion: 1, relativeDirectory: 'Playground/elf-of-doom'}
  vi.stubGlobal('fetch', vi.fn(async () => json(200, {schemaVersion: 1, preview})))
  expect(await openExampleProject('elf-of-doom')).toBe(preview)
  const [url, options] = fetch.mock.calls[0]
  expect(url).toBe('/api/pixel/example-projects/open')
  expect(options.method).toBe('POST')
  expect(JSON.parse(options.body)).toEqual({name: 'elf-of-doom'})
  fetch.mockResolvedValueOnce(json(404, {detail: 'gone'}))
  await expect(openExampleProject('elf-of-doom')).rejects.toMatchObject({code: 'missing'})
  fetch.mockResolvedValueOnce(json(503, {detail: 'down'}))
  await expect(openExampleProject('elf-of-doom')).rejects.toMatchObject({code: 'unavailable'})
  fetch.mockResolvedValueOnce(json(200, {schemaVersion: 1}))
  await expect(openExampleProject('elf-of-doom')).rejects.toMatchObject({code: 'unavailable'})
})

test('an example that already has conversations is that project, never a second row', () => {
  const own = {root: 'Playground', name: 'elf-of-doom', path: 'Playground/elf-of-doom', chats: [{chatId: 'c1'}]}
  expect(exampleProjectRows(['elf-of-doom'], [own])).toEqual([])
  expect(exampleProjectRows(['elf-of-doom'], [])).toEqual([
    {root: 'Playground', name: 'elf-of-doom', path: 'Playground/elf-of-doom', chats: [], example: true}])
})
