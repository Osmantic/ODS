// Public progress only: these descriptions are derived from observed tool kinds,
// never model reasoning, shell text, or an inferred successful outcome.
// Portal's own UI text is English; the browser locale does not change it.
const labels={
  read:'Inspecting available files and information.',
  edit:'Making changes to project files.',
  browser:'Consulting sources on the web.',
  run:'Running commands and tracking processes.',
  preview:'Publishing the project preview.',
  agent:'Coordinating work across agents.',
}

export function activityProgressRows(events) {
  const rows=[], publicMessages=new Set()
  let phase=null, lastSummary=-4, lastPublic=-4
  for(let i=0;i<events.length;i++) {
    const event=events[i]
    if(event.display?.type==='text' && event.state==='completed') {
      const key=event.display.label.trim().toLocaleLowerCase().replace(/\s+/g,' ')
      lastPublic=i
      if(!publicMessages.has(key)){rows.push({event});publicMessages.add(key)}
      continue
    }
    const eligible=['running','completed'].includes(event.state) && labels[event.kind]
    // A nearby explicit update takes priority. Group repetitive tool calls and
    // avoid narrating every alternating read/write in a tight loop.
    const nearbyPublic=events.slice(i,i+3).some(item=>item.display?.type==='text' && item.state==='completed')
    if(eligible && event.kind!==phase && i-lastSummary>=4 && i-lastPublic>=4 && !nearbyPublic) {
      rows.push({id:`progress-${event.sequence}`,text:labels[event.kind]})
      lastSummary=i
    }
    if(eligible)phase=event.kind
    rows.push({event})
  }
  return rows
}
