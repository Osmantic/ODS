const states = new Set(['Ready','Unavailable','Reported by ODS','Not loaded','Verified','Not verified'])
const accessModes = new Set(['Safer mode','Full Access','Not verified'])
const row = (result,label) => result.rows?.find(item => item[0] === label)?.[1]

export function diagnosticSummary(results, checkedAt) {
  if (!(checkedAt instanceof Date) || !Number.isFinite(checkedAt.getTime()) || !['agent','model','access'].every(id => results[id])) throw new Error('Checks are incomplete')
  const checks = Object.fromEntries(['agent','model','access'].map(id => {
    const result = results[id]
    const summary = {state:states.has(result.state) ? result.state : 'Unavailable', ok:result.ok === true}
    if (id === 'access') {
      for (const [label,key] of [['Configured','configuredMode'],['Effective','effectiveMode']]) {
        const value=row(result,label)
        summary[key]=accessModes.has(value) ? value : 'Not verified'
      }
    } else {
      const value=row(result,'Context window')
      summary.contextWindow=typeof value === 'string' && value.length <= 64 && /^[\p{N},.\s\u066B\u066C]+ tokens$/u.test(value) ? value : 'Not reported'
    }
    return [id,summary]
  }))
  return {schemaVersion:1,kind:'ods-pixel-diagnostic-summary',checkedAt:checkedAt.toISOString(),checks}
}
