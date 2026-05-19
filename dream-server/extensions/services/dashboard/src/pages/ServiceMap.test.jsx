import { describe, expect, it } from 'vitest'
import { buildTopology } from './ServiceMap'

const statusPayload = {
  services: [
    { id: 'ape', name: 'APE (Agent Policy Engine)', status: 'healthy', port: 7890, uptime: 120 },
    { id: 'comfyui', name: 'ComfyUI (Image Generation)', status: 'healthy', port: 8188, uptime: 120 },
    { id: 'dashboard', name: 'Dashboard (Control Center)', status: 'healthy', port: 3001, uptime: 120 },
    { id: 'dashboard-api', name: 'Dashboard API (System Status)', status: 'healthy', port: 3002, uptime: 120 },
    { id: 'embeddings', name: 'TEI (Embeddings)', status: 'healthy', port: 8090, uptime: 120 },
    { id: 'langfuse', name: 'Langfuse (LLM Observability)', status: 'healthy', port: 3007, uptime: 120 },
    { id: 'llama-server', name: 'llama-server (LLM Inference)', status: 'healthy', port: 11434, uptime: 120 },
    { id: 'litellm', name: 'LiteLLM (API Gateway)', status: 'healthy', port: 4000, uptime: 120 },
    { id: 'n8n', name: 'n8n (Workflows)', status: 'healthy', port: 5678, uptime: 120 },
    { id: 'open-webui', name: 'Open WebUI (Chat)', status: 'healthy', port: 3000, uptime: 120 },
    { id: 'openclaw', name: 'OpenClaw (Agents)', status: 'healthy', port: 7860, uptime: 120 },
    { id: 'opencode', name: 'OpenCode (IDE)', status: 'healthy', port: 3003, uptime: 120 },
    { id: 'perplexica', name: 'Perplexica (Deep Research)', status: 'healthy', port: 3004, uptime: 120 },
    { id: 'privacy-shield', name: 'Privacy Shield (PII Protection)', status: 'healthy', port: 8085, uptime: 120 },
    { id: 'qdrant', name: 'Qdrant (Vector DB)', status: 'healthy', port: 6333, uptime: 120 },
    { id: 'searxng', name: 'SearXNG (Web Search)', status: 'healthy', port: 8888, uptime: 120 },
    { id: 'token-spy', name: 'Token Spy (Usage Monitor)', status: 'healthy', port: 3005, uptime: 120 },
    { id: 'tts', name: 'Kokoro (TTS)', status: 'healthy', port: 8880, uptime: 120 },
    { id: 'whisper', name: 'Whisper (STT)', status: 'healthy', port: 9000, uptime: 120 },
  ],
}

const expectedIds = [
  'ape',
  'comfyui',
  'dashboard',
  'dashboard-api',
  'embeddings',
  'langfuse',
  'llama-server',
  'litellm',
  'n8n',
  'open-webui',
  'openclaw',
  'opencode',
  'perplexica',
  'privacy-shield',
  'qdrant',
  'searxng',
  'token-spy',
  'tts',
  'whisper',
]

const expectedCategories = {
  'llama-server': 'core',
  qdrant: 'core',
  searxng: 'core',
  embeddings: 'core',
  whisper: 'core',
  tts: 'core',
  litellm: 'middleware',
  'dashboard-api': 'middleware',
  'token-spy': 'middleware',
  'privacy-shield': 'middleware',
  langfuse: 'middleware',
  ape: 'middleware',
  'open-webui': 'user-facing',
  perplexica: 'user-facing',
  n8n: 'user-facing',
  openclaw: 'user-facing',
  dashboard: 'user-facing',
  comfyui: 'user-facing',
  opencode: 'user-facing',
}

const expectedEdges = [
  ['open-webui', 'litellm', 'LLM proxy'],
  ['litellm', 'llama-server', 'inference'],
  ['perplexica', 'searxng', 'search'],
  ['perplexica', 'litellm', 'LLM proxy'],
  ['n8n', 'litellm', 'LLM proxy'],
  ['n8n', 'qdrant', 'vector store'],
  ['openclaw', 'litellm', 'LLM proxy'],
  ['openclaw', 'qdrant', 'vector store'],
  ['litellm', 'langfuse', 'observability'],
  ['qdrant', 'embeddings', 'embeddings'],
  ['open-webui', 'whisper', 'voice input'],
  ['open-webui', 'tts', 'voice output'],
  ['dashboard', 'dashboard-api', 'API'],
  ['dashboard-api', 'llama-server', 'API'],
  ['token-spy', 'litellm', 'intercept'],
  ['privacy-shield', 'litellm', 'privacy'],
  ['comfyui', 'open-webui', 'API'],
  ['ape', 'litellm', 'LLM proxy'],
]

describe('buildTopology', () => {
  it('uses /api/status service ids for categories and known edges', () => {
    const topology = buildTopology(statusPayload)

    expect(topology.nodes.map(node => node.id)).toEqual(expectedIds)
    for (const [id, category] of Object.entries(expectedCategories)) {
      expect(topology.nodes.find(node => node.id === id)?.category).toBe(category)
    }
    expect(topology.edges).toHaveLength(expectedEdges.length)
    expect(topology.edges).toEqual(expect.arrayContaining(
      expectedEdges.map(([source, target, label]) => expect.objectContaining({ source, target, label }))
    ))
  })

  it('does not collapse nodes when an older /api/status payload only has names', () => {
    const legacyPayload = {
      services: statusPayload.services.map(service => ({
        name: service.name,
        status: service.status,
        port: service.port,
        uptime: service.uptime,
      })),
    }

    const topology = buildTopology(legacyPayload)

    expect(topology.nodes).toHaveLength(statusPayload.services.length)
    expect(new Set(topology.nodes.map(node => node.id)).size).toBe(statusPayload.services.length)
    expect(topology.nodes.some(node => node.id === undefined)).toBe(false)
    expect(topology.nodes.map(node => node.id)).toEqual(expectedIds)
    expect(topology.edges).toHaveLength(expectedEdges.length)
    expect(topology.edges).toEqual(expect.arrayContaining(
      expectedEdges.map(([source, target, label]) => expect.objectContaining({ source, target, label }))
    ))
  })
})
