import {render,screen,fireEvent,cleanup} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import PortalMascotSettings from './PortalMascotSettings'
import {PORTAL_MASCOT_KEY,normalizeMascotPreferences} from '../../lib/portalMascotPreferences'

afterEach(()=>{cleanup();localStorage.removeItem(PORTAL_MASCOT_KEY);vi.restoreAllMocks()})
it('saves preferences, restores them on remount, previews every state and resets defaults',()=>{
  const view=render(<PortalMascotSettings/>)
  expect(screen.getByLabelText('Sleep after inactivity')).toHaveValue('60')
  fireEvent.change(screen.getByLabelText('Sleep after inactivity'),{target:{value:'15'}})
  fireEvent.click(screen.getByRole('checkbox',{name:/Show mascot/}))
  expect(JSON.parse(localStorage.getItem(PORTAL_MASCOT_KEY))).toEqual({enabled:false,animated:true,sleepAfterSeconds:15})
  for(const label of ['Idle','Thinking','Working','Waiting','Attention','Done','Sleeping']) {
    fireEvent.click(screen.getByRole('button',{name:label,exact:true}))
    expect(screen.getByRole('button',{name:label,exact:true})).toHaveAttribute('aria-pressed','true')
  }
  expect(screen.getByRole('button',{name:'Play with Portal'})).toBeVisible()
  view.unmount();render(<PortalMascotSettings/>)
  expect(screen.getByRole('checkbox',{name:/Show mascot/})).not.toBeChecked()
  expect(screen.getByLabelText('Sleep after inactivity')).toHaveValue('15')
  fireEvent.click(screen.getByRole('button',{name:'Reset mascot preferences'}))
  expect(screen.getByRole('checkbox',{name:/Show mascot/})).toBeChecked()
  expect(screen.getByLabelText('Sleep after inactivity')).toHaveValue('60')
})
it('normalizes malformed settings and reports blocked storage instead of claiming a save',()=>{
  expect(normalizeMascotPreferences({enabled:'no',animated:null,sleepAfterSeconds:-1})).toEqual({enabled:true,animated:true,sleepAfterSeconds:60})
  localStorage.setItem(PORTAL_MASCOT_KEY,'not json')
  render(<PortalMascotSettings/>)
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('blocked')})
  fireEvent.click(screen.getByRole('checkbox',{name:/Show mascot/}))
  expect(screen.getByRole('alert')).toHaveTextContent('Could not save')
  expect(screen.getByRole('checkbox',{name:/Show mascot/})).toBeChecked()
})
