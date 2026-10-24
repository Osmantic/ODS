import { deliveredArtifactDisplayText, deliveredArtifactForDownloadHref, deliveredArtifactForHref, deliveredArtifactMetadata, parseDeliveredArtifacts, parseDeliveredArtifactsFrame } from './pixelDeliveredArtifacts'

const receipt = () => ({schemaVersion:1, kind:'ods-pixel-workspace-artifact', relativePath:'Playground/project/report.md',
  siteId:`site-${'a'.repeat(24)}`, sha256:'a'.repeat(64), file:{path:'report.md', bytes:12, sha256:'b'.repeat(64)}})
const frame = artifacts => ({choices:[{finish_reason:'stop'}],pixel_artifacts:{schemaVersion:1,artifacts}})

it('resolves a download-labelled filename only to one completed receipt',()=>{
  const source=receipt(),href='https://untrusted.example/s/report.md'
  expect(deliveredArtifactForDownloadHref(href,[source])).toEqual(source)
  expect(deliveredArtifactForHref(href,[source])).toBeNull('ordinary source links keep their stricter snapshot binding')
  const other={...source,relativePath:'Other/report.md',siteId:`site-${'c'.repeat(24)}`,sha256:'c'.repeat(64)}
  for(const artifacts of [null,[],[source,other],[{...source,file:{...source.file,sha256:'bad'}}]]) {
    expect(deliveredArtifactForDownloadHref(href,artifacts)).toBeNull()
  }
})

it.each([
  'https://untrusted.example/s/other.md','https://untrusted.example/s/report.md?x=1',
  'https://untrusted.example/s/report.md#copy','https://user:pass@untrusted.example/s/report.md',
  'https://untrusted.example/a/../s/report.md','https://untrusted.example/a/%2e%2e/s/report.md',
  'https://untrusted.example/s/%72eport.md','https://untrusted.example/s\\report.md',
  'file:///s/report.md','javascript:report.md',
])('never binds a download to malformed or unmatched URL %s',href=>{
  expect(deliveredArtifactForDownloadHref(href,[receipt()])).toBeNull()
})

it('resolves the observed invented host only through its exact delivered snapshot and file', () => {
  const source = {schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'fleet-current-main-20261017/welcome.md',
    siteId:'site-59a5b7d9dcc69d70aad30310',sha256:'59a5b7d9dcc69d70aad303105bdd065a05cebd0d3849a845cbd231e50e797e07',
    file:{path:'welcome.md',bytes:67,sha256:'28a58d9ddc4c50bd490b22b8e9aa79b02325bbfd018fb2741b0e5a5f30e90185'}}
  expect(deliveredArtifactForHref('https://portal.openclaw.ai/59a5b7d9dcc69d70aad30310/welcome.md',[source])).toEqual(source)
})

it.each(['site', 'stripped', 'relay', 'preview'])('matches a canonical %s path without trusting the authored host', kind => {
  const source=receipt()
  const paths={site:`/${source.siteId}/report.md`,stripped:`/${source.siteId.slice(5)}/report.md`,relay:`/pixel-preview/${source.siteId}/report.md`,preview:`/preview/${source.siteId}/report.md`}
  expect(deliveredArtifactForHref(`https://untrusted.example${paths[kind]}`,[source])).toEqual(source)
})

it.each([
  '/site-bbbbbbbbbbbbbbbbbbbbbbbb/report.md', '/site-aaaaaaaaaaaaaaaaaaaaaaaa/other.md',
  '/site-aaaaaaaaaaaaaaaaaaaaaaaa/report.md/extra', '/other/site-aaaaaaaaaaaaaaaaaaaaaaaa/report.md',
  '/unused/../site-aaaaaaaaaaaaaaaaaaaaaaaa/report.md', '/site-aaaaaaaaaaaaaaaaaaaaaaaa/./report.md',
  '/unused/%2e%2e/site-aaaaaaaaaaaaaaaaaaaaaaaa/report.md', '/site-aaaaaaaaaaaaaaaaaaaaaaaa/%72eport.md',
  '/site-aaaaaaaaaaaaaaaaaaaaaaaa/report.md?download=1', '/site-aaaaaaaaaaaaaaaaaaaaaaaa/report.md#copy',
  '/site-aaaaaaaaaaaaaaaaaaaaaaaa\\report.md',
])('does not select a receipt from a noncanonical or unmatched path %s', path => {
  expect(deliveredArtifactForHref(`https://example.com${path}`,[receipt()])).toBeNull()
})

it.each(['http://user:password@example.com','https://exa\nmple.com',' https://example.com','file://example.com','javascript:example.com'])('rejects malformed or credential-bearing authored URLs %s', origin => {
  expect(deliveredArtifactForHref(`${origin}/${receipt().siteId}/report.md`,[receipt()])).toBeNull()
})

it('rejects absent, invalid or ambiguous metadata instead of guessing by filename', () => {
  const source=receipt(),url=`https://example.com/${source.siteId}/report.md`
  for(const artifacts of [undefined,[],[source,source],[{...source,sha256:'c'.repeat(64)}]]) {
    expect(deliveredArtifactForHref(url,artifacts)).toBeNull()
  }
  const collision={...source,sha256:'a'.repeat(24)+'b'.repeat(40)}
  expect(deliveredArtifactForHref(url,[source,collision])).toBeNull()
  expect(deliveredArtifactForHref('https://example.com/report.md',[source])).toBeNull()
})

it('accepts bounded host metadata separately from websites and copies it for history', () => {
  const source = receipt(), result = parseDeliveredArtifactsFrame(frame([source]))
  expect(result).toEqual([source])
  result[0].file.bytes = 13
  expect(source.file.bytes).toBe(12)
  expect(parseDeliveredArtifactsFrame(frame([]))).toEqual([])
  expect(deliveredArtifactMetadata({role:'assistant',status:'done',artifacts:[source]})).toEqual({artifacts:[source]})
  expect(deliveredArtifactMetadata({role:'user',status:'done',artifacts:[source]})).toEqual({})
  expect(deliveredArtifactMetadata({role:'assistant',status:'error',artifacts:[source]})).toEqual({})
})

it.each(['md','markdown','txt','csv','tsv','json','pdf','zip','rar','docx','xlsx','pptx','PDF'])('accepts %s as opaque downloadable bytes', ext => {
  const source = receipt(); source.relativePath = `Project/report.${ext}`; source.file.path = `report.${ext}`
  source.file.bytes = 0
  expect(parseDeliveredArtifacts([source])).toEqual([source])
  source.file.bytes = 4 * 1024 * 1024
  expect(parseDeliveredArtifacts([source])).toEqual([source])
})

it.each([
  item => {item.relativePath = '../report.md'}, item => {item.relativePath = '/etc/report.md'},
  item => {item.relativePath = 'Project/%2e%2e/report.md'}, item => {item.relativePath = 'C:\\report.md'},
  item => {item.relativePath = 'file:///report.md'}, item => {item.relativePath = 'Project/.hidden/report.md'},
  item => {item.relativePath = `${'a'.repeat(129)}/report.md`},
  item => {item.relativePath = `${('a'.repeat(127)+'/').repeat(4)}report.md`},
  item => {item.relativePath = `${'p/'.repeat(12)}report.md`},
  item => {item.file.path = '../report.md'}, item => {item.file.path = 'other.md'},
  item => {item.file.bytes = -1}, item => {item.file.bytes = 4 * 1024 * 1024 + 1},
  item => {item.file.bytes = 0.1}, item => {item.file.bytes = '12'},
  item => {item.sha256 = 'c'.repeat(64)}, item => {item.sha256 = ['a'.repeat(64)]},
  item => {item.file.sha256 = ['b'.repeat(64)]}, item => {item.file.sha256 = 'invalid'},
  item => {item.url = 'https://external.example/report.md'}, item => {item.file.extra = true},
  item => {item.kind = 'ods-pixel-workspace-preview'}, item => {item.schemaVersion = 2},
  item => {item.siteId = 'site-'+'a'.repeat(23)},
  item => {item.relativePath = 'Project/report.html'; item.file.path = 'report.html'},
  item => {item.relativePath = 'Project/report.docm'; item.file.path = 'report.docm'},
])('rejects altered receipt %s without authorizing a fetch', change => {
  const source = receipt(); change(source)
  expect(parseDeliveredArtifacts([source])).toBeNull()
})

it('rejects duplicate/oversized batches and nonterminal or malformed envelopes', () => {
  const source = receipt()
  expect(parseDeliveredArtifacts([source,source])).toBeNull()
  expect(parseDeliveredArtifacts(Array(5).fill(source))).toBeNull()
  expect(parseDeliveredArtifacts(null)).toBeNull()
  expect(parseDeliveredArtifactsFrame({...frame([source]), choices:[{finish_reason:null}]})).toBeNull()
  expect(parseDeliveredArtifactsFrame({...frame([source]), error:{code:'failed'}})).toBeNull()
  expect(parseDeliveredArtifactsFrame({...frame([source]),pixel_artifacts:{schemaVersion:1,artifacts:[source],extra:true}})).toBeNull()
  expect(parseDeliveredArtifactsFrame({choices:[{finish_reason:'stop',delta:{content:'MEDIA:Project/report.md'}}]})).toBeNull()
})

const FILE_SHA = '8aee' + '0'.repeat(60)
const SNAPSHOT_SHA = 'a'.repeat(64)
const delivered = () => ({
  schemaVersion: 1, kind: 'ods-pixel-workspace-artifact',
  relativePath: 'Playground/project/report.md',
  siteId: `site-${SNAPSHOT_SHA.slice(0, 24)}`,
  sha256: SNAPSHOT_SHA,
  file: { path: 'report.md', bytes: 12, sha256: FILE_SHA },
})
const directive = (ref = FILE_SHA, title = 'report.md', height = '480') =>
  `[embed ref="artifact_${ref}" title="${title}" height="${height}" /]`

it('hides an exact stand-alone generated directive matching a delivered artifact', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  expect(deliveredArtifactDisplayText(directive(), artifacts)).toBe('')
  expect(deliveredArtifactDisplayText(directive() + '\n', artifacts)).toBe('')
  expect(deliveredArtifactDisplayText(directive() + '\n\n', artifacts)).toBe('')
})

it('does not mutate the input content or artifacts', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  const snapshot = JSON.stringify(artifacts)
  const content = directive()
  deliveredArtifactDisplayText(content, artifacts)
  expect(content).toBe(directive())
  expect(JSON.stringify(artifacts)).toBe(snapshot)
})

it('preserves content when no delivered artifacts are present', () => {
  expect(deliveredArtifactDisplayText(directive(), [])).toBe(directive())
  expect(deliveredArtifactDisplayText(directive(), null)).toBe(directive())
  expect(deliveredArtifactDisplayText(directive(), undefined)).toBe(directive())
})

it('preserves content when the ref matches the snapshot digest instead of the file digest', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  expect(deliveredArtifactDisplayText(directive(SNAPSHOT_SHA), artifacts)).toBe(directive(SNAPSHOT_SHA))
})

it('preserves content when the title does not match the delivered file path', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  expect(deliveredArtifactDisplayText(directive(FILE_SHA, 'other.md'), artifacts)).toBe(directive(FILE_SHA, 'other.md'))
})

it('preserves content when the ref does not match any delivered file digest', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  expect(deliveredArtifactDisplayText(directive('c'.repeat(64)), artifacts)).toBe(directive('c'.repeat(64)))
})

it('preserves prose surrounding the directive', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  const before = `Here is your file:\n${directive()}`
  const after = `${directive()}\nLet me know if you need changes.`
  const both = `Here is your file:\n${directive()}\nLet me know if you need changes.`
  expect(deliveredArtifactDisplayText(before, artifacts)).toBe(before)
  expect(deliveredArtifactDisplayText(after, artifacts)).toBe(after)
  expect(deliveredArtifactDisplayText(both, artifacts)).toBe(both)
})

it('preserves indented, quoted, and fenced forms', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  const indented = `  ${directive()}`
  const quoted = `"${directive()}"`
  const fenced = '```\n' + directive() + '\n```'
  expect(deliveredArtifactDisplayText(indented, artifacts)).toBe(indented)
  expect(deliveredArtifactDisplayText(quoted, artifacts)).toBe(quoted)
  expect(deliveredArtifactDisplayText(fenced, artifacts)).toBe(fenced)
})

it('preserves malformed or non-canonical directives', () => {
  const artifacts = parseDeliveredArtifacts([delivered()])
  const cases = [
    `[embed ref="artifact_${FILE_SHA}" title="report.md" height="480"]`,
    `[embed ref='artifact_${FILE_SHA}' title='report.md' height='480' /]`,
    `[embed title="report.md" ref="artifact_${FILE_SHA}" height="480" /]`,
    `[embed ref="artifact_${FILE_SHA.toUpperCase()}" title="report.md" height="480" /]`,
    `[embed ref="artifact_${FILE_SHA}" title="report.md" height="48a" /]`,
    `[embed ref="artifact_${FILE_SHA}" title="report.md" height="480" /] `,
  ]
  for (const value of cases) expect(deliveredArtifactDisplayText(value, artifacts)).toBe(value)
})

it('preserves content when the receipt is missing or invalid', () => {
  expect(deliveredArtifactDisplayText(directive(), parseDeliveredArtifacts(null))).toBe(directive())
  const broken = delivered(); broken.file.sha256 = 'invalid'
  expect(deliveredArtifactDisplayText(directive(), parseDeliveredArtifacts([broken]))).toBe(directive())
})

it('requires a valid complete receipt even when digest and filename match', () => {
  for (const change of [item => {delete item.kind}, item => {item.siteId='invalid'}, item => {item.file.bytes=-1}]) {
    const invalid=delivered(); change(invalid)
    expect(deliveredArtifactDisplayText(directive(), [invalid])).toBe(directive())
  }
  const item=delivered(); Object.freeze(item.file); Object.freeze(item)
  expect(deliveredArtifactDisplayText(directive()+'\r\n', Object.freeze([item]))).toBe('')
})
