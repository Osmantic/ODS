import { cleanup, fireEvent, screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import Pixel from './Pixel'
import { CHAT_KEY } from '../lib/pixelConversations'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: true, json: async () => ({available: true})})))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function restoreReply(content) {
  localStorage.setItem(CHAT_KEY, JSON.stringify({
    schema: 1, chatId: 'image-history', messages: [{role: 'assistant', content}],
    draft: '', inFlight: false, interrupted: false,
  }))
  return render(<Pixel />)
}

test('restored reply images remain inert until individually requested', async () => {
  const {container, unmount} = restoreReply('![First plot](https://images.example/one.png)\n\n![Second plot](https://images.example/two.png)')
  await screen.findByText('Available')
  expect(container.querySelector('img[src^="https://images.example"]')).toBeNull()
  fireEvent.click(screen.getAllByRole('button', {name: 'Load image from images.example'})[0])
  expect(screen.getByRole('img', {name: 'First plot'})).toHaveAttribute('src', 'https://images.example/one.png')
  expect(screen.getByRole('img', {name: 'First plot'})).toHaveAttribute('referrerpolicy', 'no-referrer')
  expect(screen.queryByRole('img', {name: 'Second plot'})).not.toBeInTheDocument()
  unmount()
  const restored = render(<Pixel />)
  await screen.findByText('Available')
  expect(restored.container.querySelector('img[src^="https://images.example"]')).toBeNull()
})

test('linked images have separate load and navigation controls', async () => {
  const {container} = restoreReply('[![Chart](https://images.example/chart.png)](https://source.example/report)')
  await screen.findByText('Available')
  const load = screen.getByRole('button', {name: 'Load image from images.example'})
  expect(load.closest('a')).toBeNull()
  expect(screen.getByRole('link', {name: 'Open image link'})).toHaveAttribute('href', 'https://source.example/report')
  expect(container.querySelector('a a')).toBeNull()
  fireEvent.click(load)
  fireEvent.error(screen.getByRole('img', {name: 'Chart'}))
  expect(screen.getByText('Image could not be loaded.')).toBeInTheDocument()
  expect(screen.queryByRole('img', {name: 'Chart'})).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', {name: 'Load image from images.example'}))
  expect(screen.getByRole('img', {name: 'Chart'})).toBeInTheDocument()
})

test.each(['/api/local-image', '//images.example/chart.png', 'javascript:alert(1)'])('unsupported image source %s cannot trigger a load', async src => {
  const {container} = restoreReply(`![Unavailable chart](${src})`)
  await screen.findByText('Available')
  expect(container.querySelector('img')).toBeNull()
  expect(screen.queryByRole('button', {name: /Load image from/})).not.toBeInTheDocument()
  expect(screen.getByText('Unavailable chart')).toBeInTheDocument()
})
