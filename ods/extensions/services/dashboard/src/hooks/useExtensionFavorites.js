import { useState } from 'react'

const KEY = 'ods-extension-favorites-v1'

function readFavorites() {
  try {
    const ids = JSON.parse(globalThis.localStorage.getItem(KEY) || '[]')
    if (!Array.isArray(ids) || ids.some(id => typeof id !== 'string')) {
      return { ids: [], notice: 'Saved favorites could not be read. Choose favorites again.' }
    }
    return { ids, notice: '' }
  } catch (error) {
    if (!(error instanceof globalThis.DOMException) && !(error instanceof SyntaxError)) throw error
    return { ids: [], notice: 'Favorites are available for this visit; browser storage could not be read.' }
  }
}

export function useExtensionFavorites() {
  const [state, setState] = useState(readFavorites)
  function toggle(id) {
    const ids = state.ids.includes(id) ? state.ids.filter(item => item !== id) : [...state.ids, id]
    let notice = ''
    try {
      globalThis.localStorage.setItem(KEY, JSON.stringify(ids))
    } catch (error) {
      if (!(error instanceof globalThis.DOMException)) throw error
      notice = 'Favorites changed for this visit but could not be saved in this browser.'
    }
    setState({ ids, notice })
  }
  return { favorites: state.ids, favoriteNotice: state.notice, toggleFavorite: toggle }
}
