import {useState} from 'react'

// Consent belongs to this rendered image, not the URL's host or saved history.
// The caller keys by src so streamed URL changes cannot inherit permission.
export default function PixelReplyImage({src, alt = '', title}) {
  const [requested, setRequested] = useState(false)
  const [failed, setFailed] = useState(false)
  let host = ''
  if (typeof src === 'string' && /^https?:\/\//i.test(src)) {
    try { host = new URL(src).host } catch { /* Invalid Markdown URL: show alt text only. */ }
  }
  return (
    <span className="my-2 inline-block max-w-full rounded border border-theme-border p-2">
      {requested ? (
        <img src={src} alt={alt} title={title} referrerPolicy="no-referrer"
          className="max-w-full" onError={() => {setRequested(false); setFailed(true)}} />
      ) : (
        <>
          <span>{alt || 'Image'}</span>
          {failed && <span role="status" className="ml-2">Image could not be loaded.</span>}
          {host && <button type="button" className="ml-2 text-theme-accent-light underline"
            onClick={() => {setFailed(false); setRequested(true)}}>Load image from {host}</button>}
        </>
      )}
    </span>
  )
}
