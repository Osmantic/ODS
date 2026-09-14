import { act, fireEvent, render, screen } from '@testing-library/react'
import SplashScreen from '../SplashScreen' // eslint-disable-line no-unused-vars

vi.mock('gsap', () => {
  const createTimeline = () => {
    const timeline = {
      to: vi.fn(() => timeline),
      from: vi.fn(() => timeline),
      add: vi.fn(() => timeline),
      progress: vi.fn(() => timeline),
      timeScale: vi.fn(() => timeline),
      totalTime: vi.fn(() => timeline),
      paused: vi.fn(() => timeline),
    }

    return timeline
  }

  return {
    gsap: {
      registerPlugin: vi.fn(),
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

vi.mock('gsap/CustomEase', () => ({
  CustomEase: {
    create: vi.fn(() => 'custom-ease'),
  },
}))

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
    vi.unstubAllGlobals()
  })

  test('renders accessible loading dialog with skip control', () => {
    const { container } = render(<SplashScreen onComplete={() => {}} />)

    expect(screen.getByRole('dialog', { name: 'ODS' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'ODS', level: 1 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Skip splash screen' })).toBeInTheDocument()
    expect(container.querySelector('.ods-opening-orb')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelectorAll('.ell')).toHaveLength(31)
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
    act(() => vi.advanceTimersByTime(400))

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  test('finishes on time even when the parent refreshes its callback', () => {
    const onComplete = vi.fn()
    const view = render(<SplashScreen onComplete={() => {}} />)
    act(() => vi.advanceTimersByTime(2000))
    view.rerender(<SplashScreen onComplete={onComplete} />)
    act(() => vi.advanceTimersByTime(1600))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  test('preview stays open until dismissed and cleans its exit timer on unmount', () => {
    const onComplete = vi.fn()
    const view = render(<SplashScreen preview onComplete={onComplete} />)
    act(() => vi.advanceTimersByTime(10000))
    expect(onComplete).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', {name: 'Skip splash screen'}))
    view.unmount()
    act(() => vi.advanceTimersByTime(400))
    expect(onComplete).not.toHaveBeenCalled()
  })
})
