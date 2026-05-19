import { useState } from 'react'
import { AlertCircle, ChevronDown, ChevronUp, Terminal, Copy, Check } from 'lucide-react'

const commonIssues = [
  {
    id: 'port-conflict',
    title: 'Port already in use',
    symptoms: ['Error: port 3000 already in use', 'Cannot start service on port X'],
    cause: 'Another program is using the required port',
    solutions: [
      {
        title: 'Find and stop the conflicting service',
        command: 'lsof -i :3000  # Replace 3000 with your port',
        description: 'Shows which process is using the port'
      },
      {
        title: 'Use different ports',
        command: '# Edit .env file\nWEBUI_PORT=3005\nDASHBOARD_PORT=3006',
        description: 'Change ports in .env and restart'
      }
    ]
  },
  {
    id: 'gpu-not-detected',
    title: 'GPU not detected',
    symptoms: ['No GPU detected', 'CPU-only mode active', 'Slow inference'],
    cause: 'GPU drivers or container runtime not configured',
    solutions: [
      {
        title: 'NVIDIA: Install Container Toolkit',
        command: '# Ubuntu/Debian\ncurl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg\n\n# Then restart Docker\nsudo systemctl restart docker',
        description: 'Required for NVIDIA GPU access in containers'
      },
      {
        title: 'AMD: Check device access and user groups',
        command: '# Ensure your user has GPU access\nsudo usermod -aG render,video $USER\n# Then log out and back in\n\n# Verify GPU is visible\nrocminfo | head -20',
        description: 'Required for AMD ROCm GPU access (/dev/kfd and /dev/dri)'
      },
      {
        title: 'Verify GPU is visible',
        command: '# NVIDIA:\nnvidia-smi\n\n# AMD:\nrocminfo | head -20',
        description: 'Should show your GPU details'
      }
    ]
  },
  {
    id: 'model-loading',
    title: 'Model loading slowly or failing',
    symptoms: ['Connection error in Open WebUI', 'llama-server unhealthy', 'Chat not responding'],
    cause: 'Model download incomplete or VRAM exhausted',
    solutions: [
      {
        title: 'Check model download progress',
        command: 'ls -lh ~/dream-server/data/models/',
        description: 'Verify model files exist and have size > 1GB'
      },
      {
        title: 'Check VRAM usage',
        command: 'nvidia-smi | head -20',
        description: 'Look for processes using GPU memory'
      },
      {
        title: 'Use smaller model tier',
        command: '# Edit .env\nGPU_TIER=minimal  # Uses Qwen 1.5B instead of 32B',
        description: 'For GPUs with <16GB VRAM'
      }
    ]
  },
  {
    id: 'voice-not-working',
    title: 'Voice chat not working',
    symptoms: ['Cannot connect to voice', 'Microphone not detected', 'No audio output'],
    cause: 'LiveKit not started or browser permissions blocked',
    solutions: [
      {
        title: 'Start voice services',
        command: 'cd ~/dream-server && docker compose up -d whisper tts',
        description: 'LiveKit and voice agent must be running'
      },
      {
        title: 'Check browser permissions',
        command: '# In browser:\n# 1. Click lock icon in address bar\n# 2. Allow microphone access\n# 3. Refresh page',
        description: 'Browsers block mic by default'
      }
    ]
  },
  {
    id: 'docker-not-running',
    title: 'Docker not running or accessible',
    symptoms: ['Cannot connect to Docker daemon', 'docker: command not found', 'Permission denied'],
    cause: 'Docker service stopped or user not in docker group',
    solutions: [
      {
        title: 'Start Docker service',
        command: 'sudo systemctl start docker',
        description: 'Start the Docker daemon'
      },
      {
        title: 'Add user to docker group',
        command: 'sudo usermod -aG docker $USER && newgrp docker',
        description: 'Required for non-root Docker access'
      }
    ]
  }
]

export function TroubleshootingAssistant({ serviceStatus }) {
  const [expanded, setExpanded] = useState(null)
  const [copied, setCopied] = useState(null)
  const [search, setSearch] = useState('')

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text)
    setCopied(id)
    setTimeout(() => setCopied(null), 2000)
  }

  const filteredIssues = search 
    ? commonIssues.filter(i => 
        i.title.toLowerCase().includes(search.toLowerCase()) ||
        i.symptoms.some(s => s.toLowerCase().includes(search.toLowerCase()))
      )
    : commonIssues

  // Auto-expand issues matching current service errors
  const unhealthyServices = serviceStatus?.services?.filter(s => s.status !== 'healthy') || []
  const relevantIssues = commonIssues.filter(issue => {
    if (issue.id === 'gpu-not-detected' && unhealthyServices.some(s => s.name.includes('llama-server'))) return true
    if (issue.id === 'voice-not-working' && unhealthyServices.some(s => s.name.includes('LiveKit'))) return true
    if (issue.id === 'model-loading' && unhealthyServices.some(s => s.name.includes('llama-server'))) return true
    return false
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <AlertCircle className="w-5 h-5 text-amber-400" />
        <h3 className="text-sm font-medium text-theme-text">Troubleshooting Assistant</h3>
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="Search issues..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full px-3 py-2 bg-theme-card border border-theme-border rounded-lg text-sm text-theme-text placeholder-theme-text-muted focus:outline-none focus:border-theme-accent"
      />

      {/* Relevant issues first */}
      {relevantIssues.length > 0 && !search && (
        <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
          <p className="text-xs text-amber-300 font-medium mb-2">Detected potential issues:</p>
          <div className="space-y-1">
            {relevantIssues.map(issue => (
              <button
                key={issue.id}
                onClick={() => setExpanded(expanded === issue.id ? null : issue.id)}
                className="w-full text-left text-sm text-amber-200 hover:text-amber-100 flex items-center gap-2"
              >
                <ChevronDown className="w-3 h-3" />
                {issue.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Issue List */}
      <div className="space-y-2">
        {filteredIssues.map((issue) => (
          <div
            key={issue.id}
            className={`border rounded-lg overflow-hidden transition-all ${
              expanded === issue.id 
                ? 'border-theme-border bg-theme-card/50'
                : 'border-theme-border hover:border-theme-border'
            }`}
          >
            <button
              onClick={() => setExpanded(expanded === issue.id ? null : issue.id)}
              className="w-full flex items-center justify-between p-3 text-left"
            >
              <div>
                <span className="text-sm font-medium text-theme-text">{issue.title}</span>
                {relevantIssues.includes(issue) && (
                  <span className="ml-2 text-xs text-amber-400">(may be relevant)</span>
                )}
              </div>
              {expanded === issue.id ? (
                <ChevronUp className="w-4 h-4 text-theme-text-muted" />
              ) : (
                <ChevronDown className="w-4 h-4 text-theme-text-muted" />
              )}
            </button>

            {expanded === issue.id && (
              <div className="px-3 pb-3 space-y-3">
                {/* Symptoms */}
                <div>
                  <p className="text-xs text-theme-text-muted mb-1">Symptoms:</p>
                  <ul className="space-y-0.5">
                    {issue.symptoms.map((symptom, i) => (
                      <li key={i} className="text-xs text-theme-text-secondary flex items-center gap-1">
                        <span className="text-theme-text-muted">•</span> {symptom}
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Cause */}
                <div>
                  <p className="text-xs text-theme-text-muted mb-1">Likely cause:</p>
                  <p className="text-xs text-theme-text-secondary">{issue.cause}</p>
                </div>

                {/* Solutions */}
                <div className="space-y-2">
                  <p className="text-xs text-theme-text-muted">Solutions:</p>
                  {issue.solutions.map((solution, i) => (
                    <div key={i} className="bg-theme-card rounded p-2">
                      <p className="text-xs font-medium text-theme-text mb-1">{solution.title}</p>
                      <p className="text-xs text-theme-text-muted mb-2">{solution.description}</p>
                      
                      {solution.command && (
                        <div className="relative">
                          <pre className="bg-zinc-950 p-2 rounded text-xs text-theme-text-secondary overflow-x-auto font-mono">
                            {solution.command}
                          </pre>
                          <button
                            onClick={() => copyToClipboard(solution.command, `${issue.id}-${i}`)}
                            className="absolute top-1 right-1 p-1 bg-theme-card hover:bg-theme-surface-hover rounded text-theme-text-muted hover:text-theme-text transition-colors"
                          >
                            {copied === `${issue.id}-${i}` ? (
                              <Check className="w-3 h-3 text-emerald-400" />
                            ) : (
                              <Copy className="w-3 h-3" />
                            )}
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {filteredIssues.length === 0 && (
        <p className="text-sm text-theme-text-muted text-center py-4">
          No issues found matching "{search}"
        </p>
      )}

      {/* Help footer */}
      <div className="pt-3 border-t border-theme-border">
        <p className="text-xs text-theme-text-muted">
          Still stuck? Check the{' '}
          <a 
            href="https://github.com/Light-Heart-Labs/DreamServer/tree/main/dream-server#troubleshooting" 
            target="_blank"
            rel="noopener noreferrer"
            className="text-theme-accent hover:text-theme-accent-light"
          >
            full troubleshooting guide
          </a>
          {' '}or run{' '}
          <code className="bg-theme-card px-1 py-0.5 rounded text-theme-text-muted">./scripts/dream-test.sh</code>
        </p>
      </div>
    </div>
  )
}
