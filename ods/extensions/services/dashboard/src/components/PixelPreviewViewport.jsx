import './pixel-preview-viewport.css'

export default function PixelPreviewViewport({access, title, hidden}) {
  return <section className="pixel-preview-viewport" hidden={hidden} aria-label="Preview">
    <div className="pixel-viewport-stage">
      <iframe src={access.frameUrl} title={title} hidden={hidden} sandbox={access.sandbox} data-preview-route={access.route} referrerPolicy="no-referrer" style={{width:'100%',height:'100%'}}/>
    </div>
  </section>
}
