import { catalogInstallCommand } from './catalogInstallCommand'

test.each([
  'ola, instale pra mim /extensions @apache-answer',
  'Ol\u00e1, instale para mim /extensions @Apache-Answer.',
  'por favor instale /extensions @apache-answer',
  'Hi, please install /extensions @apache-answer',
])('routes an explicit catalog installation: %s', value => {
  expect(catalogInstallCommand(value)).toBe('/extensions @apache-answer')
})

test.each([
  'explique /extensions @apache-answer',
  'nao instale /extensions @apache-answer',
  'do not install /extensions @apache-answer',
  'se eu pedir instale /extensions @apache-answer',
  'instale /extensions @apache-answer ou outra',
  'instale /extensions @apache-answer @other',
  'instale /extensions @apache-answer; remove other',
  'instale /extensions @apache-answer\nremove other',
  '"instale /extensions @apache-answer"',
  'ola, instale pra mim /extensions @excalibur draw algo assim',
  '/extensions @apache-answer',
])('does not infer authority or rewrite other requests: %s', value => {
  expect(catalogInstallCommand(value)).toBe(value)
})
