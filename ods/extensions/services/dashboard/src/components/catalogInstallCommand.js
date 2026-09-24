// Accept only a complete affirmative install request, never arbitrary prose
// containing a mention. Canonicalize before both chat and coordinator dispatch.
export function catalogInstallCommand(value) {
  if (typeof value !== 'string') return value
  const match = /^(?:(?:ola|olá|oi|hello|hi)[, ]+)?(?:por favor[, ]+|please[ ]+)?(?:instale(?:[ ]+(?:pra|para)[ ]+mim)?|install(?:[ ]+for[ ]+me)?)[ ]+\/extensions?[ ]+@([a-z0-9][a-z0-9_-]{0,63})[ ]*[.!]?$/i.exec(value.trim())
  return match ? `/extensions @${match[1].toLowerCase()}` : value
}
