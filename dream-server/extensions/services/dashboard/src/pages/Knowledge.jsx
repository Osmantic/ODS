import { useState, useEffect, useRef } from 'react'
import { BookOpen, UploadCloud, Trash2, FileText, Loader2, CheckCircle2, AlertCircle, Database } from 'lucide-react'

export default function Knowledge() {
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)
  const fileInputRef = useRef(null)

  const fetchDocuments = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/knowledge/documents')
      const text = await res.text()
      if (res.ok) {
        try {
          const data = JSON.parse(text)
          setDocuments(data.documents || [])
        } catch (err) {
          setError('Invalid JSON response from server')
        }
      } else {
        setError(`Failed to fetch documents: ${res.statusText}`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDocuments()
  }, [])

  const handleUpload = async (file) => {
    if (!file) return
    setUploading(true)
    setError(null)
    setSuccess(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch('/api/knowledge/upload', {
        method: 'POST',
        body: formData,
      })
      const text = await res.text()
      let data = {}
      try { data = JSON.parse(text) } catch (e) {}

      if (res.ok) {
        setSuccess(`Successfully indexed ${file.name} (${data.chunks || 0} chunks)`)
        fetchDocuments()
      } else {
        setError(data.detail || 'Upload failed')
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const handleDelete = async (docId) => {
    if (!window.confirm('Are you sure you want to delete this document from the knowledge base?')) return

    try {
      const res = await fetch(`/api/knowledge/documents/${docId}`, {
        method: 'DELETE',
      })
      if (res.ok) {
        fetchDocuments()
      } else {
        const text = await res.text()
        let data = {}
        try { data = JSON.parse(text) } catch (e) {}
        setError(data.detail || 'Failed to delete document')
      }
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-8">
        <BookOpen className="w-8 h-8 text-theme-accent" />
        <div>
          <h1 className="text-2xl font-bold">Knowledge Base</h1>
          <p className="text-theme-text-muted">Upload documents to chat with them across your local models.</p>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {success && (
        <div className="mb-6 p-4 rounded-xl bg-green-500/10 border border-green-500/20 text-green-400 flex items-start gap-3">
          <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
          <p>{success}</p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Upload Section */}
        <div className="lg:col-span-1">
          <div className="bg-theme-card rounded-2xl border border-theme-border p-6 shadow-sm">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <UploadCloud className="w-5 h-5 text-theme-accent" />
              Upload Document
            </h2>
            <div
              className="border-2 border-dashed border-theme-border rounded-xl p-8 text-center hover:border-theme-accent transition-colors cursor-pointer"
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading ? (
                <div className="flex flex-col items-center gap-3">
                  <Loader2 className="w-8 h-8 text-theme-accent animate-spin" />
                  <p className="text-sm text-theme-text-muted">Processing & Embedding...</p>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3">
                  <FileText className="w-8 h-8 text-theme-text-muted" />
                  <div>
                    <p className="text-sm font-medium">Click to upload</p>
                    <p className="text-xs text-theme-text-muted mt-1">PDF, TXT, MD, CSV (Max 10MB)</p>
                  </div>
                </div>
              )}
            </div>
            <input
              type="file" 
              ref={fileInputRef} 
              className="hidden" 
              accept=".pdf,.txt,.md,.csv" 
              onChange={(e) => handleUpload(e.target.files[0])}
            />
          </div>
        </div>

        {/* Documents List */}
        <div className="lg:col-span-2">
          <div className="bg-theme-card rounded-2xl border border-theme-border shadow-sm overflow-hidden">
            <div className="p-6 border-b border-theme-border flex items-center justify-between">
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <Database className="w-5 h-5 text-theme-accent" />
                Indexed Files
              </h2>
              <span className="text-sm text-theme-text-muted bg-theme-bg px-2.5 py-1 rounded-full font-mono">
                {documents.length} docs
              </span>
            </div>
            
            <div className="divide-y divide-theme-border max-h-[600px] overflow-y-auto">
              {loading ? (
                <div className="p-8 text-center text-theme-text-muted flex items-center justify-center gap-2">
                  <Loader2 className="w-5 h-5 animate-spin" /> Loading...
                </div>
              ) : documents.length === 0 ? (
                <div className="p-12 text-center">
                  <BookOpen className="w-12 h-12 text-theme-border mx-auto mb-4" />
                  <p className="text-theme-text-muted">Your knowledge base is empty.</p>
                  <p className="text-sm text-theme-text-muted mt-1">Upload a document to get started.</p>
                </div>
              ) : (
                documents.map(doc => (
                  <div key={doc.id} className="p-4 flex items-center justify-between hover:bg-theme-bg transition-colors group">
                    <div className="flex items-center gap-3 overflow-hidden">
                      <div className="w-10 h-10 rounded-lg bg-theme-bg flex items-center justify-center shrink-0 border border-theme-border">
                        <FileText className="w-5 h-5 text-theme-text-muted" />
                      </div>
                      <div className="min-w-0">
                        <p className="font-medium truncate text-sm">{doc.filename}</p>
                        <p className="text-xs text-theme-text-muted font-mono mt-1">ID: {doc.id.split('-')[0]}...</p>
                      </div>
                    </div>
                    <button 
                      onClick={() => handleDelete(doc.id)}
                      className="p-2 text-theme-text-muted hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors opacity-0 group-hover:opacity-100"
                      title="Delete from Knowledge Base"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
