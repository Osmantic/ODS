import {readPixelReadiness, pixelReadinessView} from './pixelReadiness'

const observed = overrides => ({schemaVersion:1, state:'unverified', routeAvailable:true,
  accessState:'verified', effectiveMode:'sandboxed', releaseState:'unverified',
  reasonCode:'release-binding-unavailable', observedAt:new Date().toISOString(), ...overrides})

it('never turns verified access plus partial release into Ready', () => {
  const value = pixelReadinessView(observed(), true)
  expect(value).toMatchObject({label:'Available · unverified', access:'Verified', release:'Unverified', attention:false})
})

it('shows the exact failed Mac access check despite model route availability', () => {
  const value = observed({state:'attention', accessState:'failed', effectiveMode:'unknown', reasonCode:'access-inspection-failed'})
  expect(pixelReadinessView(value, true)).toMatchObject({label:'Needs attention', access:'Failed', attention:true})
  expect(pixelReadinessView(value, true).detail).toMatch(/host access inspection failed/)
})

it.each([undefined, null, {}, observed({schemaVersion:2}), observed({state:'ready'}),
  observed({releaseState:'verified'}), observed({accessState:'failed'}), observed({routeAvailable:false}),
  observed({observedAt:'2000-01-01T00:00:00.000Z'}), observed({observedAt:'invalid'}),
  observed({reasonCode:'private-upstream-secret'}), observed({effectiveMode:'root'}),
])('missing, old, stale and inconsistent status cannot render green: %j', value => {
  expect(readPixelReadiness(value, true)).toBeNull()
  expect(pixelReadinessView(value, true)).toMatchObject({label:'Available · unverified', access:'Unverified', release:'Unverified'})
  expect(JSON.stringify(pixelReadinessView(value, true))).not.toContain('private-upstream-secret')
})

it('retains both failed access and release drift, without inventing admission state', () => {
  const value = observed({state:'attention', accessState:'failed', effectiveMode:'unknown', releaseState:'mismatch', reasonCode:'access-inspection-failed'})
  expect(pixelReadinessView(value, true)).toMatchObject({access:'Failed', release:'Files changed', attention:true})
  expect(readPixelReadiness({...value, admissionHeld:false, token:'secret'}, true)).toEqual(readPixelReadiness(value, true))
})

it('does not retain earlier proof after a missing or failed observation', () => {
  expect(pixelReadinessView(observed(), true).access).toBe('Verified')
  expect(pixelReadinessView(undefined, true).access).toBe('Unverified')
  expect(pixelReadinessView(observed(), false).label).toBe('Unavailable')
})
