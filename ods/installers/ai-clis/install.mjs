#!/usr/bin/env node
// Install reviewed optional tools without global npm resolution or lifecycle scripts.
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const sourceDirectory = path.dirname(fileURLToPath(import.meta.url))

export function validateLock(manifest, lock) {
  if (lock.lockfileVersion !== 3) throw new Error('Unsupported CLI lockfile format')
  for (const [name, version] of Object.entries(manifest.dependencies)) {
    if (!/^\d+\.\d+\.\d+$/.test(version) || lock.packages?.[`node_modules/${name}`]?.version !== version) {
      throw new Error(`CLI dependency is not pinned: ${name}`)
    }
  }
  for (const [name, entry] of Object.entries(lock.packages)) {
    if (!name) continue
    if (!entry.version || !entry.resolved?.startsWith('https://registry.npmjs.org/') ||
        !/^sha512-[A-Za-z0-9+/]+={0,2}$/.test(entry.integrity || '') || entry.link) {
      throw new Error(`CLI dependency lacks reviewed registry integrity: ${name}`)
    }
  }
}

function findNpmCli() {
  const candidates = [path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js')]
  for (const directory of (process.env.PATH || '').split(path.delimiter)) {
    if (!directory) continue
    candidates.push(path.join(directory, 'node_modules/npm/bin/npm-cli.js'))
    try { candidates.push(fs.realpathSync(path.join(directory, 'npm'))) } catch { /* absent */ }
  }
  const found = candidates.find(candidate => candidate.endsWith('npm-cli.js') && fs.existsSync(candidate))
  if (!found) throw new Error('A Node.js 22+ installation with npm is required')
  return found
}

export function installOptionalClis({
  home = os.homedir(), env = process.env, platform = process.platform,
  architecture = process.arch, manifestDirectory = sourceDirectory,
  npmCli, run = spawnSync,
} = {}) {
  if (env.ODS_INSTALL_AI_CLIS !== 'true') return { skipped: true }
  if (Number(process.versions.node.split('.')[0]) < 22) throw new Error('Optional AI CLIs require Node.js 22+')
  if (!['linux', 'darwin', 'win32'].includes(platform) || !['x64', 'arm64'].includes(architecture)) {
    throw new Error('No reviewed AI CLI packages exist for this platform')
  }
  if (process.platform === 'darwin' && architecture === 'x64' &&
      spawnSync('/usr/sbin/sysctl', ['-n', 'sysctl.proc_translated'], { encoding: 'utf8' }).stdout?.trim() === '1') {
    throw new Error('Use native ARM64 Node.js on Apple Silicon to install the reviewed native CLI packages')
  }
  const manifestBytes = fs.readFileSync(path.join(manifestDirectory, 'package.json'))
  const lockBytes = fs.readFileSync(path.join(manifestDirectory, 'package-lock.json'))
  const manifest = JSON.parse(manifestBytes)
  const lock = JSON.parse(lockBytes)
  validateLock(manifest, lock)
  const lockSha256 = createHash('sha256').update(lockBytes).digest('hex')
  const root = path.resolve(home, '.ods', 'ai-clis')
  // npm ci removes node_modules. Use a fresh private directory on each attempt
  // and publish launchers only after success, preserving any earlier install.
  for (const directory of [path.join(home, '.ods'), root, path.join(root, 'bin')]) {
    if (fs.existsSync(directory) && fs.lstatSync(directory).isSymbolicLink()) {
      throw new Error('Refusing a linked AI CLI installation directory')
    }
  }
  fs.mkdirSync(root, { recursive: true, mode: 0o700 })
  const target = fs.mkdtempSync(path.join(root, `${lockSha256}-`))
  fs.writeFileSync(path.join(target, 'package.json'), manifestBytes)
  fs.writeFileSync(path.join(target, 'package-lock.json'), lockBytes)
  const result = run(process.execPath, [npmCli || findNpmCli(), 'ci', '--prefix', target,
    '--ignore-scripts', '--include=optional', '--no-audit', '--no-fund',
    '--registry=https://registry.npmjs.org'], { stdio: 'inherit', env })
  if (result.error || result.status !== 0) throw new Error('Locked AI CLI installation failed')
  const musl = platform === 'linux' && !process.report.getReport().header.glibcVersionRuntime
  const nativePackage = `@anthropic-ai/claude-code-${platform}-${architecture}${musl ? '-musl' : ''}`
  const claude = path.join(target, 'node_modules', nativePackage, platform === 'win32' ? 'claude.exe' : 'claude')
  const codex = path.join(target, 'node_modules/@openai/codex/bin/codex.js')
  const codexNative = path.join(target, `node_modules/@openai/codex-${platform}-${architecture}/package.json`)
  for (const filename of [claude, codex, codexNative]) {
    if (!fs.statSync(filename).isFile()) throw new Error('A locked platform binary is missing')
  }
  // Claude's reviewed 2.1.280 wrapper only copies this optional-package binary.
  // Launch it directly, so no dependency postinstall or fallback download runs.
  if (platform !== 'win32') fs.chmodSync(claude, 0o755)
  const bin = path.join(root, 'bin')
  fs.mkdirSync(bin, { recursive: true, mode: 0o700 })
  for (const [name, executable, prefix] of [['claude', claude, []], ['codex', process.execPath, [codex]]]) {
    const launcher = `#!/usr/bin/env node\nconst {spawnSync}=require('node:child_process');\n` +
      `const result=spawnSync(${JSON.stringify(executable)},[...${JSON.stringify(prefix)},...process.argv.slice(2)],{stdio:'inherit'});\n` +
      `if(result.error) console.error(result.error.message);\nprocess.exit(result.status ?? 1);\n`
    fs.writeFileSync(path.join(bin, platform === 'win32' ? `${name}.cjs` : name), launcher, { mode: 0o755 })
    if (platform === 'win32') fs.writeFileSync(path.join(bin, `${name}.cmd`), `@echo off\r\nnode "%~dp0${name}.cjs" %*\r\n`)
  }
  const receipt = { lockSha256, dependencies: manifest.dependencies, integrity: Object.fromEntries(
    Object.entries(lock.packages).filter(([name]) => name).map(([name, entry]) => [name, entry.integrity])) }
  fs.writeFileSync(path.join(target, 'install-receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o600 })
  return { bin, receipt: path.join(target, 'install-receipt.json') }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = installOptionalClis()
    console.log(result.skipped ? 'Optional AI CLIs disabled; set ODS_INSTALL_AI_CLIS=true to opt in.' :
      `Optional AI CLIs installed. PATH directory: ${result.bin}\nIntegrity receipt: ${result.receipt}`)
  } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}
