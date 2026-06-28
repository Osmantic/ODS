import { fireEvent, render, screen } from '@testing-library/react'
import SplashScreen from '../SplashScreen' // eslint-disable-line no-unused-vars

vi.mock('gsap', () => {
  const createTimeline = () => {
    const timeline = {
      to: vi.fn(() => timeline),
      from: vi.fn(() => timeline),
      progress: vi.fn(() => timeline),
      timeScale: vi.fn(() => timeline),
    }

    return timeline
  }

  return {
    gsap: {
      context: (callback) => {
        callback()
        return { revert: vi.fn() }
      },
      set: vi.fn(),
      timeline: vi.fn(createTimeline),
      utils: {
        interpolate: vi.fn(() => () => '#ffffff'),
      },
    },
  }
})

describe('SplashScreen', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('renders accessible loading dialog with skip control', () => {
    render(<SplashScreen onComplete={() => {}} />)

    expect(screen.getByRole('dialog', { name: 'ODS' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'ODS', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Skip splash screen' })).toBeInTheDocument()
  })

  test('completes immediately when reduced motion is requested', () => {
    const onComplete = vi.fn()
    globalThis.matchMedia = vi.fn(() => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))

    render(<SplashScreen onComplete={onComplete} />)

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  test('skip button completes the splash only once', () => {
    const onComplete = vi.fn()

    render(<SplashScreen onComplete={onComplete} />)

    fireEvent.click(screen.getByRole('button', { name: 'Skip splash screen' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    vi.advanceTimersByTime(300)

    expect(onComplete).toHaveBeenCalledTimes(1)
  })
})
