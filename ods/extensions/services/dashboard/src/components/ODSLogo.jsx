import { useEffect, useState } from 'react'
import { LiquidMetal } from '@paper-design/shaders-react'

const source = '/osmantic-isolated-os.png'

export default function ODSLogo({ active = true }) {
  const [mask, setMask] = useState(null)
  const [still, setStill] = useState(false)
  useEffect(() => {
    // Keep the original image in embedded/limited renderers without browser APIs.
    if (typeof window.matchMedia !== 'function') return
    let disposed = false
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setStill(preference.matches)
    update()
    preference.addEventListener('change', update)
    const canvas = document.createElement('canvas')
    let supported = null
    try {
      supported = document.createElement('canvas').getContext('webgl2')
      supported?.getExtension('WEBGL_lose_context')?.loseContext()
    } catch {
      // Disabled GPU contexts must not prevent the dashboard from opening.
    }
    if (supported) {
      const img = new Image()
      img.onload = () => {
        if (disposed) return
        try {
          canvas.width = img.naturalWidth
          canvas.height = img.naturalHeight
          const ctx = canvas.getContext('2d')
          if (!ctx) return
          ctx.drawImage(img, 0, 0)
          const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height)
          // Use chroma as opacity: the black textured background is not part of the mark.
          for (let i = 0; i < pixels.data.length; i += 4) {
            const chroma = Math.max(...pixels.data.slice(i, i + 3)) - Math.min(...pixels.data.slice(i, i + 3))
            pixels.data[i + 3] = Math.max(0, Math.min(255, (chroma - 25) * 2))
            pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = 255
          }
          ctx.putImageData(pixels, 0, 0)
          setMask(canvas.toDataURL())
        } catch {
          // Keep the static mark if the browser denies canvas readback.
        }
      }
      img.src = source
    }
    return () => { disposed = true; preference.removeEventListener('change', update) }
  }, [])
  return <div className="ods-metal-logo" aria-hidden="true">
    {mask ? <LiquidMetal image={mask} width="100%" height="100%" colorBack="#00000000" colorTint="#ffffff" repetition={2} softness={0.1} shiftRed={0.3} shiftBlue={0.3} distortion={0.07} contour={0.4} angle={70} speed={still || !active ? 0 : 1} scale={0.9} fit="contain" /> : <img src={source} alt="" style={{filter:'grayscale(1)',mixBlendMode:'screen'}} />}
  </div>
}
