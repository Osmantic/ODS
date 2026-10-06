import {LifeBuoy} from 'lucide-react'
import {ODS_HELP_DISCORD_URL} from '../lib/support'

// A visible way out of any error: the ODS community Discord.
export default function HelpLink({label = 'Get help on Discord', className = ''}) {
  return (
    <a
      href={ODS_HELP_DISCORD_URL}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center gap-1 underline ${className}`}
    >
      <LifeBuoy size={13} aria-hidden="true" />
      <span>{label}</span>
    </a>
  )
}
