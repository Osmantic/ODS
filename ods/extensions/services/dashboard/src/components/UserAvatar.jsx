import {UserRound} from 'lucide-react'
import '../profile.css'

export default function UserAvatar({profile, className=''}) {
  const name = profile.name || 'You'
  const initials = profile.name.trim().split(/\s+/).filter(Boolean).slice(0,2).map(part => Array.from(part)[0]).join('').toUpperCase()
  return <span className={`user-avatar ${className}`} title={name}>
    {profile.photo ? <img src={profile.photo} alt={`${name} profile photo`}/> : <span role="img" aria-label={`${name} avatar`}>{initials || <UserRound size={16} aria-hidden="true"/>}</span>}
  </span>
}
