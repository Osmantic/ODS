import './pixel-preview-viewport.css'

export default function PixelPreviewViewport({access, title, hidden, onRetry}) {
  return <section className="pixel-preview-viewport" hidden={hidden} aria-label="Preview">
    <div className="pixel-viewport-stage">
      {access.frameUrl ? <iframe src={access.frameUrl} title={title} hidden={hidden} sandbox={access.sandbox} data-preview-route={access.route} referrerPolicy="no-referrer" style={{width:'100%',height:'100%'}}/> : <p role="status">{access.checking?'Connecting preview…':'Preview connection unavailable.'}{!access.checking && onRetry && <button type="button" onClick={onRetry}>Retry</button>}</p>}
    </div>
    {access.frameUrl && <p className="pixel-viewport-note">Preview inputs are temporary and may be lost when you reload.</p>}
  </section>
}
