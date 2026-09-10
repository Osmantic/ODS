export const SYSTEM_RULES_KEY = 'ods.pixel.customSystemRules.v1'

export function saveSystemRules(rules) {
  if (rules?.trim()) localStorage.setItem(SYSTEM_RULES_KEY, rules.trim())
  else localStorage.removeItem(SYSTEM_RULES_KEY)
  window.dispatchEvent(new Event('storage'))
}

export function getSystemRules() {
  return localStorage.getItem(SYSTEM_RULES_KEY) || ''
}
