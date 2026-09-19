import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TemplatePicker } from '../TemplatePicker' // eslint-disable-line no-unused-vars

const templates = [{ id: 'workflows', name: 'Workflows', services: ['n8n'] }]
const preview = { ok: true, json: async () => ({ changes: { to_enable: ['n8n'] } }) }
afterEach(() => vi.unstubAllGlobals())

async function open() {
  const user = userEvent.setup()
  render(<><button>Background action</button><TemplatePicker templates={templates}/><button>After templates</button></>)
  const trigger = screen.getByRole('button', { name: /Workflows/ })
  trigger.focus()
  await user.keyboard('{Enter}')
  await screen.findByRole('button', { name: 'Apply Template' })
  return { user, trigger, dialog: screen.getByRole('dialog') }
}

test('keyboard opening moves focus into the preview and Escape returns to its card', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => preview))
  const { user, trigger, dialog } = await open()
  expect(dialog.contains(document.activeElement)).toBe(true)
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(trigger).toHaveFocus()
})

test('Tab and Shift+Tab stay within the current enabled dialog controls', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => preview))
  const { user, dialog } = await open()
  const buttons = within(dialog).getAllByRole('button')
  buttons.at(-1).focus()
  await user.tab()
  expect(buttons[0]).toHaveFocus()
  await user.tab({ shift: true })
  expect(buttons.at(-1)).toHaveFocus()
  screen.getByRole('button', { name: 'Background action' }).focus()
  expect(dialog.contains(document.activeElement)).toBe(true)
})

test.each([true, false])('pending apply keeps focus and blocks Escape until settlement (success=%s)', async success => {
  let finish
  vi.stubGlobal('fetch', vi.fn(async url => url.endsWith('/preview') ? preview
    : new Promise(resolve => { finish = resolve })))
  const { user, trigger, dialog } = await open()
  await user.click(screen.getByRole('button', { name: 'Apply Template' }))
  await user.tab()
  expect(dialog.contains(document.activeElement)).toBe(true)
  await user.keyboard('{Escape}')
  expect(screen.getByRole('dialog')).toBe(dialog)
  await act(async () => finish({ ok: success, status: success ? 200 : 503,
    json: async () => ({ enabled_count: 1 }) }))
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(trigger).toHaveFocus()
})

test('Cancel restores focus and unmount removes focus containment', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => preview))
  const { user, trigger } = await open()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(trigger).toHaveFocus()
  const outside = screen.getByRole('button', { name: 'Background action' })
  outside.focus()
  expect(outside).toHaveFocus()
})

test('returns to another available card when refresh disables the original card', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => preview))
  const user = userEvent.setup()
  const other = { id: 'chat', name: 'Chat', services: ['open-webui'] }
  const view = render(<TemplatePicker templates={[...templates, other]}/>)
  const trigger = screen.getByRole('button', { name: /Workflows/ })
  trigger.focus()
  await user.keyboard('{Enter}')
  await screen.findByRole('button', { name: 'Apply Template' })
  view.rerender(<TemplatePicker templates={[{ ...templates[0], _status: 'applied' }, other]}/>)
  await user.keyboard('{Escape}')
  expect(screen.getByRole('button', { name: /Chat/ })).toHaveFocus()
})
