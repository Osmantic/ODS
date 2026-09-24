import {useEffect, useState} from 'react'

export default function PixelVerifiedImage({bytes, mime, path}) {
  const [shown, setShown] = useState(false)
  const [source, setSource] = useState(null)
  const [failed, setFailed] = useState(false)
  const [dimensions, setDimensions] = useState(null)
  useEffect(() => {
    if (!shown) return
    setFailed(false)
    setDimensions(null)
    let url
    try {
      url = URL.createObjectURL(new Blob([bytes], {type:mime}))
      setSource({bytes, url})
    } catch {setFailed(true)}
    return () => {if (url) URL.revokeObjectURL(url)}
  }, [shown, bytes, mime])
  const url = shown && source?.bytes === bytes ? source.url : null
  return <div className="p-3 text-xs">
    <button type="button" aria-expanded={shown} onClick={() => {setSource(null); setShown(value => !value)}}>{shown ? 'Hide image preview' : 'Show verified image'}</button>
    {shown && (failed ? <p role="alert">The bytes are verified, but this browser could not display the image. You can still download the file.</p> : url && <figure className="mt-2">
      <img src={url} alt={`Published image: ${path}`} className="max-h-96 max-w-full object-contain" onError={() => setFailed(true)} onLoad={event => setDimensions([event.currentTarget.naturalWidth, event.currentTarget.naturalHeight])}/>
      {dimensions && <figcaption>{dimensions[0]} × {dimensions[1]} pixels · verified published image</figcaption>}
    </figure>)}
  </div>
}
