import {parseDeliveredArtifacts} from './pixelDeliveredArtifacts'

// Presentation only. A MEDIA line cannot authorize a file or supply a URL;
// the completed response's validated receipt and download card do that.
export function suppressDeliveredMediaLines({source,artifacts}) {
  const delivered=parseDeliveredArtifacts(artifacts)
  const counts=new Map()
  for(const artifact of delivered || []) {
    const name=artifact.file.path
    counts.set(name,(counts.get(name) || 0)+1)
  }
  return tree=>{
    if(!counts.size || typeof source!=='string')return
    tree.children=tree.children.filter(paragraph=>{
      // Quotes, lists and code remain examples. Raw inline HTML can contain
      // text siblings in a paragraph, so leave those paragraphs intact too.
      if(paragraph.tagName!=='p' || paragraph.children.some(child=>child.type==='raw'))return true
      let changed=false
      const children=paragraph.children.map(child=>{
        if(child.type!=='text')return child
        const start=child.position?.start?.offset,end=child.position?.end?.offset
        if(!Number.isInteger(start) || !Number.isInteger(end))return child
        const raw=source.slice(start,end).split(/(\r\n|\n|\r)/)
        const shown=child.value.split(/(\r\n|\n|\r)/)
        if(raw.length!==shown.length)return child
        let offset=start,removed=false
        for(let i=0;i<raw.length;i+=2) {
          const line=raw[i],lineEnd=offset+line.length
          const match=/^MEDIA:([^\s/\\]+)$/.exec(line)
          // AST offsets can exclude indentation, escapes or trailing Markdown.
          // Require the exact raw line at column zero, not normalized text.
          const wholeLine=(offset===0 || /[\r\n]/.test(source[offset-1]))
            && (lineEnd===source.length || /[\r\n]/.test(source[lineEnd]))
          if(match && wholeLine && shown[i]===line && counts.get(match[1])===1) {
            shown[i]=''
            removed=true
          }
          offset=lineEnd+(raw[i+1]?.length || 0)
        }
        if(!removed)return child
        changed=true
        return {...child,value:shown.join('')}
      })
      if(!changed)return true
      paragraph.children=children
      return !children.every(child=>child.type==='text' && !child.value.trim())
    })
  }
}
