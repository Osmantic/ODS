import { FileDown } from 'lucide-react'
import PixelArtifactDownload from './PixelArtifactDownload'
import { parseDeliveredArtifacts } from '../lib/pixelDeliveredArtifacts'

function byteLabel(bytes) {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function PortalDeliveredArtifacts({ artifacts }) {
  const verified = parseDeliveredArtifacts(artifacts)
  if (!verified?.length) return null
  return <section aria-label="Delivered files" className="my-3 space-y-2">
    {verified.map(artifact => <div key={`${artifact.siteId}/${artifact.file.path}/${artifact.file.sha256}/${artifact.file.bytes}`}
      className="flex flex-wrap items-center gap-3 rounded-xl border border-theme-border bg-theme-card/60 px-4 py-3">
      <FileDown aria-hidden="true" className="h-5 w-5 shrink-0 text-theme-text-secondary" />
      <div className="min-w-0 flex-1">
        <p className="break-all text-sm font-medium text-theme-text">{artifact.file.path}</p>
        <p className="text-xs text-theme-text-secondary">{byteLabel(artifact.file.bytes)}</p>
      </div>
      <div className="shrink-0 [&_button]:rounded-lg [&_button]:border [&_button]:border-theme-border [&_button]:px-3 [&_button]:py-2 [&_button]:text-xs [&_button]:font-medium [&_button:hover]:bg-theme-border/30 [&_small]:mt-1 [&_small]:block [&_small]:max-w-52 [&_small]:text-xs [&_small]:text-theme-text-secondary">
        <PixelArtifactDownload preview={artifact} file={artifact.file} />
      </div>
    </div>)}
  </section>
}
