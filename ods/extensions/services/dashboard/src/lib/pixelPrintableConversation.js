import {conversationTitle, readConversations} from './pixelConversations'

const escape = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))

export function downloadPrintableConversation(chatId) {
  const chat = readConversations().find(item => item.chatId === chatId)
  if (!chat) throw new Error('Conversation unavailable')
  const title = escape(conversationTitle({...chat, draft:''}))
  const messages = chat.messages.map((message, index) => `<article><h2>${index + 1}. ${message.role === 'user' ? 'You' : 'Assistant'}</h2><pre>${escape(message.content)}</pre></article>`).join('\n')
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>${title}</title><style>body{max-width:70ch;margin:2rem auto;padding:0 1rem;color:#111;background:#fff;font:16px/1.6 system-ui,sans-serif}h1{font-size:1.6rem;overflow-wrap:anywhere}h2{font-size:1rem;break-after:avoid}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}article{border-top:1px solid #bbb;margin-top:1.5rem}footer{margin-top:2rem;font-size:.8rem;color:#555}@media print{body{max-width:none;margin:0;padding:0}@page{margin:18mm}}</style></head>
<body><h1>${title}</h1><p>Saved conversation snapshot · ${escape(new Date().toISOString())}</p>
<p>${chat.inFlight ? 'Work was still active when saved; this transcript may be incomplete. ' : ''}Messages are shown as text, including Markdown notation. Unsent drafts and runtime metadata are excluded. Use your browser's Print command to print or save as PDF.</p>
${messages}<footer>ODS Pixel · Local printable transcript</footer></body></html>`
  let url, anchor
  try {
    url = URL.createObjectURL(new Blob([html], {type:'text/html;charset=utf-8'}))
    anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'ods-pixel-transcript.html'
    document.body.append(anchor)
    anchor.click()
  } finally {
    anchor?.remove()
    if (url) setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
