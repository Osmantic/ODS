import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { installOptionalClis, validateLock } from './install.mjs'

const directory = path.dirname(fileURLToPath(import.meta.url))
const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'package.json')))
const lock = JSON.parse(fs.readFileSync(path.join(directory, 'package-lock.json')))
function temporaryHome(t) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-cli-test-'))
  t.after(() => {
    assert.ok(path.resolve(home).startsWith(path.join(path.resolve(os.tmpdir()), 'ods-cli-test-')))
    fs.rmSync(home, { recursive: true, force: true })
  })
  return home
}

test('all direct and platform dependencies have registry integrity and exact versions', () => {
  validateLock(manifest, lock)
  for (const name of ['@anthropic-ai/claude-code', '@openai/codex']) {
    const optional = lock.packages[`node_modules/${name}`].optionalDependencies
    assert.ok(Object.keys(optional).length >= 6)
    for (const dependency of Object.keys(optional)) assert.ok(lock.packages[`node_modules/${dependency}`].integrity)
  }
})

test('unreviewed tarball URLs or absent integrity are rejected', () => {
  for (const patch of [{ resolved: 'https://example.invalid/package.tgz' }, { integrity: '' }]) {
    const edited = structuredClone(lock)
    Object.assign(edited.packages['node_modules/@openai/codex'], patch)
    assert.throws(() => validateLock(manifest, edited), /reviewed registry integrity/)
  }
})

test('missing or nonexact opt-in does not create files or invoke npm', t => {
  const home = temporaryHome(t)
  for (const flag of [undefined, 'false', 'TRUE', '1']) {
    const result = installOptionalClis({ home, env: { ODS_INSTALL_AI_CLIS: flag }, run() { assert.fail('npm invoked') } })
    assert.equal(result.skipped, true)
  }
  assert.deepEqual(fs.readdirSync(home), [])
})

test('npm integrity failure never publishes command launchers', t => {
  const home = temporaryHome(t)
  assert.throws(() => installOptionalClis({ home, env: { ODS_INSTALL_AI_CLIS: 'true' },
    npmCli: '/fixture/npm-cli.js', run() { return { status: 1 } },
  }), /Locked AI CLI installation failed/)
  assert.equal(fs.existsSync(path.join(home, '.ods/ai-clis/bin')), false)
})

test('missing platform binary fails closed after npm succeeds', t => {
  const home = temporaryHome(t)
  assert.throws(() => installOptionalClis({ home, env: { ODS_INSTALL_AI_CLIS: 'true' },
    npmCli: '/fixture/npm-cli.js', run() { return { status: 0 } },
  }), /ENOENT|platform binary/)
  assert.equal(fs.existsSync(path.join(home, '.ods/ai-clis/bin')), false)
})

for (const platform of ['linux', 'darwin', 'win32']) {
  test(`${platform} fixture install uses npm ci without lifecycle scripts and records integrity`, t => {
    const home = temporaryHome(t)
    const result = installOptionalClis({ home, platform, architecture: 'x64',
      env: { ODS_INSTALL_AI_CLIS: 'true' }, npmCli: '/fixture/npm-cli.js',
      run(command, args) {
        assert.equal(command, process.execPath)
        assert.ok(args.includes('ci'))
        assert.ok(args.includes('--ignore-scripts'))
        assert.ok(args.includes('--include=optional'))
        assert.ok(args.includes('--registry=https://registry.npmjs.org'))
        const target = args[args.indexOf('--prefix') + 1]
        const musl = platform === 'linux' && !process.report.getReport().header.glibcVersionRuntime
        const files = [
          `node_modules/@anthropic-ai/claude-code-${platform}-x64${musl ? '-musl' : ''}/${platform === 'win32' ? 'claude.exe' : 'claude'}`,
          'node_modules/@openai/codex/bin/codex.js',
          `node_modules/@openai/codex-${platform}-x64/package.json`,
        ]
        for (const file of files) {
          const destination = path.join(target, file)
          fs.mkdirSync(path.dirname(destination), { recursive: true })
          fs.writeFileSync(destination, 'test fixture, never executed')
        }
        return { status: 0 }
      },
    })
    const receipt = JSON.parse(fs.readFileSync(result.receipt))
    assert.deepEqual(receipt.dependencies, manifest.dependencies)
    assert.equal(receipt.integrity['node_modules/@openai/codex'], lock.packages['node_modules/@openai/codex'].integrity)
    for (const name of ['claude', 'codex']) assert.ok(fs.existsSync(path.join(result.bin, `${name}${platform === 'win32' ? '.cmd' : ''}`)))
  })
}
