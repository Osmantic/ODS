import {useEffect, useRef, useState} from 'react'
import {useTheme} from '../contexts/ThemeContext'

export default function WallpaperVideo() {
  const {theme, wallpapers = [], wallpaperMotion = true} = useTheme()
  const wallpaper = wallpapers.find(item => item.id === theme)
  const blob = wallpaper?.kind === 'video' ? wallpaper.video : null
  const videoRef = useRef(null)
  const [source, setSource] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
    if (!blob) { setSource(null); return }
    const url = URL.createObjectURL(blob)
    setSource({blob, url})
    return () => URL.revokeObjectURL(url)
  }, [blob])

  const url = source?.blob === blob ? source?.url : null
  useEffect(() => {
    const video = videoRef.current
    if (!video || !url || failed) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    let disposed = false
    const sync = () => {
      if (disposed) return
      if (!wallpaperMotion || document.hidden || reduced?.matches) video.pause()
      else {
        const playing = video.play()
        playing?.then(() => { if (!disposed && (document.hidden || reduced?.matches)) video.pause() })
          .catch(error => {
            // pause() can interrupt a pending play when visibility or motion
            // changes. That cancellation must not disable later playback.
            if (!disposed && error.name !== 'AbortError') setFailed(true)
          })
      }
    }
    sync()
    document.addEventListener('visibilitychange', sync)
    reduced?.addEventListener('change', sync)
    return () => {
      disposed = true
      video.pause()
      document.removeEventListener('visibilitychange', sync)
      reduced?.removeEventListener('change', sync)
    }
  }, [url, wallpaperMotion, failed])

  if (!url || failed) return null
  return <video ref={videoRef} className="workspace-wallpaper-video" src={url} poster={wallpaper.image}
    muted loop playsInline preload="auto" aria-hidden="true" tabIndex={-1}
    disablePictureInPicture onError={() => setFailed(true)}/>
}
