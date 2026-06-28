import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider } from '../contexts/ThemeContext'

function AllProviders({ children }) {
  return (
    <MemoryRouter>
      <ThemeProvider>{children}</ThemeProvider>
    </MemoryRouter>
  )
}

function customRender(ui, options) {
  return render(ui, { wrapper: AllProviders, ...options })
}

export * from '@testing-library/react'
export { customRender as render }
