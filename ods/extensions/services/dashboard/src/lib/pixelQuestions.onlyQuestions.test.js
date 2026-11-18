import { onlyQuestions } from './pixelQuestions'

const CARD = [{ id: 'memory_save', question: 'Remember this about you? "The owner is vegetarian."', options: ['Save', "Don't save", 'Always save without asking'] }]

it('tells a reply that only writes out its card from one that answers beside it', () => {
  expect(onlyQuestions('1. Remember this about you? "The owner is vegetarian."\n- Save\n- Don\'t save\n- Always save without asking', CARD)).toBe(true)
  expect(onlyQuestions('Remember this about you? "The owner is vegetarian."', CARD)).toBe(true)
  expect(onlyQuestions('', CARD)).toBe(true)
  expect(onlyQuestions('Try a chickpea curry tonight.', CARD)).toBe(false)
  expect(onlyQuestions('Try a chickpea curry.\n\nRemember this about you? "The owner is vegetarian."', CARD)).toBe(false)
  expect(onlyQuestions(undefined, CARD)).toBe(true)
})
