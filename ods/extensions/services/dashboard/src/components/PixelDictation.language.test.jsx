import {act, fireEvent, render, screen} from '@testing-library/react'
import PixelDictation from './PixelDictation'

let instances
beforeEach(() => {
  instances = []
  window.SpeechRecognition = function () {
    const active = {start:vi.fn(), stop:vi.fn(), abort:vi.fn()}
    instances.push(active)
    return active
  }
})
afterEach(() => { delete window.SpeechRecognition })
function selectLanguage(value) {
  fireEvent.click(screen.getByRole('button', {name:'Dictation language'}))
  fireEvent.change(screen.getByRole('combobox', {name:'Spoken language'}), {target:{value}})
}
test('uses an explicit spoken language without recording until the microphone is clicked', () => {
  const insert = vi.fn()
  render(<PixelDictation onInsert={insert}/>)
  selectLanguage('vi-VN')
  expect(instances).toHaveLength(0)
  expect(insert).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name:'Dictate message'}))
  expect(instances[0].lang).toBe('vi-VN')
  expect(instances[0].start).toHaveBeenCalledOnce()
  act(() => instances[0].onresult({results:[Object.assign([{transcript:'Xin chào'}], {isFinal:true})]}))
  expect(insert).toHaveBeenCalledExactlyOnceWith('Xin chào ')
})
test('locks language for an active and finishing recording, then allows changing the next one', () => {
  render(<PixelDictation onInsert={vi.fn()}/>)
  selectLanguage('ja-JP')
  const options = screen.getByRole('button', {name:'Dictation language'})
  fireEvent.click(screen.getByRole('button', {name:'Dictate message'}))
  expect(options).toBeDisabled()
  fireEvent.click(screen.getByRole('button', {name:'Stop dictation'}))
  expect(options).toBeDisabled()
  act(() => instances[0].onend())
  expect(options).toBeEnabled()
  selectLanguage('')
  fireEvent.click(screen.getByRole('button', {name:'Dictate message'}))
  expect(instances[1].lang).toBe(navigator.language || 'en-US')
})
test('retains the tab choice across conversation changes while discarding the old transcript', () => {
  const insert = vi.fn()
  const view = render(<PixelDictation conversationId="one" onInsert={insert}/>)
  selectLanguage('fr-FR')
  fireEvent.click(screen.getByRole('button', {name:'Dictate message'}))
  view.rerender(<PixelDictation conversationId="two" onInsert={insert}/>)
  act(() => instances[0].onresult({results:[[{transcript:'stale'}]]}))
  expect(instances[0].abort).toHaveBeenCalledOnce()
  expect(insert).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name:'Dictation language'}))
  fireEvent.click(screen.getByRole('button', {name:'Close language options'}))
  fireEvent.click(screen.getByRole('button', {name:'Dictate message'}))
  expect(instances[1].lang).toBe('fr-FR')
})
