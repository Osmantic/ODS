import { useEffect, useState } from 'react'
import './ods-logo.css'

const source = '/osmantic-isolated-os.png'

export default function ODSLogo() {
  const [mask, setMask] = useState(null)
  useEffect(() => {
    let disposed = false
    const img = new Image()
    img.onload = () => {
      if (disposed) return
      try {
        const canvas = document.createElement('canvas')
        canvas.width = img.naturalWidth
        canvas.height = img.naturalHeight
        const ctx = canvas.getContext('2d')
        if (!ctx) return
        ctx.drawImage(img, 0, 0)
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height)
        // Preserve the supplied mark's silhouette; its dark textured backdrop
        // is not part of the logo. This is the existing chroma-to-alpha mapping.
        for (let i = 0; i < pixels.data.length; i += 4) {
          const chroma = Math.max(...pixels.data.slice(i, i + 3)) - Math.min(...pixels.data.slice(i, i + 3))
          pixels.data[i + 3] = Math.max(0, Math.min(255, (chroma - 25) * 2))
          pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = 255
        }
        ctx.putImageData(pixels, 0, 0)
        setMask(canvas.toDataURL())
      } catch {
        // Keep the original silhouette when canvas readback is unavailable.
      }
    }
    img.src = source
    return () => { disposed = true }
  }, [])
  return <div className="ods-metal-logo ods-frosted-logo" aria-hidden="true" data-mask-ready={mask ? '' : undefined} style={mask ? {'--ods-logo-mask': `url("${mask}")`} : undefined}>
    <div className="ods-frosted-mark" />
    <img className="ods-logo-fallback" src={source} alt="" />
  </div>
}
