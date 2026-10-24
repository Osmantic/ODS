import React, {Component} from 'react'
import ReactDOM from 'react-dom/client'
import {BrowserRouter} from 'react-router-dom'
import {ThemeProvider} from './contexts/ThemeContext'
import Pixel from './pages/Pixel'
import PixelConversationNavigation from './components/PixelConversationNavigation'
import './index.css'
import './pixel-workspace.css'
import './wallpaper-themes.css'
import './native-portal.css'

class NativeBoundary extends Component {
  state={failed:false}
  static getDerivedStateFromError(){return {failed:true}}
  render(){return this.state.failed ? <main className="native-portal-failure"><h1>Portal could not open</h1><p>Your saved conversations are kept. Close this window and open ODS again to retry.</p></main> : this.props.children}
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <NativeBoundary><ThemeProvider><BrowserRouter>
    <div className="pixel-app native-portal-shell bg-theme-bg text-theme-text">
      <aside className="native-portal-sidebar"><a href="/" className="native-portal-brand">ODS <span>Windows preview</span></a><PixelConversationNavigation nativeChat /></aside>
      <main className="pixel-workspace native-portal-main"><Pixel nativeChat /></main>
    </div>
  </BrowserRouter></ThemeProvider></NativeBoundary>,
)
