import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { activityProgressRows } from './portalActivityProgress'

// Portal's own UI text is English. Text the owner sends in another language is
// answered in that language by Portal, but the dashboard never writes Portuguese
// for them, whatever the browser locale.
const src = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// Portuguese letters, or common Portuguese words, unaccented UI words included.
// Words that are also English (qual, UMA) are left out. JavaScript's \b only
// knows ASCII letters, so it cannot end a word on an accented one: the
// boundaries here are Unicode letters and digits. Same detector as
// pixel-agent/tests/english_default.test.mjs.
const PORTUGUESE_WORDS = ['não', 'nao', 'você', 'voce', 'fontes?', 'arquivos?', 'verificação', 'verificada', 'consulte',
  'pesquise', 'pesquisando', 'crie', 'publique', 'publicar', 'inspecione', 'quais', 'resultados', 'execução',
  'leitura', 'tarefa', 'ação', 'ações', 'recuperação', 'tentativas', 'bloqueou', 'remoção', 'prévia', 'projeto', 'agentes',
  'seu', 'sua', 'está', 'são', 'escreva', 'responda', 'resposta', 'idioma', 'pedido', 'portugues', 'carregando',
  'aguarde', 'concluido', 'erro', 'salvar', 'enviar', 'cancelar', 'tente novamente']
const PORTUGUESE = new RegExp(`[ãõçáéíóúâêôàÃÕÇÁÉÍÓÚÂÊÔÀ]|(?<![\\p{L}\\p{N}])(?:${PORTUGUESE_WORDS.join('|')})(?![\\p{L}\\p{N}])`, 'iu')

// Owner-input aliases: they read what the owner typed and never become output.
const INPUT_ALIASES = {
  'lib/portalTeams.js': ['(?:agents|agentes)'],
}
function sources(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) sources(path, out)
    else if (/\.jsx?$/.test(name) && !/\.test\.jsx?$/.test(name)) out.push(path)
  }
  return out
}

describe('dashboard UI text', () => {
  it('detects accented and unaccented Portuguese, and no ordinary English', () => {
    for (const text of ['Página indisponível', 'Concluído', 'Olá! Tudo pronto.', 'O site está pronto.', 'Pesquisando na web',
      'Publicar o site', 'Carregando...', 'Aguarde', 'Concluido', 'Erro de rede', 'Salvar', 'Enviar', 'Cancelar',
      'Tente novamente', 'Escreva no idioma do pedido.']) expect(text).toMatch(PORTUGUESE)
    for (const text of ['Loading...', 'Please wait', 'Completed', 'Error: try again', 'errors', 'Save', 'Send', 'Cancel',
      'Cancelled', 'Inspecting available files and information.', 'Publishing the project preview.',
      'Coordinating work across agents.', 'Summarize the qual results from the last bench run.',
      'Set the UMA frame buffer to 512 MB.']) expect(text).not.toMatch(PORTUGUESE)
    // A listed word inside a longer word is no match, also next to a letter
    // outside ASCII. With \b these made-up words would match, because \b
    // treats ñ and ü as word breaks.
    for (const text of ['Señerro', 'Grüseu']) expect(text).not.toMatch(PORTUGUESE)
  })

  it('has no Portuguese outside owner-input aliases', () => {
    const unexpected = [], used = new Set()
    for (const file of sources(src)) {
      // Dashboard CI also runs on Windows: compare forward-slash paths.
      const name = relative(src, file).split('\\').join('/')
      readFileSync(file, 'utf8').split('\n').forEach((line, index) => {
        if (!PORTUGUESE.test(line)) return
        const alias = (INPUT_ALIASES[name] ?? []).find((fragment) => line.includes(fragment))
        if (alias) { used.add(`${name}:${alias}`); return }
        unexpected.push(`${name}:${index + 1}: ${line.trim().slice(0, 120)}`)
      })
    }
    expect(unexpected).toEqual([])
    const stale = Object.entries(INPUT_ALIASES).flatMap(([name, fragments]) =>
      fragments.filter((fragment) => !used.has(`${name}:${fragment}`)).map((fragment) => `${name}:${fragment}`))
    expect(stale).toEqual([])
  })

  it('labels activity progress in English on every browser locale', () => {
    // The component used to pass navigator.language; a Portuguese browser must
    // still get English labels.
    const events = [{ sequence: 1, kind: 'read', state: 'running' }]
    for (const locale of [undefined, 'en-US', 'pt-BR', 'pt']) {
      expect(activityProgressRows(events, locale)[0].text).toBe('Inspecting available files and information.')
    }
  })
})
