const EXTENSIONS = {
  html:'html', htm:'html', css:'css', scss:'scss', js:'javascript', mjs:'javascript', cjs:'javascript',
  jsx:'jsx', ts:'typescript', tsx:'tsx', py:'python', sh:'bash', yml:'yaml', yaml:'yaml', toml:'ini',
  json:'json', svg:'xml', xml:'xml', md:'markdown', markdown:'markdown', txt:'text', map:'json', csv:'text', tsv:'text',
}
const FILENAMES = {
  makefile:'makefile', gnumakefile:'makefile', readme:'text', license:'text', notice:'text',
  dockerfile:'dockerfile', containerfile:'dockerfile',
}

// Recognize documented project text names without treating unknown binaries as
// text. Byte limits, SHA-256 verification and strict UTF-8 decoding stay upstream.
export function sourceLanguage(path) {
  const name = String(path).split('/').at(-1).toLowerCase()
  return FILENAMES[name] || (name.includes('.') ? EXTENSIONS[name.split('.').at(-1)] : undefined)
}
