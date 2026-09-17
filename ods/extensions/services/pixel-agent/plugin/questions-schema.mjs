const plain = (value, max) => typeof value === 'string' && value.trim().length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
export function parseQuestions(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 3) return null;
  const ids = new Set();
  for (const item of value) {
    if (!item || Object.keys(item).sort().join() !== 'id,options,question' || !/^[a-z][a-z0-9_]{0,39}$/.test(item.id ?? '') || ids.has(item.id) || !plain(item.question,300) || !Array.isArray(item.options) || item.options.length < 2 || item.options.length > 4 || new Set(item.options).size !== item.options.length || !item.options.every(option=>plain(option,160))) return null;
    ids.add(item.id);
  }
  return value.map(item=>({id:item.id,question:item.question,options:[...item.options]}));
}
