import { ArrowUpCircle, ArrowRight, X } from 'lucide-react'
import './portal-update-notice.css'

export default function PortalUpdateNotice({ version, onReview, onDismiss }) {
  if (!version?.update_available || !version.latest || !version.current?.trim() || version.latest === version.current
      || (version.check_status && version.check_status !== 'checked')) return null
  return <section className="portal-update-notice" role="status" aria-label="ODS update available">
    <ArrowUpCircle size={18} aria-hidden="true" />
    <div><strong>ODS {version.latest} is available</strong><span>Installed version: {version.current}</span></div>
    <button type="button" className="portal-update-review" onClick={onReview}>View update <ArrowRight size={14} aria-hidden="true" /></button>
    <button type="button" className="portal-update-dismiss" aria-label="Dismiss this update" onClick={onDismiss}><X size={16} aria-hidden="true" /></button>
  </section>
}
