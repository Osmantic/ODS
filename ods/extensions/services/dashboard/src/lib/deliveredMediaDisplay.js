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
    const wholeLine=(start,end)=>(start===0 || /[\r\n]/.test(source[start-1]))
      && (end===source.length || /[\r\n]/.test(source[end]))
    tree.children=tree.children.filter(paragraph=>{
      // Quotes, lists and code remain examples. Raw inline HTML can contain
      // text siblings in a paragraph, so leave those paragraphs intact too.
      if(paragraph.tagName!=='p' || paragraph.children.some(child=>child.type==='raw'))return true
      let changed=false
      const children=paragraph.children.map(child=>{
        const start=child.position?.start?.offset,end=child.position?.end?.offset
        if(!Number.isInteger(start) || !Number.isInteger(end))return child
        if(child.type!=='text') {
          let leaf=child
          while(['strong','em'].includes(leaf.tagName) && leaf.children?.length===1)leaf=leaf.children[0]
          const match=leaf.type==='text' && /^MEDIA:([^\s/\\]+)$/.exec(leaf.value)
          const raw=source.slice(start,end)
          const wrapped=match && ['**','__','*','_','***','___'].some(marker=>raw===`${marker}${leaf.value}${marker}`)
          if(wrapped && wholeLine(start,end) && counts.get(match[1])===1) {
            changed=true
            return {type:'text',value:'',position:child.position}
          }
          return child
        }
        const raw=source.slice(start,end).split(/(\r\n|\n|\r)/)
        const shown=child.value.split(/(\r\n|\n|\r)/)
        if(raw.length!==shown.length)return child
        let offset=start,removed=false
        for(let i=0;i<raw.length;i+=2) {
          const line=raw[i],lineEnd=offset+line.length
          const match=/^MEDIA:([^\s/\\]+)$/.exec(line)
          // AST offsets can exclude indentation, escapes or trailing Markdown.
          // Require the exact raw line at column zero, not normalized text.
          if(match && wholeLine(offset,lineEnd) && shown[i]===line && counts.get(match[1])===1) {
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
