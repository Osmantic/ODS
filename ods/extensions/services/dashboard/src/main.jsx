import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ThemeProvider } from './contexts/ThemeContext'
import {
  clearStaleAssetRecovery,
  isStaleAssetError,
  recoverFromStaleAsset,
} from './utils/staleAssetRecovery'
import './index.css'

const RECOVERY_CLEAR_DELAY_MS = 30_000

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }
  componentDidMount() {
    this.recoveryTimer = globalThis.setTimeout(clearStaleAssetRecovery, RECOVERY_CLEAR_DELAY_MS)
  }
  componentWillUnmount() {
    globalThis.clearTimeout(this.recoveryTimer)
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }
  componentDidCatch(error, info) {
    console.error('Dashboard crash:', error, info.componentStack)
    if (recoverFromStaleAsset(error)) return
    this.setState({ stack: info.componentStack })
  }
  render() {
    if (this.state.hasError) {
      const staleAsset = isStaleAssetError(this.state.error)
      return (
        <div style={{ display: 'grid', placeItems: 'center', padding: '2rem', color: '#d4d4d8', background: '#09090b', minHeight: '100vh', fontFamily: 'system-ui, sans-serif' }}>
          <div style={{ width: 'min(32rem, 100%)', padding: '2rem', background: '#18181b', border: '1px solid #3f3f46', borderRadius: '16px' }}>
            <h1 style={{ color: '#fff', margin: '0 0 0.75rem', fontSize: '1.5rem' }}>
              {staleAsset ? 'Dashboard updated' : 'Something went wrong'}
            </h1>
            <p style={{ lineHeight: 1.6, margin: 0 }}>
              {staleAsset
                ? 'ODS refreshed its dashboard files. Reload once to continue with the latest version.'
                : 'The dashboard could not finish loading this screen. Your ODS services are still running.'}
            </p>
            <button onClick={() => globalThis.location.reload()}
              style={{ marginTop: '1.25rem', padding: '0.65rem 1rem', background: '#7c3aed', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}>
              Reload dashboard
            </button>
            {!staleAsset && this.state.error && (
              <details style={{ marginTop: '1.25rem', color: '#a1a1aa', fontFamily: 'monospace', fontSize: '0.8rem' }}>
                <summary style={{ cursor: 'pointer' }}>Technical details</summary>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{this.state.error.toString()}</pre>
              </details>
            )}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

globalThis.addEventListener?.('vite:preloadError', (event) => {
  if (recoverFromStaleAsset(event.payload)) event.preventDefault()
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ThemeProvider>
    </ErrorBoundary>
  </React.StrictMode>
)
