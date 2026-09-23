function SourceLink({ url, children }) {
  let href
  try {
    const parsed = new URL(url)
    if (parsed.protocol === 'https:' && !parsed.username && !parsed.password) href = parsed.href
  } catch { /* Keep untrusted or missing URLs as text. */ }
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{children}</a> : <span>{children}</span>
}

const ROLES = { artifact_publisher: 'Artifact publisher', declared_base: 'Declared base', declared_ancestor: 'Declared ancestor' }
const text = value => typeof value === 'string' ? value : ''
const rows = value => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : []

export default function ModelTermsContent({ result }) {
  const terms = result?.terms
  if (!terms) return null
  return <div className="space-y-3 break-words">
    <p className="font-semibold text-theme-text">{result.releaseReady ? 'Terms reviewed' : 'License review pending'}</p>
    <p>Publisher declarations are recorded below. They do not establish permission for every use.</p>
    <p>Acknowledging this record does not complete its license review or accept terms on the publisher's website.</p>
    <p>Commercial use: {terms.commercial_use === 'permitted_with_conditions' ? 'Permitted subject to the recorded conditions' : terms.commercial_use === 'restricted' ? 'Restricted; read the publisher terms' : 'Not yet assessed'}.</p>
    <p>Acceptance requirement: {terms.upstream_acceptance === 'required_by_observed_gating' ? "Complete the acceptance or access step required by the publisher" : terms.upstream_acceptance === 'required' ? 'Read and accept the applicable terms under the recorded conditions' : terms.upstream_acceptance === 'not_required' ? 'No separate acceptance step required by the reviewed terms' : 'Not yet assessed'}.</p>
    {rows(terms.conditions).length > 0 && <div><p className="font-semibold">Recorded conditions</p><ul className="space-y-2">{rows(terms.conditions).map((condition, index) => <li key={index}>{text(condition.trigger) && <span className="font-medium">{condition.trigger}: </span>}{text(condition.requirement)}</li>)}</ul></div>}
    <ul className="space-y-2">
      {rows(terms.sources).map((source, index) => <li key={`${source.repository}:${source.revision}:${index}`}>
        <span>{ROLES[source.role] || 'Source'}: </span><SourceLink url={source.url}>{text(source.repository)}</SourceLink>
        <div><SourceLink url={source.declaration_url}>{text(source.license_id) || 'No license identifier declared'}{text(source.license_name) ? ` (${source.license_name})` : ''}</SourceLink></div>
        {text(source.license_url) && <div><SourceLink url={source.license_url}>Publisher's declared license link</SourceLink></div>}
        {text(source.revision) && <p className="break-all text-xs">Revision: {source.revision}</p>}
      </li>)}
    </ul>
    {Array.isArray(terms.declared_base_repositories) && terms.declared_base_repositories.length > 0 && <div><p className="font-semibold">Declared base models — not reviewed</p><ul>{terms.declared_base_repositories.map((repository, index) => <li key={index}>{text(repository)}</li>)}</ul></div>}
    {rows(terms.license_documents).length ? <div><p className="font-semibold">Publisher license documents</p><ul>{rows(terms.license_documents).map((document, index) => <li key={`${document.url}:${index}`}><SourceLink url={document.url}>{text(document.repo) || 'License document'}</SourceLink></li>)}</ul></div> : <p>No separate license document was retrieved in this review.</p>}
    {rows(terms.notice_documents).length ? <div><p className="font-semibold">Attribution and notices</p><ul>{rows(terms.notice_documents).map((document, index) => <li key={`${document.url}:${index}`}><SourceLink url={document.url}>{text(document.repository)}: {text(document.path)}</SourceLink></li>)}</ul></div> : <p>No separate attribution notice was retrieved in this review.</p>}
    {rows(terms.local_notices).length > 0 && <div><p className="font-semibold">Retained notice copies</p><ul>{rows(terms.local_notices).map((notice, index) => <li key={index}><SourceLink url={notice.source_url}>{text(notice.path)}</SourceLink></li>)}</ul></div>}
    {Array.isArray(terms.issues) && terms.issues.length > 0 && <div><p className="font-semibold">Unresolved review issues</p><ul>{terms.issues.map((issue, index) => <li key={index}>{text(issue).replaceAll('_', ' ')}</li>)}</ul></div>}
    {text(terms.note) && <p>{terms.note}</p>}
    {rows(terms.artifacts).some(artifact => !artifact.observed_present) && <p className="text-amber-400">The configured download file was not found at the reviewed source revision.</p>}
    <p>Retain the applicable license and attribution notices when redistributing model files.</p>
  </div>
}
