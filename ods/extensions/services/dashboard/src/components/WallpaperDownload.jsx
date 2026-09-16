import {useState} from 'react'
import {isStoredWallpaper} from '../lib/customWallpapers'

export default function WallpaperDownload({wallpaper}) {
  const [state, setState] = useState('')
  if (!isStoredWallpaper(wallpaper)) return null
  function download() {
    setState('')
    let url, link
    try {
      const video = wallpaper.kind === 'video'
      const mime = video ? wallpaper.video.type : wallpaper.image.slice(5, wallpaper.image.indexOf(';'))
      const extension = {'image/jpeg':'jpg', 'image/png':'png', 'image/webp':'webp', 'video/mp4':'mp4', 'video/webm':'webm'}[mime]
      const bytes = video ? null : Uint8Array.from(globalThis.atob(wallpaper.image.split(',')[1]), character => character.charCodeAt(0))
      const blob = video ? wallpaper.video : new Blob([bytes], {type:mime})
      url = URL.createObjectURL(blob)
      link = document.createElement('a')
      link.href = url
      link.download = `ods-wallpaper-${wallpaper.id.slice(7)}.${extension}`
      document.body.append(link)
      link.click()
      setState('saved')
    } catch { setState('error') }
    finally {
      link?.remove()
      if (url) setTimeout(() => URL.revokeObjectURL(url), 1000)
    }
  }
  return <div>
    <button type="button" className="btn-secondary" onClick={download}>Download saved wallpaper</button>
    <p className="wallpaper-note">Images download as the resized browser copy. Videos keep their stored file bytes.</p>
    {state === 'saved' && <p role="status">Wallpaper download started.</p>}
    {state === 'error' && <p role="alert">Wallpaper download could not start. Try again; your saved wallpaper is unchanged.</p>}
  </div>
}
