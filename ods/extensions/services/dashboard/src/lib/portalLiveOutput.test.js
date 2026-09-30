import {liveModelOutputTokens} from './portalLiveOutput'

const local = {source:'local-switchboard', model:'local', contextLength:65536}
const status = (inference, stale=false) => ({inference, clientTelemetry:{sampledAt:1, stale}})

it('shows only a fresh measured count from the local route while it is generating', () => {
  expect(liveModelOutputTokens(local, status({inferenceActive:true, liveOutputTokens:3412}))).toBe(3412)
  expect(liveModelOutputTokens(local, status({inferenceActive:true, liveOutputTokens:3412}, true))).toBeNull()
  expect(liveModelOutputTokens(local, status({inferenceActive:false, liveOutputTokens:3412}))).toBeNull()
  expect(liveModelOutputTokens(local, status({inferenceActive:true, liveOutputTokens:null}))).toBeNull()
  expect(liveModelOutputTokens(local, status({inferenceActive:true, liveOutputTokens:0}))).toBeNull()
  expect(liveModelOutputTokens(local, status({inferenceActive:true, liveOutputTokens:'3412'}))).toBeNull()
  expect(liveModelOutputTokens(local, null)).toBeNull()
})

it('never attributes the local runtime counter to a remote or external model route', () => {
  const generating = status({inferenceActive:true, liveOutputTokens:3412})
  expect(liveModelOutputTokens({...local, source:'remote-provider'}, generating)).toBeNull()
  expect(liveModelOutputTokens({...local, source:'external-host'}, generating)).toBeNull()
  expect(liveModelOutputTokens(null, generating)).toBeNull()
})
