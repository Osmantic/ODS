import { useState, useEffect } from 'react'
import { CheckCircle, XCircle, Loader2, MessageSquare, Mic, FileText, Zap, RefreshCw } from 'lucide-react'

export function SuccessValidation({ status, onAllPassed }) {
  const [tests, setTests] = useState([])
  const [running, setRunning] = useState(false)
  const [allPassed, setAllPassed] = useState(false)

  useEffect(() => {
    if (status?.services) {
      initializeTests(status.services)
    }
  }, [status])

  const initializeTests = (services) => {
    const serviceMap = Object.fromEntries(services.map(s => [s.name, s.status]))
    
    setTests([
      {
        id: 'llm',
        name: 'AI Chat (LLM)',
        description: 'Can have a conversation',
        icon: MessageSquare,
        status: serviceMap['llama-server (LLM Inference)'] === 'healthy' ? 'passed' : 'pending',
        service: 'llama-server (LLM Inference)',
        action: 'Try chatting at localhost:3000',
        testUrl: '/api/test/llm'
      },
      {
        id: 'voice',
        name: 'Voice I/O',
        description: 'Speech-to-text and text-to-speech services are ready',
        icon: Mic,
        status: (serviceMap['Whisper (STT)'] === 'healthy' && serviceMap['Kokoro (TTS)'] === 'healthy') 
          ? 'passed' 
          : 'pending',
        service: 'Whisper + Kokoro',
        action: 'Check ODS Talk owner setup',
        testUrl: '/api/voice/status'
      },
      {
        id: 'documents',
        name: 'Document Chat (RAG)',
        description: 'Can upload and ask about files',
        icon: FileText,
        status: serviceMap['Qdrant (Vector DB)'] === 'healthy' ? 'passed' : 'pending',
        service: 'Qdrant (Vector DB)',
        action: 'Upload a PDF in Open WebUI',
        testUrl: '/api/test/rag'
      },
      {
        id: 'workflows',
        name: 'Workflows',
        description: 'Can automate tasks',
        icon: Zap,
        status: serviceMap['n8n (Workflows)'] === 'healthy' ? 'passed' : 'pending',
        service: 'n8n (Workflows)',
        action: 'Visit localhost:5678',
        testUrl: '/api/test/workflows'
      }
    ])
  }

  const runLiveTests = async () => {
    setRunning(true)
    
    const updatedTests = [...tests]
    
    for (let i = 0; i < updatedTests.length; i++) {
      if (updatedTests[i].status === 'passed') continue
      
      updatedTests[i] = { ...updatedTests[i], status: 'running' }
      setTests([...updatedTests])
      
      try {
        const response = await fetch(updatedTests[i].testUrl)
        const result = await response.json()
        const success = Boolean(result.success ?? result.available)
        
        await new Promise(r => setTimeout(r, 800)) // Visual feedback
        
        updatedTests[i] = { 
          ...updatedTests[i], 
          status: success ? 'passed' : 'failed',
          error: result.error
        }
      } catch (err) {
        await new Promise(r => setTimeout(r, 800))
        updatedTests[i] = { 
          ...updatedTests[i], 
          status: 'failed',
          error: 'Test endpoint not available'
        }
      }
      
      setTests([...updatedTests])
    }
    
    const passed = updatedTests.every(t => t.status === 'passed')
    setAllPassed(passed)
    if (passed) onAllPassed?.()
    
    setRunning(false)
  }

  const getStatusIcon = (test) => {
    if (test.status === 'running') {
      return <Loader2 className="w-5 h-5 text-theme-accent animate-spin" />
    }
    if (test.status === 'passed') {
      return <CheckCircle className="w-5 h-5 text-emerald-400" />
    }
    if (test.status === 'failed') {
      return <XCircle className="w-5 h-5 text-red-400" />
    }
    return <div className="w-5 h-5 rounded-full border-2 border-theme-border" />
  }

  const getStatusClass = (status) => {
    if (status === 'passed') return 'border-emerald-500/30 bg-emerald-500/5'
    if (status === 'failed') return 'border-red-500/30 bg-red-500/5'
    if (status === 'running') return 'border-theme-accent/30 bg-theme-accent/5'
    return 'border-theme-border bg-theme-card/30'
  }

  const passedCount = tests.filter(t => t.status === 'passed').length
  const totalCount = tests.length

  return (
    <div className="space-y-4">
      {/* Progress Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-theme-text">Feature Tests</h3>
          <p className="text-xs text-theme-text-muted mt-0.5">
            {passedCount === totalCount
              ? 'All features working!'
              : `${passedCount}/${totalCount} features ready`}
          </p>
        </div>
        <button
          onClick={runLiveTests}
          disabled={running}
          className="flex items-center gap-2 px-3 py-1.5 text-sm bg-theme-card hover:bg-theme-surface-hover text-theme-text rounded-lg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${running ? 'animate-spin' : ''}`} />
          {running ? 'Testing...' : 'Run Tests'}
        </button>
      </div>

      {/* Progress Bar */}
      <div className="h-2 bg-theme-border rounded-full overflow-hidden">
        <div 
          className="h-full bg-gradient-to-r from-emerald-500 to-teal-500 rounded-full transition-all duration-500"
          style={{ width: `${(passedCount / totalCount) * 100}%` }}
        />
      </div>

      {/* Test Cards */}
      <div className="space-y-2">
        {tests.map((test) => {
          const Icon = test.icon
          return (
            <div
              key={test.id}
              className={`flex items-start gap-3 p-3 rounded-lg border ${getStatusClass(test.status)} transition-all duration-300`}
            >
              <div className="mt-0.5">
                {getStatusIcon(test)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <Icon className="w-4 h-4 text-theme-text-muted" />
                  <span className="text-sm font-medium text-theme-text">{test.name}</span>
                </div>
                <p className="text-xs text-theme-text-secondary mt-0.5">{test.description}</p>
                
                {test.status === 'passed' && (
                  <p className="text-xs text-emerald-400 mt-1.5 flex items-center gap-1">
                    <CheckCircle className="w-3 h-3" />
                    {test.service} is healthy
                  </p>
                )}
                
                {test.status === 'failed' && test.error && (
                  <p className="text-xs text-red-400 mt-1.5">
                    Error: {test.error}
                  </p>
                )}
                
                {test.status !== 'passed' && test.action && (
                  <div className="mt-2 flex items-center gap-2">
                    <span className="text-xs text-theme-text-muted">Try:</span>
                    <span className="text-xs text-theme-accent-light">{test.action}</span>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Success Banner */}
      {allPassed && (
        <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
          <p className="text-sm font-medium text-emerald-300">
            ODS is fully operational.
          </p>
          <p className="text-xs text-emerald-200/70 mt-1">
            All features are working. You're ready to chat, use voice, upload documents, and create workflows.
          </p>
        </div>
      )}
    </div>
  )
}
