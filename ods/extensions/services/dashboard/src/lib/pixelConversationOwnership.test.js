import { describe, it, expect, vi } from 'vitest';
import { createConversationOwnership } from './pixelConversationOwnership.js';

function createFakeLocks() {
  const held = new Map();
  return {
    held,
    request: vi.fn((name, opts, cb) => {
      if (held.has(name)) {
        return Promise.resolve(cb(null));
      }
      held.set(name, true);
      const result = cb({ name, mode: opts.mode });
      const cleanup = () => { held.delete(name); };
      if (result && typeof result.then === 'function') {
        return result.then(
          (v) => { cleanup(); return v; },
          (e) => { cleanup(); throw e; }
        );
      }
      cleanup();
      return Promise.resolve(result);
    }),
  };
}

// Locks that defer the callback until the test explicitly flushes it.
function createDeferredLocks() {
  const held = new Map();
  const pending = [];
  return {
    held,
    pending,
    request: vi.fn((name, opts, cb) => {
      if (held.has(name)) {
        return Promise.resolve(cb(null));
      }
      held.set(name, true);
      let resolveOuter;
      const outer = new Promise((res) => { resolveOuter = res; });
      pending.push({
        name,
        cb,
        grant() {
          const result = cb({ name, mode: opts.mode });
          if (result && typeof result.then === 'function') {
            result.then(
              (v) => { held.delete(name); resolveOuter(v); },
              (e) => { held.delete(name); resolveOuter(Promise.reject(e)); }
            );
          } else {
            held.delete(name);
            resolveOuter(result);
          }
        },
        deny() {
          const result = cb(null);
          held.delete(name);
          resolveOuter(result);
        },
      });
      return outer;
    }),
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('createConversationOwnership', () => {
  it('two controllers contend for same host lock', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    const b = createConversationOwnership(locks);
    const ra = await a.acquire('chat1');
    expect(ra).toEqual({ owned: true });
    expect(a.owns('chat1')).toBe(true);
    const rb = await b.acquire('chat1');
    expect(rb).toEqual({ owned: false, reason: 'locked' });
    expect(b.owns('chat1')).toBe(false);
  });

  it('release then takeover', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    const b = createConversationOwnership(locks);
    await a.acquire('chat1');
    a.release();
    await tick();
    const rb = await b.acquire('chat1');
    expect(rb).toEqual({ owned: true });
    expect(b.owns('chat1')).toBe(true);
  });

  it('different chat locks are independent', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    const b = createConversationOwnership(locks);
    expect(await a.acquire('chat1')).toEqual({ owned: true });
    expect(await b.acquire('chat2')).toEqual({ owned: true });
    expect(a.owns('chat1')).toBe(true);
    expect(b.owns('chat2')).toBe(true);
  });

  it('same pending/held acquisition reuse', async () => {
    const locks = createDeferredLocks();
    const a = createConversationOwnership(locks);
    const p1 = a.acquire('chat1');
    const p2 = a.acquire('chat1');
    expect(p1).toBe(p2);
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(a.owns('chat1')).toBe(false);
    locks.pending[0].grant();
    expect(await p1).toEqual({ owned: true });
    const p3 = a.acquire('chat1');
    expect(p3).toBe(p1);
  });

  it('switching A to B releases A lock so a third controller can acquire A', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    const b = createConversationOwnership(locks);
    const c = createConversationOwnership(locks);
    expect(await a.acquire('chatA')).toEqual({ owned: true });
    expect(await a.acquire('chatB')).toEqual({ owned: true });
    expect(a.owns('chatA')).toBe(false);
    expect(a.owns('chatB')).toBe(true);
    await tick();
    expect(await c.acquire('chatA')).toEqual({ owned: true });
    expect(c.owns('chatA')).toBe(true);
    // b is unused but ensures no cross-talk
    expect(b.owns('chatA')).toBe(false);
  });

  it('release while acquisition pending settles admission immediately', async () => {
    const locks = createDeferredLocks();
    const a = createConversationOwnership(locks);
    const p = a.acquire('chat1');
    expect(locks.pending.length).toBe(1);
    a.release();
    const result = await p;
    expect(result).toEqual({ owned: false, reason: 'released' });
    expect(a.owns('chat1')).toBe(false);
    // Now flush the stale grant; must not own or hold.
    locks.pending[0].grant();
    await tick();
    expect(a.owns('chat1')).toBe(false);
    expect(locks.held.has('ods:pixel-chat-writer:chat1')).toBe(false);
  });

  it('switch while acquisition pending settles old admission and ignores stale grant', async () => {
    const locks = createDeferredLocks();
    const a = createConversationOwnership(locks);
    const p1 = a.acquire('chat1');
    expect(locks.pending.length).toBe(1);
    const p2 = a.acquire('chat2');
    expect(await p1).toEqual({ owned: false, reason: 'released' });
    // Flush stale grant for chat1.
    locks.pending[0].grant();
    await tick();
    expect(a.owns('chat1')).toBe(false);
    expect(locks.held.has('ods:pixel-chat-writer:chat1')).toBe(false);
    // Now grant chat2.
    locks.pending[1].grant();
    expect(await p2).toEqual({ owned: true });
    expect(a.owns('chat2')).toBe(true);
  });

  it('dispose while acquisition pending settles admission as disposed', async () => {
    const locks = createDeferredLocks();
    const a = createConversationOwnership(locks);
    const p = a.acquire('chat1');
    a.dispose();
    expect(await p).toEqual({ owned: false, reason: 'disposed' });
    locks.pending[0].grant();
    await tick();
    expect(a.owns('chat1')).toBe(false);
    expect(locks.held.has('ods:pixel-chat-writer:chat1')).toBe(false);
  });

  it('unavailable manager fails closed', async () => {
    const a = createConversationOwnership(null);
    expect(await a.acquire('chat1')).toEqual({ owned: false, reason: 'unavailable' });
    const b = createConversationOwnership({});
    expect(await b.acquire('chat1')).toEqual({ owned: false, reason: 'unavailable' });
  });

  it('sync throw from locks.request fails closed', async () => {
    const locks = { request: vi.fn(() => { throw new Error('boom'); }) };
    const a = createConversationOwnership(locks);
    expect(await a.acquire('chat1')).toEqual({ owned: false, reason: 'unavailable' });
    expect(a.owns('chat1')).toBe(false);
  });

  it('native rejection returns deterministic failure', async () => {
    const locks = { request: vi.fn(() => Promise.reject(new Error('boom'))) };
    const a = createConversationOwnership(locks);
    expect(await a.acquire('chat1')).toEqual({ owned: false, reason: 'unavailable' });
    expect(a.owns('chat1')).toBe(false);
    const next = a.acquire('chat1');
    expect(await next).toEqual({ owned: false, reason: 'unavailable' });
    expect(locks.request).toHaveBeenCalledTimes(2);
  });

  it('native rejection after grant clears ownership and releases hold', async () => {
    let rejectOuter;
    let hold;
    const locks = {
      request: vi.fn((name, opts, cb) => {
        hold = cb({ name, mode: opts.mode });
        return new Promise((_, rej) => { rejectOuter = rej; });
      }),
    };
    const a = createConversationOwnership(locks);
    const p = a.acquire('chat1');
    expect(await p).toEqual({ owned: true });
    expect(a.owns('chat1')).toBe(true);
    rejectOuter(new Error('late'));
    await tick();
    expect(a.owns('chat1')).toBe(false);
    await hold;
  });

  it('a synchronous native throw after grant releases the granted hold', async () => {
    let hold;
    const locks = { request: vi.fn((name,opts,cb) => {
      hold = cb({name,mode:opts.mode});
      throw new Error('after callback');
    }) };
    const a = createConversationOwnership(locks);
    // Admission already succeeded; ownership must nevertheless be revoked.
    expect(await a.acquire('chat1')).toEqual({owned:true});
    expect(a.owns('chat1')).toBe(false);
    await hold;
  });

  it('a denied acquisition can be retried after the other author releases', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    const b = createConversationOwnership(locks);
    await a.acquire('chat1');
    expect(await b.acquire('chat1')).toEqual({owned:false,reason:'locked'});
    a.release();
    await tick();
    expect(await b.acquire('chat1')).toEqual({owned:true});
  });

  it('dispose is final and blocks further acquire', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    await a.acquire('chat1');
    a.dispose();
    expect(a.owns('chat1')).toBe(false);
    expect(await a.acquire('chat1')).toEqual({ owned: false, reason: 'disposed' });
  });

  it('invalid chat id rejected', async () => {
    const locks = createFakeLocks();
    const a = createConversationOwnership(locks);
    expect(await a.acquire('')).toEqual({ owned: false, reason: 'invalid-chat-id' });
    expect(await a.acquire('bad id!')).toEqual({ owned: false, reason: 'invalid-chat-id' });
    expect(await a.acquire('a'.repeat(129))).toEqual({ owned: false, reason: 'invalid-chat-id' });
  });
});
