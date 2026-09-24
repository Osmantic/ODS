import { screen } from '@testing-library/react'
import { render } from '../test/test-utils'
import { AssignmentTable } from './AssignmentTable' // eslint-disable-line no-unused-vars

// The shapes below mirror what scripts/assign_gpus.py emits and what
// bin/ods-host-agent.py persists. Keep them in sync with select_parallelism()
// and the strategy values the planner and `ods gpu reassign` write.

function assignment(strategy, parallelism) {
  return {
    version: '1.0',
    strategy,
    services: {
      llama_server: {
        gpus: ['GPU-00000000-0000-0000-0000-000000000000'],
        gpu_indices: [0],
        parallelism,
      },
    },
  }
}

describe('AssignmentTable parallelism labels', () => {
  it('labels a hybrid plan instead of showing the raw mode string', () => {
    render(<AssignmentTable assignment={assignment('dedicated', {
      mode: 'hybrid',
      tensor_parallel_size: 2,
      pipeline_parallel_size: 4,
      gpu_memory_utilization: 0.93,
    })} />)

    expect(screen.getByText(/Hybrid Tensor \+ Pipeline/)).toBeInTheDocument()
    expect(screen.getByText(/tp=2/)).toBeInTheDocument()
    expect(screen.getByText(/pp=4/)).toBeInTheDocument()
  })

  it.each([
    ['none', 'Single Process'],
    ['tensor', 'Tensor Parallel'],
    ['pipeline', 'Pipeline Parallel'],
    ['hybrid', 'Hybrid Tensor + Pipeline'],
  ])('labels the %s mode', (mode, label) => {
    render(<AssignmentTable assignment={assignment('dedicated', {
      mode,
      tensor_parallel_size: 1,
      pipeline_parallel_size: 1,
      gpu_memory_utilization: 0.95,
    })} />)

    expect(screen.getByText(new RegExp(label.replace('+', '\\+')))).toBeInTheDocument()
  })

  it('falls back to the raw mode for a value the UI does not know', () => {
    render(<AssignmentTable assignment={assignment('dedicated', {
      mode: 'some-future-mode',
      tensor_parallel_size: 1,
      pipeline_parallel_size: 1,
      gpu_memory_utilization: 0.95,
    })} />)

    expect(screen.getByText(/some-future-mode/)).toBeInTheDocument()
  })
})

describe('AssignmentTable strategy badge', () => {
  const NEUTRAL = 'bg-zinc-700'

  it.each(['single', 'colocated', 'dedicated', 'manual'])(
    'gives the %s strategy its own badge rather than the neutral fallback',
    (strategy) => {
      render(<AssignmentTable assignment={assignment(strategy, {
        mode: 'none',
        tensor_parallel_size: 1,
        pipeline_parallel_size: 1,
        gpu_memory_utilization: 0.95,
      })} />)

      const badge = screen.getByText(strategy)
      expect(badge).toBeInTheDocument()
      expect(badge.className).not.toContain(NEUTRAL)
    },
  )

  it('uses the neutral badge for an unrecognised strategy', () => {
    render(<AssignmentTable assignment={assignment('something-new', {
      mode: 'none',
      tensor_parallel_size: 1,
      pipeline_parallel_size: 1,
      gpu_memory_utilization: 0.95,
    })} />)

    const badge = screen.getByText('something-new')
    expect(badge.className).toContain(NEUTRAL)
  })
})
