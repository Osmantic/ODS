import { deliveredArtifactMetadata, parseDeliveredArtifacts, parseDeliveredArtifactsFrame } from './pixelDeliveredArtifacts'

const receipt = () => ({schemaVersion:1, kind:'ods-pixel-workspace-artifact', relativePath:'Playground/project/report.md',
  siteId:`site-${'a'.repeat(24)}`, sha256:'a'.repeat(64), file:{path:'report.md', bytes:12, sha256:'b'.repeat(64)}})
const frame = artifacts => ({choices:[{finish_reason:'stop'}],pixel_artifacts:{schemaVersion:1,artifacts}})

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
