// Elf of Doom — AI, updates, rendering, UI wiring
"use strict";

// ---------- movement input (keyboard only) ----------
function moveVec() {
  let dx = 0, dy = 0;
  if (keys["arrowleft"] || keys["a"]) dx -= 1;
  if (keys["arrowright"] || keys["d"]) dx += 1;
  if (keys["arrowup"] || keys["w"]) dy -= 1;
  if (keys["arrowdown"] || keys["s"]) dy += 1;
  const l = Math.hypot(dx, dy);
  return { dx: l ? dx / l : 0, dy: l ? dy / l : 0, l };
}

// ---------- enemy AI ----------
// generation token: invalidates any pending shot once the arena is rebuilt
let AI_GEN = 0;
const PENDING_SHOTS = [];
const MAX_PROJECTILES = 220;

function clearPendingShots() {
  for (const p of PENDING_SHOTS) clearTimeout(p.id);
  PENDING_SHOTS.length = 0;
}
function _removePendingShot(rec) {
  // swap-and-pop: no O(n) splice churn while arrows fly
  const k = rec.idx;
  if (k == null || k >= PENDING_SHOTS.length || PENDING_SHOTS[k] !== rec) return;
  const last = PENDING_SHOTS.pop();
  if (last !== rec) { last.idx = k; PENDING_SHOTS[k] = last; }
  rec.idx = null;
}
function scheduleEnemyShot(e, gen, spawn) {
  if (PENDING_SHOTS.length > 80) return;   // hard cap: no unbounded timer growth
  const rec = { gen, idx: PENDING_SHOTS.length };
  rec.id = setTimeout(() => {
    _removePendingShot(rec);
    if (gen !== AI_GEN || G.state !== "play" || e.dead) return;
    spawn();
  }, 340);
  PENDING_SHOTS.push(rec);
}

// LOS cache: a full exact line sample costs ~20-60 circle tests, so each enemy
// re-samples at most every ~90 ms. The gate is the frame clock (G.t), never
// performance.now(): with a real wall clock, one slow frame makes every cached entry
// stale at once and the whole crowd re-samples in that same frame.
const LOS_CACHE = .09;
const LOS_STEP = 14, LOS_MAX = 46;
function losToHero(e) {
  if (e._losAt !== undefined && G.t - e._losAt < LOS_CACHE) return e._los;
  e._losAt = G.t;
  if (!D) { e._los = true; return e._los; }
  const x0 = e.x, y0 = e.y - 14, x1 = P.x, y1 = P.y - 14;
  const dx = x1 - x0, dy = y1 - y0;
  const len = Math.hypot(dx, dy);
  let ok = true;
  if (len > 1) {
    const steps = len < LOS_MAX * LOS_STEP ? Math.ceil(len / LOS_STEP) : LOS_MAX;
    for (let i = 1; i < steps; i++) {
      const t = i / steps;
      if (D.circleBlocked(x0 + dx * t, y0 + dy * t, 5)) { ok = false; break; }
    }
  }
  e._los = ok;
  return ok;
}

// Budget for the expensive per-enemy geometry work (wall-slide recovery, wedge
// relocation, unstick search) in ONE update. A normal brawl stays under it, so nothing
// is ever deferred; a big brawl spreads the recovery work over the next couple of
// frames instead of paying for every brute in one tick. That single-tick spike — dozens of
// enemies each running an unbounded geometry search in the same frame — is what froze
// the game for seconds and then jumped ahead.
const AI_WORK_BUDGET = 900;
let AI_MS_BUDGET = 7;                   // ms of AI geometry work allowed in one frame (adaptive)
let _aiWork = 0, _aiSkips = 0, _aiSkipAcc = 0, _aiClock = 0, _aiDeadline = 0, _aiElapsed = 0;
function aiSpend(n) { _aiWork += n; return _aiWork <= AI_WORK_BUDGET && performance.now() < _aiDeadline; }
// Skipped work is remembered and paid back first on the NEXT frame. Without that, a
// brawl that exhausts the budget keeps skipping the same tail-of-array enemies every
// frame, so they grind into a wall indefinitely, their stuckT runs away, and they all
// trip the wedge-relocation path in the same frame later. That is the "few seconds of
// freeze then a jump" pattern: defer-spike, defer-spike, crowd-wide relocate.
function aiSpendOrSkip(n) {
  // The wall-clock deadline is the real guarantee. The operation count above is only a
  // cheap pre-filter: circleBlocked/nearestFloor cost wildly different amounts depending
  // on how much wall sits under the entity, so a purely synthetic budget could be "spent"
  // by a crowd all grinding one pillar in a single tick. Once the deadline passes, the
  // rest of the crowd defers to the next frame and pays its debt first.
  if (performance.now() >= _aiDeadline) { _aiSkips++; return false; }
  if (_aiWork + n <= AI_WORK_BUDGET) { _aiWork += n; return true; }
  // Deferred work is paid back FIRST on the following frames instead of being dropped.
  // Without that, a brawl that exhausts the budget keeps skipping the same tail-of-array
  // enemies every frame, so they grind into a wall indefinitely, their stuckT runs away,
  // and they all trip the wedge-relocation path in the same frame later. That is the
  // "few seconds of freeze then a jump" pattern: defer-spike, defer-spike, crowd-wide
  // relocate. Note this must be tested BEFORE adding n to _aiWork: the old version
  // charged the budget even for skipped calls, which pinned _aiWork above the budget and
  // starved the debt to zero — so nothing was ever repaid and the spike kept building.
  if (_aiSkipAcc > 0) { _aiWork += n; _aiSkipAcc--; return true; }
  _aiWork += n; _aiSkips++;
  return false;
}
// True only when this enemy may run a wedge relocation this frame. Relocation calls
// D.freeSpot()/D.nearestFloor(), which scan dozens of tiles with several samples each: a
// single one is the priciest thing the AI does. The old version let any sufficiently stale
// enemy jump the budget whenever _aiSkips was low, so one big hit could wedge a whole pack
// into a wall and every one of them then paid for that search in the same handful of frames
// — that is the multi-second freeze followed by a jump. Relocations are now both budgeted
// AND limited to a couple per frame, and the stalest enemies qualify first, so a wedged pack
// still always unwedges (over a frame or two) instead of locking the tab.
const AI_RELOCATE_BUDGET = 2;
let _aiRelocates = 0;
function aiDesperate(e) {
  if (_aiRelocates <= 0) return false;
  // 2.2s of continuous wedging, not 0.1s: a single slow frame can add an entire clamped
  // dt (up to 33ms) to every stuckT at once, and with a low threshold that released a
  // whole pack into freeSpot() searches in one tick.
  if ((e.stuckT || 0) < 2.2) return false;
  if (!aiSpendOrSkip(60)) return false;
  _aiRelocates--;
  return true;
}

// Rotating cursor for the per-frame AI passes. Previously every enemy paid for LOS,
// separation, wall-slide recovery and the unstick scan in the same tick, so one big hit
// that pushed a pack into a wall made dozens of enemies run every expensive geometry
// path at once. Enemies are now visited in staggered slices (like the LOS cache) so any
// single frame only pays for a bounded slice of the crowd instead of the whole crowd.
const AI_SLICE = 6;
let _aiCursor = 0;
function aiDue(i) { return (i % AI_SLICE) === _aiCursor; }

// Separation must NOT share the AI slice. The stagger above is what makes a big crowd affordable,
// but the crowd's inward drive from enemyAI runs every single frame while the push that answers it
// ran on the same 1-in-6 stagger: attraction 6x/6, repulsion 1x/6. Under a swarm the brutes and hounds
// therefore converge on a shared overlap at the hero and never recover, which is the "they melt into
// one stacked heap around Gabriel" report. The per-entity work in the separation pass is a bucket
// lookup plus a handful of cheap comparisons -- far cheaper than the LOS and path work the AI slice
// exists to ration -- so the crowd pass now runs against every enemy every frame. Only the expensive
// AI passes stay sliced.
const SEP_SLICE = 1;
let _sepCursor = 0;
function sepDue(i) { return (i % SEP_SLICE) === _sepCursor; }

// in-place array compaction: avoids allocating a fresh array every frame (GC pressure
// is what made long sessions lag slower and slower)
function compact(arr, alive) {
  let n = 0;
  for (let i = 0; i < arr.length; i++) { const v = arr[i]; if (alive(v)) arr[n++] = v; }
  arr.length = n;
}

// pooled buckets keyed by a packed cell integer (never a "x,y" string: string keys
// allocated thousands of temporaries per frame and that GC churn is what stuttered
// big fights). Use: reset() -> add(obj,x,y) -> near(x,y,out).
function makeBuckets(cell) {
  const map = new Map(), pool = [];
  let n = 0;
  return {
    cell,
    reset() { map.clear(); n = 0; },
    add(obj, x, y) {
      // Cells are clamped to the arena: everything here lives in world pixels from the
      // origin, and RW*RH << 100000, so two different tiles can never pack to the same
      // key. The old +1024 offset made cell x=-1 collide with x=2047, so a single
      // negative-coordinate entity could be reported as "near" the hero from the far
      // side of the map and land an invisible hit.
      const k = clamp(Math.floor(x / cell), 0, 1023) * 100000 + clamp(Math.floor(y / cell), 0, 1023);
      let arr = map.get(k);
      if (!arr) {
        arr = pool[n] || (pool[n] = []);
        arr.length = 0; n++;
        map.set(k, arr);
      }
      arr.push(obj);
    },
    near(x, y, out) {
      const cx = Math.floor(x / cell), cy = Math.floor(y / cell);
      let m = 0;
      for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
        // Cells are clamped into the arena (world origin, and RW*RH << 100000), so two
        // different tiles can never pack to the same key. The old +1024 offset made
        // x=-1..0 collide with x=2047..2048: a single negative-coordinate entity could
        // therefore be reported as "near" the hero from across the map and hit them
        // invisibly, and the hero's own bucket could be missed entirely.
        const arr = map.get(clamp(cx + i, 0, 1023) * 100000 + clamp(cy + j, 0, 1023));
        if (arr) out[m++] = arr;
      }
      out.length = m;
      return out;
    },
    // Visit every pooled bucket exactly once. A for..of over a Map allocates an iterator
    // per frame, and a big fight is exactly when that hurts most.
    each(fn) { for (let p = 0; p < n; p++) fn(pool[p]); },
    // Number of live buckets since the last reset().
    count() { return n; },
  };
}
// steer around an obstruction: try rotated headings, then walk to a nearby free spot.
// The fallback search is budgeted, and an enemy that only bumps a wall for a few tenths
// of a second no longer triggers a relocation search on its own.
function steerAround(e, baseAng, spd, dt) {
  for (const off of [.5, -.5, 1.0, -1.0, 1.6, -1.6, 2.2, -2.2]) {
    if (!aiSpend(2)) return true;                       // deferred: keep moving next frame
    if (!moveEnt(e, e.r, Math.cos(baseAng + off), Math.sin(baseAng + off), spd, dt)) return true;
  }
  if (D && arenaReady() && (aiSpendOrSkip(30) || aiDesperate(e))) {
    // The waypoint is cached on the enemy across frames. It used to be reused unconditionally, so a
    // stale target survived both a relocation and an arena rebuild: farSpot teleports the brute to a
    // new spot and the next frame steered him back toward a waypoint in the old arena, which is the
    // "wedged next to the pillar forever" loop that eventually reached the desperate relocation. A
    // cached value also cannot be validated, so a throw here would poison the waypoint permanently.
    // Only a fresh result is stored, and it is validated before use.
    if (!e.way) {
      const cand = D.nearestFloor(e.x + Math.cos(baseAng) * e.r * 3, e.y + Math.sin(baseAng) * e.r * 3, e.r + 4);
      if (!cand || !isFinite(cand.x) || !isFinite(cand.y)) return true;
      e.way = cand;
    }
    const t = e.way;
    const dd = dist(e.x, e.y, t.x, t.y);
    if (dd < 20) { e.way = null; return true; }
    if (!moveEnt(e, e.r, (t.x - e.x) / dd, (t.y - e.y) / dd, spd, dt)) e.way = null;
    return true;
  }
  return false;
}

function updateEnemy(e, dt) {
  if (e.dead) return;
  e.hurtT = Math.max(0, e.hurtT - dt);
  if (e.atkT > 0) { e.atkT -= dt; if (e.atkT <= 0) e.atkT = 0; if (e.atkT <= 0 && e.pendingHit) { e.pendingHit = false; doEnemyHit(e); } }
  e.atkCdT = Math.max(0, e.atkCdT - dt);
  const d = dist(e.x, e.y, P.x, P.y);
  // archer bow aim always tracks the hero (drawn in world space)
  if (d > 1 && (e.kind === "archer" || e.kind === "brutearcher")) e.aimAng = Math.atan2(P.y - 16 - e.y, P.x - e.x);
  // smooth turning: no instant flip-flop when the hero crosses sides
  const want = P.x >= e.x ? 1 : -1;
  if (want !== e.face) {
    e.faceHold = (e.faceHold || 0) + dt;
    if (e.faceHold > .18) { e.face = want; e.faceHold = 0; }
  } else e.faceHold = 0;
  const reach = meleeReach(e);
  const spd = e.spd * (e.boss && e.phase === 2 ? 1.2 : 1) * .8;

  // brutes and grunt archers keep their distance and loose arrows (only when they can see the hero)
  if (e.kind === "archer" || e.kind === "brutearcher") {
    const los = losToHero(e);
    if (d > 260 || !los) {
      // reposition: close in, or flank around cover instead of grinding behind a pillar
      const a = Math.atan2(P.y - e.y, P.x - e.x) + Math.sin(G.t * 2 + e.wob) * .25;
      if (moveEnt(e, e.r, Math.cos(a), Math.sin(a), spd, dt)) {
        if (steerAround(e, a, spd * .9, dt)) e.way = null;
        else e.stuckT = (e.stuckT || 0) + dt;
      } else { e.stuckT = 0; e.way = null; }
    } else if (d < 170) {
      const a = Math.atan2(P.y - e.y, P.x - e.x);
      moveEnt(e, e.r, -Math.cos(a), -Math.sin(a), spd * .85, dt);
    }
    if ((e.stuckT || 0) > 1.6 && D && arenaReady() && aiDesperate(e)) {
      // fully wedged behind cover: relocate so the wave can always be cleared
      const s = D.farSpot(P.x, P.y, 260, e.r + 4);
      e.x = s.x; e.y = s.y; e.stuckT = 0; e.way = null; e._losAt = undefined;
    }
    if (!los) e.loseSightT = (e.loseSightT || 0) + dt; else e.loseSightT = 0;
    e.atkCdT -= dt;
    // never loose an arrow into a pillar: requires clear sight now and at release time
    if (e.atkCdT <= 0 && d < 420 && e.atkT <= 0 && los && e.loseSightT < .35) {
      e.atkCdT = e.kind === "brutearcher" ? rand(1.7, 2.7) : rand(1.9, 3.0); e.atkT = e.atkDur;
      e.aimAng = Math.atan2(P.y - 16 - e.y, P.x - e.x);
      const a = e.aimAng, sp2 = e.kind === "brutearcher" ? 330 : 290, dmg = e.dmg;
      const sx = e.x, sy = e.y - 20;
      scheduleEnemyShot(e, AI_GEN, () => {
        // re-check at release time with one cheap sample near the archer's bow
        if (D && D.circleBlocked(sx, sy, 6)) return;
        if (G.projectiles.length >= MAX_PROJECTILES) return;
        G.projectiles.push({ x: sx, y: sy, vx: Math.cos(a) * sp2, vy: Math.sin(a) * sp2, t: 2.4, dmg, ang: a, fromEnemy: true });
      });
    }
    return;
  }

  // bats swoop, hounds/trolls charge
  if (e.dashT > 0) {
    e.dashT -= dt;
    moveEnt(e, e.r, e.dashDx, e.dashDy, spd * 2.4, dt);
    if (e.stuck) { e.dashT = 0; e.aiT = Math.min(e.aiT, .5); }
    if (d < reach && e.atkCdT <= 0) { e.atkT = e.atkDur; e.pendingHit = true; e.atkCdT = .9; e.dashT = 0; }
    return;
  }
  e.aiT -= dt;
  if (e.aiT <= 0) {
    e.aiT = rand(1.4, 3.2);
    if (e.kind === "fenris" || (e.boss && e.kind === "troll")) {
      const a = Math.atan2(P.y - e.y, P.x - e.x);
      e.dashT = .45; e.dashDx = Math.cos(a); e.dashDy = Math.sin(a);
    }
  }
  // ranged spit for spiders & hollowcrown (web / cold bolts only when the target is visible)
  if ((e.kind === "spider" || (e.boss && (e.kind === "spider" || e.kind === "hollowcrown")))) {
    e.spitT -= dt;
    if (e.spitT <= 0 && d < 340 && d > 90 && losToHero(e) && G.projectiles.length < MAX_PROJECTILES) {
      e.spitT = e.boss ? rand(1.4, 2.4) : rand(2.5, 4.5);
      const a = Math.atan2(P.y - 16 - e.y, P.x - e.x);
      const spd2 = 210;
      // fromEnemy:true is essential — without it the bolt is treated as a hero arrow
      // and the collision loop dereferences pr.hitSet, which only hero arrows carry (freeze)
      G.projectiles.push({ x: e.x, y: e.y - (e.boss ? 30 : 18), vx: Math.cos(a) * spd2, vy: Math.sin(a) * spd2, t: 3, dmg: e.dmg * .8, ang: a, fromEnemy: true, kind: e.boss && e.kind === "hollowcrown" ? "cold" : "web" });
    }
  }
  // hollowcrown summons on phase 2
  if (e.boss && e.kind === "hollowcrown" && e.phase === 2) {
    e.sumT = (e.sumT || 3) - dt;
    if (e.sumT <= 0) {
      e.sumT = 6;
      let live = 0;   // count in place: filter() here allocated an array on every summon tick
      for (const x of G.enemies) if (!x.dead && !x.boss) live++;
      if (live < 5) { spawnEnemy("bat"); spawnEnemy("bat"); }
    }
  }

  if (d > reach - 6) {
    // approach with slight strafe, wall-aware
    const a = Math.atan2(P.y - e.y, P.x - e.x) + Math.sin(G.t * 2 + e.wob) * .3;
    const blocked0 = moveEnt(e, e.r, Math.cos(a), Math.sin(a), spd, dt);
    if (!blocked0) { e.stuckT = 0; e.way = null; }
    else {
      // slide/steer around walls and pillars instead of grinding into them
      const recovered = steerAround(e, a, spd * .9, dt);
      e.stuckT = (e.stuckT || 0) + (recovered ? dt * .3 : dt);
      if (e.stuckT > 1.6 && D && arenaReady() && aiDesperate(e)) {
        // fully wedged: relocate to a reachable spot so the wave can still be cleared
        const s = D.farSpot(P.x, P.y, 300, e.r + 4);
        e.x = s.x; e.y = s.y; e.stuckT = 0; e.dashT = 0; e.way = null; e._losAt = undefined;
      }
    }
  } else if (e.atkCdT <= 0 && e.atkT <= 0) {
    e.atkT = e.atkDur; e.pendingHit = true; e.atkCdT = e.boss ? .95 : rand(1.1, 1.7);
  }
  // boss phase change
  if (e.boss && e.phase === 1 && e.hp < e.maxHp * .5) {
    e.phase = 2; AudioSys.bossRoar(); G.shake = Math.max(G.shake, 12);
    popText(e.x, e.y - 90, "ENRAGED!", "#ff5a4a", true);
  }
}
// The only melee contact path: reached via the pendingHit swing resolution above, and the sole
// caller of hurtHero with e.dmg. Ranged damage goes through G.projectiles instead - archer arrows
// (ui.js ~248) and spider/hollowcrown web and cold bolts (~285) - which is why archer arrows and spider
// spit keep their previous numbers. Both bosses have a ranged attack too, so the scale is keyed on
// the hit actually landing as a contact swing rather than on kind, and hollowcrown cold-bolt damage is
// left unchanged. Boss contact swings are doubled, since the boss melee attack is a melee attack.
function doEnemyHit(e) {
  const d = dist(e.x, e.y, P.x, P.y);
  // Same art-derived contact distance the AI uses to decide to bite, plus the small step-in
  // allowance it already had. Using the reach from the art here too is what keeps the two honest:
  // if the bite does not connect at the distance the AI attacked from, the swing is simply wasted
  // rather than landing from out of frame.
  const reach = meleeReach(e) + 8;
  if (d < reach) hurtHero(e.dmg * MELEE_DMG_SCALE, e.x, e.y);
}

// keep packs from piling into each other (fixes jittering enemy clusters)
const SEP = makeBuckets(64);
const SEP_NEAR = [];
function enemySeparation() {
  const list = G.enemies;
  SEP.reset();
  // Hero body radius for separation, in the same units as e.r. Gabriel's drawn body is about 11 px
  // wide, plus the hero art's 2 px stroke half-width, so ~13 px is his real footprint - the same
  // 12-16 px the rest of the codebase treats him as (hurtHero knocks him back with moveEnt(P, 12, …)
  // and spawns clear him with a 16 px radius). The pack used to push only against each other, which
  // meant 15 hounds were each permitted to stand at e.r + P.r ≈ 30 px from the hero and therefore
  // inside his sprite: from the player's chair that is not a crowd around the elf, it is one
  // merging blob with a bow in the middle.
  const heroR = 13;
  // Ring radius for each kind, measured centre-to-centre from the elf. These used to be a single
  // flat `sepR` for every non-hound, which for a grunt (drawn about 24 px wide) permitted three or
  // four bodies to occupy the same 14 px disc on his chest: individually "not overlapping the hero"
  // while collectively covering him. Spiders are drawn ~38 px wide with legs at +-19 px against a
  // 15 px collision radius, so they need more than twice their collision radius or a pack of them
  // still reads as one mat of legs. Hounds are long (about 71 px nose-to-tail before zoom), so their
  // ring has to be at least the length of the animal: 34 gives a 79 px ring, which seats five or six
  // wolves around the elf -- close enough to bite, far enough that the pack occupies more than one
  // body. Everything is still well inside melee reach (52 px for non-bosses, hound reach ~90), so no
  // ring below costs the crowd a single swing.
  const ringR = (e) => e.kind === "fenris" ? 34 : e.kind === "spider" ? 22 : e.kind === "bat" ? 15 : Math.max(e.r + 6, 19);
  // Whole-crowd pass: see SEP_SLICE. The expensive AI work stays staggered; this does not.
  _sepCursor = (_sepCursor + 1) % SEP_SLICE;
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (!sepDue(i)) { a._si = -1; continue; }
    a._si = i;
    SEP.add(a, a.x, a.y);
  }
  // Nothing bucketed (tiny crowd, or everything outside this slice) — nothing to do.
  if (SEP.count() === 0) return;
  // Pairwise crowd separation, then the hero. A bucket sweep is O(1) per bucket, and it gives each
  // pair exactly one resolution and a symmetric push. Doing this per enemy instead - push away from
  // anyone within (e.r + heroR) - converges one sixth of the way per frame because of the stagger,
  // so the AI's inward drive simply outran it and the pack still ended up inside the elf.
  SEP.each(function sweep(bucket) {
    for (let k = 0; k < bucket.length; k++) {
      const a = bucket[k];
      const arrs = SEP.near(a.x, a.y, SEP_NEAR);
      for (let ai = 0; ai < arrs.length; ai++) {
        const arr = arrs[ai];
        for (let m = 0; m < arr.length; m++) {
          const b = arr[m];
          if (b._si <= a._si) continue;          // handle each pair exactly once
          const min = (a.sepR || a.r) + (b.sepR || b.r);
          let dx = b.x - a.x, dy = b.y - a.y;
          const d2 = dx * dx + dy * dy;
          if (d2 >= min * min || d2 < .0001) continue;
          const d = Math.sqrt(d2);
          dx /= d; dy /= d;
          // Separate by the full penetration, not a fraction of it. A proportional push
          // (`(min - d) * .7`) settles wherever the AI's inward drive happens to balance it, and
          // because that drive runs every frame the equilibrium was a permanent overlap -- several
          // bodies sharing one footprint, which is the stacked-heap look. Resolving to exactly
          // `min` each frame makes an overlapping pair impossible to sustain: they can press in
          // again on the next AI step and are separated again on the next separation step, so the
          // crowd reads as a ring of distinct bodies rather than one mass. The wall check below
          // still governs how close to geometry anyone can actually get, and nothing teleports:
          // the largest single correction is one body-radius of overlap.
          const push = min - d;
          const aw = b.boss ? 0 : 1, bw = a.boss ? 0 : 1;
          // A boss does not yield to the pack, so all of the correction falls on the smaller
          // entity; if both move equally, halve it so the pair still lands at `min` apart.
          const half = (aw + bw) === 2 ? .5 : 1;
          let nx = a.x - dx * push * aw * half, ny = a.y - dy * push * aw * half;
          if (!D || !D.circleBlocked(nx, ny, a.r)) { a.x = nx; a.y = ny; }
          nx = b.x + dx * push * bw * half; ny = b.y + dy * push * bw * half;
          if (!D || !D.circleBlocked(nx, ny, b.r)) { b.x = nx; b.y = ny; }
        }
      }
    }
  });
  // then hold the crowd off the hero's own body. Same whole-crowd pass as the pairwise sweep
  // above, so the elf is never left overlapped for five frames in six; the cost is one distance
  // test per enemy, the same order as the AI update it shares a frame with. Bosses are excluded:
  // melee bosses are supposed to reach the elf, and their own bite reach governs that, not this.
  for (let i = 0; i < list.length; i++) {
    const e = list[i];
    if (e._si < 0 || e.boss) continue;
    // Pack animals keep a wolf's length from the elf; everything else stands just outside his own
    // silhouette rather than inside it. Excluded enemies still land their hits: doEnemyHit tests
    // its own art-derived reach (52 px for non-bosses, ~90 for a hound), all well beyond these
    // rings, so holding them out here costs no bites.
    const min = ringR(e) + heroR;
    // P.y - 8 is roughly the centre of his drawn torso. Vertical overlap is discounted by that
    // offset so the push direction points away from the chest rather than from his feet, while
    // contact distance stays the honest centre-to-centre measure (his collision radius is 11).
    const oy = P.y - 8;
    let dx = e.x - P.x, dy = e.y - oy;
    const d2 = dx * dx + dy * dy;
    if (d2 >= min * min) continue;
    const d = Math.sqrt(d2);
    if (d < .0001) { dx = e.face || 1; dy = .3; }   // dead on top of him: shove toward his back
    else { dx /= d; dy /= d; }
    // Same reasoning as the crowd pass: resolve the full penetration so no body can persist
    // inside the elf's footprint between AI steps. A fractional push here balanced against the
    // AI's constant attraction at a few pixels of overlap, which is exactly "the swarm melts into
    // one heap and I can't see him".
    const push = min - d;
    const nx = e.x + dx * push, ny = e.y + dy * push;
    // A cornered enemy is allowed to stay pressed against the wall rather than be shoved through
    // it; being briefly overlapped by the elf is the lesser evil than tunnelling into geometry.
    if (!D || !D.circleBlocked(nx, ny, e.r)) { e.x = nx; e.y = ny; }
  }
}

// ---------- main update ----------
const PROJGRID = makeBuckets(72);
const PROJ_NEAR = [];

// An arrow in flight covers 20 px per frame at 60 fps, and a wolf's skull is ~14 px across once
// zoomed, so a point test at the arrow's new position alone skipped straight over the head: the
// arrow sampled to the left of the skull one frame and to the right of it the next, and the only
// thing it ever overlapped was a paw. Hero arrows now sweep the segment they actually travelled
// this frame and are tested against the shapes the renderer draws.
//
// Shapes are supplied by chars.js - the same constants that author the art - because guessing
// them from e.r is exactly what made the body and head non-interactive while the paws still took
// hits. Returns an array of capsules ({x1,y1,x2,y2,r}) in world space; null falls back to the
// circular body test for kinds without an authored shape set.
const HIT_SHAPES = [];
function enemyHitShapes(e) {
  HIT_SHAPES.length = 0;
  if (e.kind === "fenris") {
    for (const s of wolfHitShapes()) pushHitShape(e, s);
    // Skarnax is the same art at e.scale plus the boss-only strokes in drawFenris, so he needs the
    // pack shapes *and* the extras: his crest, collar, dewlaps and heavier legs stick outside the
    // pack silhouette and would otherwise be the visible parts that still swallow arrows.
    if (e.boss) for (const s of bossWolfHitShapes()) pushHitShape(e, s);
  } else if (e.boss && e.kind === "troll") {
    for (const s of bossHitShapes(e)) pushHitShape(e, s);
  } else {
    return null;
  }
  return HIT_SHAPES;
}
function pushHitShape(e, s) {
  const z = WOLF_ZOOM(e), f = e.face || 1;
  if (s.poly) {
    // polygon: emit it as a fan of capsules from its centroid, which keeps one intersection test
    // for every shape kind and is generous by roughly the stroke half-width already in the art
    let cx = 0, cy = 0;
    for (const p of s.poly) { cx += p[0]; cy += p[1]; }
    cx /= s.poly.length; cy /= s.poly.length;
    for (const p of s.poly) HIT_SHAPES.push({ x1: e.x + cx * z * f, y1: e.y + cy * z, x2: e.x + p[0] * z * f, y2: e.y + p[1] * z, r: 3.5 * z });
  } else {
    HIT_SHAPES.push({ x1: e.x + s.line[0] * z * f, y1: e.y + s.line[1] * z, x2: e.x + s.line[2] * z * f, y2: e.y + s.line[3] * z, r: s.r * z });
  }
}

// Segment (a -> b) against capsule (p1 -> p2, radius r) in world space.
function segHitsCapsule(ax, ay, bx, by, p1x, p1y, p2x, p2y, r) {
  const d = segSegDist2(ax, ay, bx, by, p1x, p1y, p2x, p2y);
  return d <= r * r;
}
function segSegDist2(ax, ay, bx, by, cx, cy, dx, dy) {
  const r1x = bx - ax, r1y = by - ay, r2x = dx - cx, r2y = dy - cy;
  const s1x = cx - ax, s1y = cy - ay;
  const a = r1x * r1x + r1y * r1y, e = r2x * r2x + r2y * r2y, f = r2x * s1y - r2y * s1x;
  if (a <= 1e-9 && e <= 1e-9) return s1x * s1x + s1y * s1y;   // both degenerate: point/point
  let s, t;
  if (a <= 1e-9) { s = 0; t = clamp(f / e, 0, 1); }
  else {
    const c = r1x * s1y - r1y * s1x;
    if (e <= 1e-9) { t = 0; s = clamp(-c / a, 0, 1); }
    else {
      const denom = a * e - (r1x * r2x + r1y * r2y) * (r1x * r2x + r1y * r2y);
      s = denom > 1e-9 ? clamp(f * (r1x * r2x + r1y * r2y) - (r1x * s1y - r1y * s1x) * e, 0, 1) / denom : 0;
      t = ((r1x * r2x + r1y * r2y) * s + f) / e;
    }
  }
  const ox = s1x - r1x * s + r2x * t, oy = s1y - r1y * s + r2y * t;
  return ox * ox + oy * oy;
}

// True when this frame's arrow travel landed on the enemy as he is drawn.
function arrowHitsEnemy(e, x0, y0, x1, y1) {
  const shapes = enemyHitShapes(e);
  if (!shapes) {
    // no authored silhouette: keep the old circular body test (it is what non-wolf kinds always
    // used and they were never reported as swallowing arrows)
    const cy0 = y0 - e.r * .7, cy1 = y1 - e.r * .7, r = e.r + 8;
    return segSegDist2(x0, y0, x1, y1, e.x, cy0, e.x, cy1) <= r * r;
  }
  for (let i = 0; i < shapes.length; i++) {
    const s = shapes[i];
    if (segHitsCapsule(x0, y0, x1, y1, s.x1, s.y1, s.x2, s.y2, s.r)) return true;
  }
  return false;
}

function update(dt) {
  if (document.hidden) return;
  G.t += dt; G.elapsed += dt;
  if (G.freezeT > 0) { G.freezeT -= dt; return; }
  if (G.shake > 0) G.shake = Math.max(0, G.shake - dt * 30);
  // decaying shake offsets: no per-frame random pops, no residual offset while paused
  if (G.shake > 0) { const m = G.shake * .28; G.shX = rand(-m, m); G.shY = rand(-m, m); }
  else { G.shX = 0; G.shY = 0; }

  // hero timers
  P.invuln = Math.max(0, P.invuln - dt); P.hitFx = Math.max(0, P.hitFx - dt);
  // web slow decays independently of invulnerability, and is clamped rather than allowed to go
  // negative so a paused or frozen frame cannot leave it wedged below zero (which would mean
  // "slow forever" once compared with > 0).
  P.webT = Math.max(0, (P.webT || 0) - dt);
  P.atkCdT = Math.max(0, P.atkCdT - dt);
  if (P.atkT > 0) { P.atkT -= dt; if (P.atkT <= 0) P.atkT = 0; }
  else P.slashChecked = false;

  // movement
  {
    const v = moveVec();
    P.moving = v.l > .1;
    // Spider web: a cobalt bolt that lands on you sticks for WEB_SLOW_T and cuts your pace.
    // P.spd itself is never touched, so a "Swift Feet" boon or a level-up speed change cannot be
    // silently overwritten by the unwinding of a slow.
    const sp = P.spd * (P.webT > 0 ? WEB_SLOW : 1);
    moveEnt(P, 12, v.dx, v.dy, sp, dt);
  }
  // shoot only with Space (or J): fires in the direction you are moving / last aimed
  if (slashHeld && P.atkT <= 0 && P.atkCdT <= 0) startShot();

  // immediate facing update: face the direction actually being moved
  {
    const fv = moveVec();
    if (fv.l > .05) {
      P.face = fv.dx >= 0 ? 1 : -1;
      P.shootAng = Math.atan2(fv.dy, fv.dx);
    } else if (P.atkT <= 0) {
      // Resting pose only. `face` is just +/-1, so at rest the bow has to point along the way he
      // is looking - otherwise he strolls left with the bow held out to the right.
      //
      // But this must NEVER override an aim that is already compatible with the facing. Walking
      // up or down leaves `face` untouched, so a last-run angle of straight up/down is still the
      // correct aim; snapping it to the horizontal facing made arrows fly sideways, breaking the
      // documented "fires the way you last ran" behaviour. Horizontal-only is the exact set that
      // can contradict the facing, so only those get re-pointed: sin ~= 0 means horizontal, and
      // cos < 0 with face > 0 (or cos > 0 with face < 0) means it points the wrong way.
      const sa = Number.isFinite(P.shootAng) ? P.shootAng : null;
      const horizontal = sa === null || Math.abs(Math.sin(sa)) < 1e-6;
      const wrongSide = sa !== null && Math.sign(Math.cos(sa)) === -Math.sign(P.face);
      if (horizontal && wrongSide) P.shootAng = P.face >= 0 ? 0 : Math.PI;
      else if (sa === null) P.shootAng = P.face >= 0 ? 0 : Math.PI;
    }
  }

  // camera follow (clamped)
  const targetX = clamp(P.x - W / 2, 0, DW - W);
  const targetY = clamp(P.y - H / 2 - 40, 0, DH - H);
  G.camX = lerp(G.camX, targetX, 1 - Math.pow(.001, dt));
  G.camY = lerp(G.camY, targetY, 1 - Math.pow(.001, dt));

  // enemies
  supplyTick(dt);
  _aiWork = 0; _aiSkips = 0; _aiRelocates = AI_RELOCATE_BUDGET;   // per-frame geometry budget
  _aiClock = performance.now(); _aiDeadline = _aiClock + AI_MS_BUDGET;
  _aiSkipAcc = Math.min(400, _aiSkipAcc + _aiSkips * 20);   // pay last frame's deferred work back first
  _aiCursor = (_aiCursor + 1) % AI_SLICE;                    // stagger the expensive AI passes
  // Separation first: it is O(n) via the bucket grid, and it stops the crowd from
  // stacking into one cell before the AI runs (stacked enemies all block each other's
  // move, which is what sent whole packs into the wall-slide/relocate path at once).
  if (D) enemySeparation();
  const _tAI = performance.now();
  for (const e of G.enemies) updateEnemy(e, dt);
  profAdd("ai", performance.now() - _tAI);
  if (D) {
    // Track the real cost of this frame's AI so the next frame can budget against it.
    _aiElapsed = performance.now() - _aiClock;
    // Adaptive: if this frame still blew the ceiling even with the deadline (a genuinely
    // enormous crowd, or a slow machine), tighten the next frames instead of hanging. The
    // debt mechanism guarantees deferred work still gets done, so this sheds load without
    // wedging enemies forever. It relaxes back at ~1ms per second of normal frames.
    AI_MS_BUDGET = _aiElapsed > AI_MS_BUDGET * 2
      ? Math.max(2.5, AI_MS_BUDGET * .75)
      : Math.min(7, AI_MS_BUDGET + dt);
  }
  compact(G.enemies, e => !e.dead);
  // Unstick pass: only entities actually touching geometry pay for a search. The old
  // unconditional pass ran a ring search for every enemy every frame — with a big pack
  // pressed against a wall that one loop was a multi-second lock-up.
  if (D) {
    for (let i = 0; i < G.enemies.length; i++) {
      const e = G.enemies[i];
      // Uninitialized entities (NaN/undefined position) used to land at the origin and
      // still swing, which is how the hero got hit by things nowhere on screen.
      if (!Number.isFinite(e.x) || !Number.isFinite(e.y)) { const s = D.spawn; e.x = s.x; e.y = s.y; continue; }
      // Bosses are excluded from the stagger here for the opposite reason from separation: he is
      // the largest collider in the arena and his separation push is never sliced, so a missed
      // unstick scan lets the pack nudge him into a pillar and leave him embedded in it.
      if (!e.boss && !aiDue(i)) continue;       // spread across frames: 1/AI_SLICE of the crowd
      if (arenaReady() && !D.circleBlocked(e.x, e.y, e.r)) continue;
      if (!aiSpendOrSkip(20)) continue;
      const f = D.nearestFloor(e.x, e.y, e.r);
      e.x = f.x; e.y = f.y;
    }
    if (D.circleBlocked(P.x, P.y, 12)) { const f = D.nearestFloor(P.x, P.y, 12); P.x = f.x; P.y = f.y; }
  }

  // wave flow: two brute waves, then the arena boss
  if (G.wave === "fight" && G.enemies.length === 0) {
    G.waveTimer += dt;
    if (G.waveTimer > 2.2) {
      G.waveTimer = 0;
      if ((G.waveNum || 0) < 2) spawnWave();
      else { G.wave = "preboss"; G.waveTimer = 2.2; announce("A presence stirs…", ZONES[realmIndex(G.zone)].boss.name + " approaches"); }
    }
  } else if (G.wave === "fight") { G.waveTimer = 0; } else if (G.wave === "preboss") {
    G.waveTimer -= dt; if (G.waveTimer <= 0) spawnBoss();
  } else if (G.wave === "boss") {
    // Timed reinforcements. Counted down here rather than on G.t or performance.now() so the
    // clock freezes with the game: a pause, a level-up panel or a lost-focus pause must not burn
    // the interval, and the whole branch is skipped unless a fight is actually live.
    // Guarded on the boss being alive, so a boss killed on the same frame as a timer expiry
    // cannot spawn a wave into the "cleared" celebration.
    if (G.boss) {
      // The fallback on a missing clock also honours the loop rule, so a boss that somehow starts
      // without an explicit timer still gets realm-6 pacing in loop 2+ instead of the slow one.
      const reinLoop = loopNumber(G.zone);
      G.bossReinT = (G.bossReinT === undefined ? bossReinIntervalS(G.boss.kind, reinLoop) : G.bossReinT) - dt;
      if (G.bossReinT <= 0) {
        const final = G.boss.kind === FINAL_BOSS_KIND;
        G.bossReinT = bossReinIntervalS(G.boss.kind, reinLoop);
        spawnReinforceWave(final);
      }
    }
  } else if (G.wave === "cleared") {
    G.waveTimer += dt;
    if (G.waveTimer > 3.4) {
      // Finishing realm 6 completes a circuit: the quest panel opens as it always did, but its
      // button now continues into the next, harder loop instead of restarting the game. The run
      // only ever truly ends when Gabriel falls, so the endless game is "how far can you go".
      if (realmIndex(G.zone) >= REALM_COUNT - 1) { questVictory(); }
      else { continueJourney(); }
    }
  }

  // projectiles: hero arrows hit enemies, enemy arrows/web/cold bolts hit the hero
  // broad-phase grid rebuilt once per frame so arrows no longer test every enemy in
  // the arena (the old O(arrows x enemies) scan is what stuttered in big melees)
  PROJGRID.reset();
  for (const e of G.enemies) if (!e.dead) PROJGRID.add(e, e.x, e.y);
  for (const pr of G.projectiles) {
    pr.x += pr.vx * dt; pr.y += pr.vy * dt; pr.t -= dt;
    // hard guard: a non-finite projectile would never expire and hang the frame.
    // isFinite() on the position only — velocities legitimately go non-finite for one
    // frame after an enemy is yanked by separation while a shot is in flight, and
    // rejecting on those threw away perfectly good arrows.
    if (!(pr.t > 0) || !Number.isFinite(pr.x) || !Number.isFinite(pr.y)) { pr.t = 0; continue; }
    if (D && D.circleBlocked(pr.x, pr.y, 4)) { burst(pr.x, pr.y, 3, pr.kind === "web" ? "#dcdcf2" : "#caa15a", 60, .3, 2.5); pr.t = 0; continue; }
    if (pr.fromEnemy) {
      // a non-finite enemy bolt would sail forever and land an invisible hit
      if (!Number.isFinite(pr.vx) || !Number.isFinite(pr.vy)) { pr.t = 0; continue; }
      if (dist(pr.x, pr.y, P.x, P.y - 16) < 20) {
        hurtHero(pr.dmg, pr.x - pr.vx, pr.y - pr.vy);
        // Spider web: a cobalt bolt that lands on you sticks for WEB_SLOW_T and cuts your pace,
        // with the "Webbed!" label as the feedback that tells you it connected. Web previously
        // only burst into particles and never slowed anything - the hit was damage-only.
        // P.spd itself is untouched, so a speed boon can never be clobbered by unwinding a slow.
        if (pr.kind === "web") {
          P.webT = WEB_SLOW_T;
          popText(P.x, P.y - 48, "Webbed!", "#b9c6ff", false);
        }
        pr.t = 0;
      }
    } else {
      // Sweep from where the arrow was to where it is now, so an arrow that visibly crossed
      // Skarnax's head or chest registers instead of tunnelling between samples and only counting
      // on a paw.
      const x0 = pr.x - pr.vx * dt, y0 = pr.y - pr.vy * dt;
      const arrs = PROJGRID.near(pr.x, pr.y, PROJ_NEAR);
      for (let ai = 0; ai < arrs.length; ai++) {
        const bucket = arrs[ai];
        for (let bi = 0; bi < bucket.length; bi++) {
          const e = bucket[bi];
          if (e.dead || pr.hitSet.has(e)) continue;
          if (arrowHitsEnemy(e, x0, y0, pr.x, pr.y)) {
            pr.hitSet.add(e);
            hurtEnemy(e, pr.dmg, Math.atan2(pr.vy, pr.vx));
            burst(pr.x, pr.y, 4, "#e8d9b0", 90, .3, 2.5);
            pr.t = 0;
            break;
          }
        }
        if (pr.t <= 0) break;
      }
    }
  }
  compact(G.projectiles, p => p.t > 0 && p.x > -20 && p.x < DW + 20 && p.y > -20 && p.y < DH + 20);
  if (G.projectiles.length > MAX_PROJECTILES) G.projectiles.splice(0, G.projectiles.length - MAX_PROJECTILES);

  // ring blast wave (removed)

  // pickups (AABB reject first: only nearby supplies pay for hypot + magnet maths)
  const px0 = P.x, py0 = P.y - 10;
  // any pickup whose timer went non-finite is dropped instead of lingering forever
  for (let i = 0; i < G.pickups.length; i++) {
    const pk = G.pickups[i];
    if (!(pk.t > 0) && pk.t !== Infinity) { pk.t = 0; G.pickDead = true; continue; }
    if (!Number.isFinite(pk.x) || !Number.isFinite(pk.y)) { pk.t = 0; G.pickDead = true; continue; }
    if (pk.t !== Infinity) { pk.t -= dt; if (pk.t <= 0) { G.pickDead = true; continue; } }
    const ddx = pk.x - px0, ddy = pk.y - py0;
    if (ddx > 96 || ddx < -96 || ddy > 96 || ddy < -96) continue;
    const dr = Math.hypot(ddx, ddy);
    if (dr < 90) {
      const a = Math.atan2(-ddy, -ddx);
      pk.x += Math.cos(a) * 380 * dt; pk.y += Math.sin(a) * 380 * dt;
    }
    if (dr < 26) {
      pk.t = 0;
      if (pk.kind === "gold") { G.gold += pk.val; AudioSys.coin(); popText(P.x, P.y - 66, "+" + pk.val + " 🪙", "#ffd76b"); }
      // pk.val is already blessing-scaled — every arrow source resolves its value through
      // arrowDrop() at drop time — so the pickup adds it as-is. Multiplying here too would apply
      // the blessing twice, which is exactly the bug class this comment exists to prevent.
      else if (pk.kind === "arrows") { P.arrows += pk.val; AudioSys.loot(); popText(P.x, P.y - 66, "+" + pk.val + " arrows", "#dfe9ff"); }
      else { P.hp = Math.min(P.maxHp, P.hp + pk.val); AudioSys.potion(); popText(P.x, P.y - 66, "+" + pk.val + " HP", "#7dff8a"); }
      updateHud();
    }
  }
  if (G.pickDead) { compact(G.pickups, p => p.t > 0); G.pickDead = false; }

  // particles / texts
  for (const pt of G.particles) {
    pt.t -= dt; pt.x += pt.vx * dt; pt.y += pt.vy * dt;
    if (!pt.float) pt.vy += 22 * dt;
  }
  // particles: compact every frame (cheap at this size) so dead motes never linger
  // between rare cull passes — that lingering pile is what made play lag over time
  compact(G.particles, p => p.t > 0 && Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.vx) && Number.isFinite(p.vy));
  if (G.particles.length > 480) G.particles.splice(0, G.particles.length - 420);   // hard ceiling for huge melees
  if (G.shockwaves && G.shockwaves.length) compact(G.shockwaves, s => (s.t -= dt) > 0);
  // escort arrivals flare in as they appear
  if (G.escorts) {
    for (const es of G.escorts) {
      es.t -= dt;
      if (es.t <= 0 && !es.flared) {
        es.flared = true;
        const e = es.e;
        if (e && !e.dead) {
          burst(e.x, e.y - e.r * .6, 10, "rgba(255,150,50,.85)", 130, .5, 3.5);
          G.shockwaves.push({ x: e.x, y: e.y - e.r * .4, t: .4, life: .4, r0: 4, r1: e.r * 2.2, color: "255,140,50" });
        }
      }
    }
    compact(G.escorts, es => es.t > -.2);
  }
  for (const tx of G.texts) { tx.t -= dt; tx.y -= 46 * dt; }
  compact(G.texts, t => t.t > 0);
  if (G.announce) { G.announce.t -= dt; if (G.announce.t <= 0) G.announce = null; }

  // vignette low-hp
  $("vignette").classList.toggle("low", P.hp / P.maxHp < .3 && G.state === "play");
}

// ---------- render ----------
// reusable render scratch + cached glow sprites (avoid per-frame allocation)
const DRAWBuf = [];
function sortByDepth(a, b) { return a._k - b._k; }

// Camera-side bias for depth sorting, in half-pixels of the * 2 key above.
//
// A sprite that only paints upward from its origin (feet on the floor) can only ever cover an
// entity whose feet are at or below its own line. So for the wolf kind - the one entity in this
// game drawn far larger than its collision radius - the honest occluder is the part of the art
// that extends *below* the feet line: the paws and the shadow, roughly the lowest 8 px of art.
// Everything above the feet must not be allowed to hide a smaller entity standing north of it.
// Without the bias, a boss 190 px tall whose feet are at y=300 painted over the hero at y=299 and
// swallowed him; with it, he only covers entities actually standing at/behind his paws, and the
// elf stays visible in front of the boss's chest as he circles around.
function depthBias(e) {
  if (!e.boss) return 0;
  // How far the drawn art reaches below the origin, in art units: the paws sit at y=-4 with a 5.5
  // radius, so the lowest painted pixel is about 1.5 units under the floor line (shadow handled
  // separately and already capped). Scaled, that is the legitimate camera-side reach.
  const below = 2 * (e.scale || 1);
  return below < 8 ? below : 8;
}
function hexToRgb(h) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h || "#ffaa50");
  return m ? parseInt(m[1], 16) + "," + parseInt(m[2], 16) + "," + parseInt(m[3], 16) : "255,170,80";
}
// CONTRACT: the sprite's bright centre is its canvas centre, and callers centre it with
// drawImage(sprite, x - R, y - R) where R is the `radius` argument passed here. That is why the
// canvas is 2R wide and the gradient is built at exactly (R, R).
//
// It used to be 2R+4 wide with the gradient at (R+2, R+2). Those two agree with each other, so
// the gradient was still dead-centre in its own canvas, but every caller subtracted only R+2,
// leaving the centre 4px right and 4px below the point it was supposed to light - the uniform
// "lights lined up by their top-left corner" offset on Gabriel and every fire pit alike. The
// magnitude was constant because it came from the sprite padding, not from any per-light position,
// which is why nothing was fixed by nudging the hero/brazier anchor values earlier.
// The +4 pad was never load-bearing: the gradient reaches alpha 0 at radius R and the arc is
// clipped to R, so pixels beyond R were already transparent.
function makeGlowSprite(rgb, radius, a0, extra, a1) {
  const r = radius || 34, d = r * 2;
  const c = document.createElement("canvas"); c.width = c.height = d;
  const x = c.getContext("2d"), k = r;
  const rg = x.createRadialGradient(k, k, r * .06, k, k, r);
  rg.addColorStop(0, "rgba(" + rgb + "," + (a0 || .28) + ")");
  if (extra) for (const [p, col] of extra) rg.addColorStop(p, col);
  // Optional mid stop: tightening the middle collapses the visible core onto the lit point
  // instead of reading as a broad bright blob sitting beside it.
  if (a1 !== undefined) rg.addColorStop(.35, "rgba(" + rgb + "," + a1 + ")");
  rg.addColorStop(1, "rgba(0,0,0,0)");
  x.fillStyle = rg; x.beginPath(); x.arc(k, k, r, 0, TAU); x.fill();
  return c;
}
// A glow sprite paired with the radius it was built at, so a blit offset can never drift away
// from the sprite's true centre. The old bug was exactly that: the sprite canvas was 2R+4 wide
// with its centre at R+2, while every caller subtracted R+2, putting every light 4px down-right
// of the thing it belonged to. `radius` is rounded by the same rule as the builder, so
// `x - radius` is always the centre. This is the only sanctioned way to make a glow.
function glowSpritePair(rgb, radius, a0, extra, a1) {
  const r = Math.max(1, Math.round(radius || 34));
  return { s: makeGlowSprite(rgb, r, a0, extra, a1), radius: r };
}

const GLOW_heart = glowSpritePair("225,60,72", 34);
const GLOW_arrows = glowSpritePair("240,190,90", 34);
const GLOW_fire = glowSpritePair("255,140,45", 150, .3, [[.42, "rgba(255,110,30,.13)"]], .09);
// Blurred-circle glow cached as a sprite. g.shadowBlur looks identical but re-rasterizes
// the blur for EVERY shape, and shadow blur is by far the most expensive 2D call in
// Chromium: one frost bolt was fine, a screen full of them was a multi-frame stall.
const GLOW_dot = {};
function glowSprite(g, x, y, radius, rgb, a) {
  const key = rgb + "|" + radius;
  let s = GLOW_dot[key];
  if (!s) s = GLOW_dot[key] = makeGlowSprite(rgb, radius, 1);
  g.save();
  g.globalCompositeOperation = "lighter";
  g.globalAlpha = a;
  g.drawImage(s, x - radius, y - radius);
  g.restore();
}
const GLOW_hero = {};           // warm-hue cache: one glowSpritePair per zone accent
// Warm wash for the light sources, independent of the zone palette. Several zone accents are cool
// or near-neutral (#6e6478, #8a6f4a, #8c7ac0, #9a8452), and a halo built from those lifts the
// middle of the screen with flat grey-violet rather than firelight - the faded, hazy reading.
// This tint is what the hero and the braziers actually cast.
const WARM_TORCH = "255,168,74";

// The white hit-flash scratch layer that used to live here is gone. The flash is no longer drawn
// (see drawEnemyEnt), so the viewport-sized layer, its once-per-frame clear and its per-frame budget
// all had no consumer. Leaving them in meant allocating and clearing a full-viewport canvas every
// frame purely to service a code path nothing reached.

// Light mask for the torchlit look. Lazily allocated once per viewport size, then reused every
// frame - allocating a canvas per frame is exactly the kind of per-frame garbage that made long
// sessions degrade.
const _LIGHT_MASKS = new Map();
function _lightMask(w, h) {
  const key = w + "x" + h;
  let m = _LIGHT_MASKS.get(key);
  if (m === undefined) {
    m = null;
    try {
      if (w > 0 && h > 0 && w * h <= 1920 * 1200) {
        const c = document.createElement("canvas"); c.width = w; c.height = h;
        const ctx = c.getContext("2d");
        if (ctx) {
          m = {
            c, ctx,
            // how black the unlit floor gets, and how fully the lights clear it
            dark: LIGHT_DARK, amb: LIGHT_AMB, heroA: LIGHT_HERO_A, fireA: LIGHT_FIRE_A,
            heroR: HERO_GLOW_R, fireR: BRAZIER_GLOW_R,
            // one oversized soft disc, stretched to the viewport when blitted, used as the
            // ambient floor under the veil so no corner reaches full darkness. Warm amber rather
            // than white: see the note in _lightPass on the grey film this pass used to cause.
            amb2: glowSpritePair("255,178,96", Math.round(Math.max(w, h) * .78), 1, [[.55, "rgba(255,178,96,.96)"], [.85, "rgba(255,150,64,.8)"]]),
            // Long, shallow falloff rather than a tight core. A steep gradient is what made the
            // lit area read as a small disc with a hard wall of dark around it; easing most of the
            // way out keeps the hall visible right to the edge of each light.
            //
            // The hero's mask in particular used to hold .9 all the way out to .42 of the radius,
            // which is what drew the visible rim: the clearing was essentially flat across a wide
            // disc and then had to fall away somewhere, so it fell off as an edge. Easing from the
            // centre instead - and topping out at .84 rather than .9 - means there is no plateau to
            // end, so the pool fades out rather than terminating.
            hero: glowSpritePair("255,255,255", HERO_GLOW_R, 1, [[.18, "rgba(255,255,255,.84)"], [.42, "rgba(255,255,255,.56)"], [.64, "rgba(255,255,255,.3)"], [.84, "rgba(255,255,255,.1)"], [1, "rgba(255,255,255,0)"]]),
            fire: glowSpritePair("255,255,255", BRAZIER_GLOW_R, 1, [[.44, "rgba(255,255,255,.86)"], [.7, "rgba(255,255,255,.52)"], [.88, "rgba(255,255,255,.22)"]]),
          };
        }
      }
    } catch (eMask) { m = null; }
    _LIGHT_MASKS.set(key, m);
  }
  return m;
}
// Pre-build every zone's halo at startup. Building an 800px radial sprite costs a large
// one-off rasterisation; done lazily it landed on the first frame of a new zone, which read
// as a hitch exactly when the arena and its crowd appeared. Now it never happens mid-fight.
try {
  for (const z of ZONES) { const k = z && z.accent, rgb = k && hexToRgb(k); if (rgb && !GLOW_hero[k]) GLOW_hero[k] = glowSpritePair(rgb, HERO_GLOW_R, .055, [[.2, "rgba(" + rgb + ",.035)"], [.5, "rgba(" + rgb + ",.018)"], [.78, "rgba(" + rgb + ",.006)"], [1, "rgba(" + rgb + ",0)"]], .014); }
  // The warm torch wash is keyed "__warm" at its build site, and the cache lookup there is by that
  // same key, so the prewarm has to use it too. It used to prewarm under key "255,168,74", which no
  // caller ever reads: the expensive 800px rasterisation still landed on the first frame that showed
  // Gabriel, which is the hitch this block exists to prevent.
  if (!GLOW_hero.__warm) GLOW_hero.__warm = glowSpritePair(WARM_TORCH, HERO_GLOW_R, .13, [[.2, "rgba(" + WARM_TORCH + ",.1)"], [.5, "rgba(" + WARM_TORCH + ",.05)"], [.78, "rgba(" + WARM_TORCH + ",.017)"], [1, "rgba(" + WARM_TORCH + ",0)"]], .038);
} catch (ePrewarm) { /* falls back to lazy build */ }
// The hit-flash sprite cache that used to live here is gone with the flash. glowSpriteWhite had no
// remaining caller once drawEnemyEnt stopped whitening silhouettes; makeGlowSprite and the
// hero/brazier/dust caches above are all still in use.
// vignetteSprite / VIGc were removed when the canvas vignette wash went away, on the reasoning
// that a viewport-sized fill could never be safe here. That ban overshot: with it went every
// edge falloff, so the arena lost its dark borders entirely and read as a flat evenly-lit plane.
// The vignetteSprite below restores that edge falloff under the same discipline that made the
// removal safe - an offscreen scratch surface whose gradient it owns, blitted as an image, never
// a coloured fillRect on the visible canvas.

// Torchlit darkness. Not a haze: a real edge falloff, transparent through the middle and falling
// to nearly black at the corners, so the arena reads as a hall lit by a few fires rather than an
// evenly-lit plane. Built once per viewport because a full-screen fill every frame is the exact
// shape of the old "one flat colour buried the arena" bug - this is an offscreen scratch surface
// whose gradient it owns, and it is blitted as an image, never as a coloured fillRect.
//
// It is warm-dark, not neutral grey: the shadow colour carries a little red/brown, which keeps the
// deep areas reading as firelit stone instead of desaturating them towards the grey film that was
// reported over the middle of the screen.
let _VIG = null, _VIG_KEY = "";
function vignetteSprite(w, h) {
  const key = w + "x" + h;
  if (_VIG && _VIG_KEY === key) return _VIG;
  try {
    if (w <= 0 || h <= 0 || w * h > 1920 * 1200) return _VIG = null;
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const x = c.getContext("2d");
    if (!x) return _VIG = null;
    const rg = x.createRadialGradient(w * .5, h * .5, 0, w * .5, h * .5, Math.max(w, h) * .72);
    // Fully clear core, so nothing films over the middle where Gabriel and the fires are doing the
    // work. Stops are explicit because a default 0-alpha start and a 1-alpha end would darken the
    // entire viewport, which is the haze this is meant to replace.
    // Clear core, so nothing washes the middle where the hero and the fires are doing the work.
    rg.addColorStop(0, "rgba(8,3,1,0)");
    rg.addColorStop(.52, "rgba(8,3,1,0)");
    rg.addColorStop(.78, "rgba(9,3,1,.34)");
    rg.addColorStop(1, "rgba(6,2,1,.66)");
    x.fillStyle = rg; x.fillRect(0, 0, w, h);
    _VIG = c; _VIG_KEY = key;
  } catch (eVig) { _VIG = null; }
  return _VIG;
}

function renderBase() {
  const g = CX;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.filter = "none";
  g.shadowBlur = 0; g.shadowColor = "transparent";
  g.lineCap = "butt"; g.lineJoin = "miter"; g.lineWidth = 1;
  g.imageSmoothingEnabled = true;
  if (G.shake > 0) g.translate(G.shX || 0, G.shY || 0);
  // clamp() kept the old six-realm game in range; with the endless loop the zone index has to be
  // wrapped with realmIndex() or every circuit past the first picks the last realm's palette.
  const zc = (ZONES[realmIndex(G.zone)] || ZONES[0]).wall;
  // solid backdrop: if a draw call throws mid-frame, the screen shows stone, never a
  // canvas-wide gradient fill (the old "whole screen turns one solid colour" bug)
  g.fillStyle = typeof zc === "string" ? zc : "#241f24"; g.fillRect(0, 0, W, H);
  renderWorld(g);
}

// The shadow veil over unlit floor, and the lights punched out of it. Runs after the arena and
// every entity are on screen but before the HUD-style passes, so darkness veils the world and
// everything standing in it without dimming anything drawn afterwards.
//
// Why this exists: the additive halo pass on its own - a hero glow built at radius 800 and
// brazier pools at 300, composited "lighter" across the viewport - lifted every tile at once.
// The arena read as flat foglit and the black edges vanished. Lights were resized to actually
// illuminate a room, and this mask restores the falloff that makes the place read torchlit.
//
// The veil is a scratch canvas, never a fillRect on the visible one. It is filled with a shadow
// colour this function explicitly sets itself, the lights punch holes in it with destination-out,
// and it is blitted once. A screen-covering fill is only ever safe on a surface whose fill colour
// the caller owns: on the visible canvas, a leftover fillStyle from the last entity drawn - that
// health-bar green - would bury the arena, which is exactly how the old vignette flashed green.
function _lightPass(g) {
  if (typeof _lightMask !== "function") return;
  const LIGHT = _lightMask(W, H);
  // One early return, not a half-drawn veil. The previous version returned here only on a missing
  // mask, so a mask that was built but lacked a sub-sprite still painted the dark flood and the
  // ambient lift and then threw on the first drawImage - leaving the whole viewport under one flat
  // translucent wash with no light holes punched in it, which is precisely a milky grey film over
  // the middle of the screen with faded colours. Now any incomplete mask means no veil at all:
  // bright and undarkened rather than hazy.
  if (!LIGHT || !LIGHT.ctx || !LIGHT.c || !LIGHT.hero || !LIGHT.fire || !LIGHT.amb2) return;
  g.save();
  g.setTransform(1, 0, 0, 1, 0, 0);      // screen space: the veil is a lens
  // The mask canvas is viewport-sized, so it is painted entirely in mask-local (screen) space and
  // every world position is converted through the camera first. That keeps the lights stuck to
  // their tiles in the hall while the canvas transform stays identity for the final blit.
  //
  // The previous version set a camera transform on the visible canvas and painted the mask in raw
  // world coordinates. Both halves were wrong in the same direction: the flood fill and the hero
  // halo were placed at (G.camX, G.camY) inside a canvas whose pixels only span 0..W, so as soon
  // as the camera left the map origin part of the veil simply was not painted. The hero's pool
  // drifted away from him and the unlit remainder of the mask kept whatever the veil colour was -
  // a uniformly murky screen with a small bright disc, which is exactly what it now looks like.
  const vx = G.camX - (G.shX || 0), vy = G.camY - (G.shY || 0);
  g.globalCompositeOperation = "source-over";
  g.globalAlpha = 1;
  const lg = LIGHT.ctx;
  lg.setTransform(1, 0, 0, 1, 0, 0);
  lg.globalCompositeOperation = "source-over";
  lg.globalAlpha = 1;
  // Mask-local: always the full canvas, never a world rect.
  lg.clearRect(0, 0, W, H);
  // set explicitly every frame so no inherited or leftover style can ever be what floods it
  lg.fillStyle = "rgba(6,3,2," + LIGHT.dark + ")";
  lg.fillRect(0, 0, W, H);
  lg.globalCompositeOperation = "destination-out";
  // hero torchlight: a disc of vision around him, soft at the rim
  lg.globalAlpha = LIGHT.heroA;
  lg.drawImage(LIGHT.hero.s, P.x - vx - LIGHT.hero.radius, P.y - 14 - vy - LIGHT.hero.radius);
  // brazier pools, pulsing on the same phase as their flames so light and fire agree
  for (const pr of G.props) {
    if (pr.kind !== "brazier") continue;
    // pr.x/pr.y is the BASE of the stand; the flame spans -34..-19, so its centre is ~-26.
    const sx = pr.x - vx, sy = pr.y - vy;
    if (sx < -260 || sx > W + 260 || sy < -260 || sy > H + 260) continue;
    const pulse = .78 + Math.sin(G.t * 7 + pr.x * .05) * .16;
    lg.globalAlpha = clamp(LIGHT.fireA * pulse, 0, 1);
    lg.drawImage(LIGHT.fire.s, sx - LIGHT.fire.radius, sy - 26 - LIGHT.fire.radius);
  }
  // Ambient floor under the veil. destination-out can only ever take alpha away, so punching the
  // lights out cannot raise a minimum brightness on its own. Without this, the far corners of a
  // large hall - the parts no brazier or the hero ever reaches - sit at full LIGHT_DARK and go
  // murky. Blending a soft full-viewport light in with 'lighter' raises the whole veil to
  // LIGHT_AMB, where LIGHT_AMB is always <= LIGHT_DARK, so the darkest possible floor is exactly
  // (LIGHT_DARK - LIGHT_AMB) and the lights still visibly win where they are.
  lg.globalCompositeOperation = "lighter";
  lg.globalAlpha = clamp(LIGHT.amb, 0, LIGHT.dark);
  // Tinted, not white. This disc is the only thing that reaches pixels no torch touches, and as a
  // pure-white additive lift it desaturated the whole hall towards grey - the milky film. A warm
  // amber floor keeps those deep areas in the firelight palette instead of pulling them to grey.
  lg.drawImage(LIGHT.amb2.s, 0, 0, W, H);
  lg.globalAlpha = 1;
  lg.globalCompositeOperation = "source-over";
  g.drawImage(LIGHT.c, 0, 0);

  // Corner falloff over the light mask. The veil is a per-light mask, and in a large hall it can
  // leave the far corners only veil-dark, which reads as a flat evenly-lit plane instead of a
  // torchlit room. This is a blit of the cached offscreen gradient, not a coloured fillRect on the
  // visible canvas, and it is fully transparent across the middle, so it cannot film over Gabriel
  // or the fires the way a screen-wide ambient lift does.
  try {
    const vig = vignetteSprite(W, H);
    if (vig) g.drawImage(vig, 0, 0);
  } catch (eVig2) { /* decorative falloff only */ }
  g.restore();
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.filter = "none";
}

function renderWorld(g) {
  g.save();
  g.translate(-G.camX, -G.camY);

  // arena backdrop (fallback fill if nothing is baked)
  if (D) {
    g.fillStyle = ZONES[realmIndex(G.zone)].floor;
    g.fillRect(0, 0, D.wpx, D.hpx);
  }

  // baked dungeon (floors, walls, baked props)
  if (D && D.baked) { g.drawImage(D.baked, 0, 0); }

  // dynamic props (animated)
  const time = G.t * 1000;
  for (const pr of G.props) {
    if (pr.kind === "pillar") continue;
    if (pr.x > G.camX - 120 && pr.x < G.camX + W + 120 && pr.y > G.camY - 140 && pr.y < G.camY + H + 120) drawProp(g, pr.kind, pr.x, pr.y, time);
  }

  // pickup glow markers (cached sprite blits: no gradients or paths per frame)
  g.save(); g.globalCompositeOperation = "lighter";
  for (const pk of G.pickups) {
    if (pk.x < G.camX - 40 || pk.x > G.camX + W + 40 || pk.y < G.camY - 46 || pk.y > G.camY + H + 40) continue;
    const pkG = pk.kind === "heart" ? GLOW_heart : GLOW_arrows;
    // -6 lifts the pool onto the icon sprite (drawn at pk.y-15, radius ~9) instead of the raw origin
    g.drawImage(pkG.s, pk.x - pkG.radius, pk.y - pkG.radius - 6);
  }
  g.restore();

  // depth sort drawables (reused array, numeric keys: no per-frame closures)
  DRAWBuf.length = 0;
  for (const pk of G.pickups) {
    if (pk.x < G.camX - 60 || pk.x > G.camX + W + 60 || pk.y < G.camY - 60 || pk.y > G.camY + H + 60) continue;
    DRAWBuf.push(pk); pk._k = pk.y * 2 + 1; pk._t = 1;
  }
  // Depth sort drawables (reused array, numeric keys: no per-frame closures).
  //
  // Sorting on the raw floor line is right for entities of comparable height and wrong for a
  // 200 px boss: Skarnax's feet sit at his own y, while the elf standing "just above" him has a
  // slightly smaller y and therefore painted *last*, i.e. on top of the boss's whole body. What
  // the owner saw was Gabriel completely hidden behind Skarnax's flank even though he is standing
  // north of him. The comparison has to use the feet line of whatever is actually being painted:
  // a tall sprite whose visible mass is well above its origin should not occlude a smaller entity
  // that is genuinely further from the camera. So each entity sorts on its own baseline (y), and
  // only entities whose drawn bodies overlap that baseline compete; the boss gets his baseline
  // nudged toward the camera by the fraction of his height that is drawn *below* his feet line,
  // which is the only part of him that can legitimately cover something standing north of him.
  for (const e of G.enemies) { DRAWBuf.push(e); e._k = e.y * 2 + depthBias(e); e._t = 0; }
  P._k = P.y * 2; P._t = 2; DRAWBuf.push(P);
  DRAWBuf.sort(sortByDepth);
  const _tDE = performance.now();
  for (const d of DRAWBuf) {
    if (d._t === 1) drawPickup(g, d, time);
    else if (d._t === 0) {
      // off-screen enemies used to be built, depth-sorted AND fully rasterised every frame:
      // in a big fight the level holds far more brutes than the viewport shows, so this paid
      // for enemies the player cannot see. Skip anything outside the camera plus a margin.
      if (d.x < G.camX - 90 || d.x > G.camX + W + 90 || d.y < G.camY - 130 || d.y > G.camY + H + 90) continue;
      drawEnemyEnt(g, d, time);
    }
    else drawHeroEnt(g, time);
  }
  profAdd("drawEnemies", performance.now() - _tDE);

  // projectiles
  for (const pr of G.projectiles) {
    if (pr.kind === "web") { strokeCircle(g, pr.x, pr.y, 6, "rgba(230,230,250,.9)", 2); strokeCircle(g, pr.x, pr.y, 2.5, "#b9a6d6", 1); }
    else if (pr.fromEnemy || !pr.kind) { drawArrowProj(g, pr); }
    else { strokeCircle(g, pr.x, pr.y, 7, "rgba(140,200,255,.85)", 2); glowSprite(g, pr.x, pr.y, 11, "127,208,255", .5); strokeCircle(g, pr.x, pr.y, 3.5, "#eaffff", 1); }
  }
  // particles
  // particles (visible subset only; cull in place, no per-frame slice copy)
  if (G.particles.length > 480) G.particles.splice(0, G.particles.length - 420);
  for (const pt of G.particles) {
    if (pt.x < G.camX - 20 || pt.x > G.camX + W + 20 || pt.y < G.camY - 20 || pt.y > G.camY + H + 20) continue;
    g.globalAlpha = clamp(pt.t / pt.life, 0, 1);
    g.fillStyle = pt.color; g.fillRect(pt.x - pt.size / 2, pt.y - pt.size / 2, pt.size, pt.size);
  }
  g.globalAlpha = 1;
  // texts
  g.textAlign = "center";
  for (const tx of G.texts) {
    g.globalAlpha = clamp(tx.t / .9, 0, 1);
    g.font = (tx.big ? "bold 22px" : "bold 15px") + " 'Trebuchet MS', sans-serif";
    g.lineWidth = 3; g.strokeStyle = "rgba(0,0,0,.7)"; g.strokeText(tx.str, tx.x, tx.y);
    g.fillStyle = tx.color; g.fillText(tx.str, tx.x, tx.y);
  }
  g.globalAlpha = 1;
  g.restore();
  // end of world transform: nothing inside it may leak state into the overlays
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.filter = "none";
  g.shadowBlur = 0; g.shadowColor = "transparent";
}

function renderOverlay(g) {
  // torchlight halo around hero + braziers (warm, zone-tinted)
  // Both lights must be centred on what emits them, not on its ground point. The hero is
  // drawn from the feet up (body near -24, head near -45), so a halo centred on P.y hung
  // below him and read as a glow off to the bottom-right. Shake is applied by the world
  // pass, so re-add it here or the light detaches from him while the screen shakes.
  // Anchor on Gabriel's visual centre. drawElf is drawn feet-up from P.y: body ellipse centred
  // about -10 (spanning -30..10) and head about -28, so the sprite occupies roughly y-38..y+10
  // and its centre is ~-14. The old -26/-27 put the bright core on his chest while his sprite
  // centre sat lower, so the glow looked detached from him.
  const hx = P.x - G.camX + (G.shX || 0), hy = P.y - G.camY - 14 + (G.shY || 0);
  const warm = (ZONES[realmIndex(G.zone)].accent) || "#ffaa50";
  // halo pass: cached sprite blits only. No createRadialGradient and no full-viewport
  // fill here - a gradient built and rasterised every frame was a large part of the cost
  // in big fights, and "lighter" combined with any screen-covering fill is what made the
  // whole arena flash a single bright colour.
  g.save();
  g.globalCompositeOperation = "lighter";
  // hero halo: a warm torch wash, then a thin zone-coloured rim so each hall still has its own
  // cast. The zone tint alone was the problem - in the neutral/cool zones it lifted the centre
  // with grey-violet, which reads as haze over the middle of the screen rather than firelight.
  //
  // Both discs are now built from a curve that starts easing immediately and reaches 0, rather than
  // holding a plateau to .34 and cutting off. The plateau was the bright beige core with a visible
  // rim that was reported: additive "lighter" over stone at .26/.12 out to a third of the radius is
  // a spotlight, and everything inside it lost its saturation. Peak added light is roughly halved
  // and the falloff is continuous, so it reads as a glow that simply runs out.
  const halo = GLOW_hero[warm] || (GLOW_hero[warm] = glowSpritePair(hexToRgb(warm), HERO_GLOW_R, .055, [[.2, "rgba(" + hexToRgb(warm) + ",.035)"], [.5, "rgba(" + hexToRgb(warm) + ",.018)"], [.78, "rgba(" + hexToRgb(warm) + ",.006)"], [1, "rgba(" + hexToRgb(warm) + ",0)"]], .014));
  const torch = GLOW_hero.__warm || (GLOW_hero.__warm = glowSpritePair(WARM_TORCH, HERO_GLOW_R, .13, [[.2, "rgba(" + WARM_TORCH + ",.1)"], [.5, "rgba(" + WARM_TORCH + ",.05)"], [.78, "rgba(" + WARM_TORCH + ",.017)"], [1, "rgba(" + WARM_TORCH + ",0)"]], .038));
  g.drawImage(torch.s, hx - torch.radius, hy - torch.radius);
  g.drawImage(halo.s, hx - halo.radius, hy - halo.radius);
  // firelight pooling around animated braziers (cached sprite, pulsing alpha)
  for (const pr of G.props) {
    if (pr.kind !== "brazier") continue;
    // pr.x/pr.y is the BASE of the stand. The flame spans -34..-19 (bright core -30..-20), so its
    // centre is ~-26. Light the flame, since that is what the eye reads as the source.
    const bx = pr.x - G.camX + (G.shX || 0), by = pr.y - G.camY - 26 + (G.shY || 0);
    if (bx < -200 || bx > W + 200 || by < -200 || by > H + 200) continue;
    g.globalAlpha = .82 + Math.sin(G.t * 7 + pr.x * .05) * .18;
    g.drawImage(GLOW_fire.s, bx - GLOW_fire.radius, by - GLOW_fire.radius);
  }
  g.globalAlpha = 1;
  g.restore();

  // Torchlit darkness moved to _lightPass(), which runs after the world pass so the veil covers
  // the arena and everything standing in it. Nothing here may be a screen-covering fill.

  // Vignette: none, deliberately. This used to be a viewport-sized fillRect every frame, and any
  // full-screen fill at this point is the "everything draws, then one flat colour is painted over
  // the whole play area" failure: if its fillStyle isn't the intended image/sprite, the arena is
  // buried under whatever colour was left over from the previous draw - which is why it matched
  // the health-bar green whenever damaged brutes were on screen (that green is the final fillStyle
  // set by drawEnemyEnt). The mood it added was a ~14% darkening at the corners; the #vignette DOM
  // layer (style.css) already provides that edge falloff, and _lightPass now owns the real
  // light falloff. Atmosphere must never be a screen-covering canvas fill.

  // drifting dust motes / embers (screen-space, purely decorative). Decorative, so it stays under
  // the veil - it is part of the hall's air, not a message.
  try { drawMotes(g); } catch (err) { /* decorative only */ }
  // every decorative pass is isolated: nothing below may inherit leaked composite/alpha/filter state
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.filter = "none";
  g.shadowBlur = 0; g.shadowColor = "transparent";

  // objective arrow and announcements moved to renderBanners(), which runs after the shadow veil
}

// World messages, drawn after the shadow veil so darkness cannot muddy them.
//
// The veil is a translucent black wash over the whole viewport with holes punched for the lights.
// Anything painted before it inherits that wash, and these three - the exit arrow and "Onward"
// hint, the aim reticle, and the announce banner - are exactly the text the owner reported as
// muddy: the Reinforcements and boss-name banners and the bottom hint all sit in dim corners or
// pulse at partial alpha, where the veil was reducing them towards the background instead of
// lifting them off it. Messages are not part of the hall, so they are not veiled with it.
function renderBanners(g) {
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.filter = "none";
  g.shadowBlur = 0; g.shadowColor = "transparent";

  // objective arrow when path clear
  if (G.state === "play" && D && (G.wave === "cleared" || G.wave === "preboss")) {
    const ex = D.exit.x - G.camX, ey = D.exit.y - G.camY;
    if (ex > -40 && ex < W + 40 && ey > -40 && ey < H + 40) {
      const pulse = .55 + Math.sin(G.t * 5) * .3;
      g.save();
      g.strokeStyle = `rgba(255,205,84,${pulse})`; g.lineWidth = 3; g.shadowColor = "#ffce54"; g.shadowBlur = 16;
      g.beginPath(); g.arc(ex, ey - 8, 22 + Math.sin(G.t * 3) * 3, 0, TAU); g.stroke();
      g.shadowBlur = 0; g.fillStyle = `rgba(255,225,140,${pulse})`;
      g.font = "bold 16px 'Trebuchet MS'"; g.textAlign = "center";
      g.fillText("⬇", ex, ey - 2);
      g.restore();
    }
    const pulse2 = .6 + Math.sin(G.t * 5) * .3;
    g.fillStyle = `rgba(255,205,84,${pulse2})`; g.font = "bold 15px 'Trebuchet MS'"; g.textAlign = "center";
    // Past realm 6 the next realm wraps to the start of the circuit, so the sign points at realm 1
    // (or names the crossing when this is the last realm before the quest panel).
    const nextIdx = realmIndex(G.zone) + 1;
    const onward = nextIdx >= REALM_COUNT
      ? "the next circuit of " + REALM_COUNT + " realms"
      : ZONES[nextIdx].name;
    g.fillText("➡ Onward to " + onward, W / 2, H - 26);
  }

  // aim reticle showing where Gabriel is shooting
  if (G.state === "play") {
    // `|| 0` here coerced a legitimate PI (aim-left) to 0 via string truthiness, snapping the
    // reticle onto Gabriel instead of to his left. Aim now defaults to the direction he is facing.
    const sa = Number.isFinite(P.shootAng) ? P.shootAng : (P.face >= 0 ? 0 : Math.PI);
    const ax = P.x - G.camX + Math.cos(sa) * 110, ay = P.y - G.camY - 16 + Math.sin(sa) * 110;
    g.save(); g.globalAlpha = .5 + Math.sin(G.t * 6) * .15;
    g.strokeStyle = "#dff0d8"; g.lineWidth = 2;
    g.beginPath(); g.arc(ax, ay, 7, 0, TAU); g.stroke();
    g.beginPath(); g.moveTo(ax - 12, ay); g.lineTo(ax - 8, ay); g.moveTo(ax + 8, ay); g.lineTo(ax + 12, ay); g.stroke();
    g.restore();
  }

  // announcement banner
  if (G.announce) {
    const a = clamp(G.announce.t / .5, 0, 1) * clamp((2.8 - G.announce.t) / .3, 0, 1);
    g.globalAlpha = a; g.textAlign = "center";
    g.font = "bold 40px 'Trebuchet MS', sans-serif";
    g.lineWidth = 6; g.strokeStyle = "rgba(0,0,0,.8)";
    g.strokeText(G.announce.title, W / 2, H * .34);
    g.fillStyle = "#ffce54"; g.fillText(G.announce.title, W / 2, H * .34);
    g.font = "italic 17px 'Trebuchet MS'";
    g.strokeText(G.announce.sub, W / 2, H * .34 + 30);
    g.fillStyle = "#e8c9a0"; g.fillText(G.announce.sub, W / 2, H * .34 + 30);
    g.globalAlpha = 1;
  }

  // hurt flash
  if (P.hitFx > 0) { $("vignette").classList.add("hurt"); } else $("vignette").classList.remove("hurt");
}

function render() {
  renderBase();
  // Additive halos first: they brighten the floor and the actors, so they have to be under the
  // veil. The veil then punches its lights out of the dark, which only works on top of them.
  //
  // renderBase ends on an identity transform, so both halves below take the camera themselves:
  // renderOverlay converts world positions with G.camX/G.camY internally, and _lightPass builds
  // its mask in mask-local pixels from camera-converted coordinates. The veil must be applied
  // between the halos and the text; if the transform were left set here, the veil's setTransform
  // would still be correct but the halos would be blitted twice-offset, which is the failure that
  // produces a bright disc stranded in the corner of a dark hall.
  renderOverlay(CX);
  _lightPass(CX);
  // Announcements last. A zone banner, a boss name and the "Onward" hint are world *messages*,
  // not world objects, and drawing them before the veil laid a translucent black wash over them:
  // the muddy text reported on the Reinforcements and boss-name banners and the bottom hint. The
  // veil darkens the hall, nothing drawn after it.
  renderBanners(CX);
}

// ---------- main loop ----------
let last = performance.now();
let RENDER_FAILS = 0, BAD_FRAME_MS = 0;
let UPDATE_FAILS = 0, LAST_UPDATE_ERR = "", LAST_RENDER_ERR = "", LAST_FRAME_MS = 0, FRAME_SEEN = 0;
// Consecutive update failures, not lifetime: a healthy frame resets it (see frame()). The bar used
// to only ever count upward and nothing ever cleared it, so one transient throw left "updFails" on
// screen for the rest of the run and >120 of them auto-paused the game even though nothing was
// wrong any more. Counting a streak keeps the auto-pause as a real runaway guard while letting both
// the bar and the counter recover on their own.
// Timing counters (no-alloc, no per-frame DOM): read PROF from the console to see which
// half of the frame is spending time during a crowd. Cheap enough to leave in; it is the
// only way to confirm a stutter is AI (PROF.ai) rather than rasterisation (PROF.render).
// hitFlashes is no longer incremented anywhere now that the flash is dropped; it is kept as a
// placeholder so the console reading of PROF is unchanged for anyone comparing sessions.
const PROF = { update: 0, render: 0, ai: 0, drawEnemies: 0, hitFlashes: 0, hits: 0, frames: 0, worst: 0, worstWhat: "" };
// Audio requests in the current frame, reset at the top of frame(). Instrumented, not a limit:
// see the note in frame() about why an audio call is one of the plausible one-frame hitches.
let HITS_THIS_FRAME = 0;
function profAdd(key, ms) {
  PROF[key] = (PROF[key] || 0) + ms;
  PROF.frames++;
  if (ms > PROF.worst) { PROF.worst = ms; PROF.worstWhat = key; }
}
// True only for a fully populated arena: dimensions usable and every method the frame loop calls
// actually callable. `D` is a plain global that startZone() reassigns while a run is live, so a
// single `D &&` guard in AI cannot prove a method exists - and every `D && D.someMethod(...)` call
// site that *does* guard is silently disabled by the same class of breakage, which is why the
// crowds stopped steering around pillars before they ever threw. Readiness is checked once per
// frame here rather than at each call site.
function arenaReady() {
  return !!D && D.RW > 0 && D.RH > 0 && D.wpx > 0 && D.hpx > 0 &&
    isFinite(D.wpx) && isFinite(D.hpx) &&
    !!D.spawn && isFinite(D.spawn.x) && isFinite(D.spawn.y) &&
    ARENA_METHODS.every(k => typeof D[k] === "function");
}

function frame(now) {
  // The hit-flash scratch layer used to be cleared here once per frame at identity. The layer and
  // the flash are both gone (see drawEnemyEnt), so there is nothing left to clear.
  // Audio work is folded into the update timing below, and the audio calls themselves are counted
  // rather than measured. Each hit, kill and arrow impact creates oscillator/bufferSource nodes, and
  // node creation plus biquad setup is the synchronous part of WebAudio: when an arrow volley lands
  // in a crowd, a dozen hits fire inside one tick. The per-sound gate in AudioSys already throttles
  // by time, but only per sound key, so hit + crit + die + slash can each still fire in the same
  // frame. PROF.hits counts what the browser was asked to voice in a single frame, which is the
  // number to read next to PROF.worst when a volley-into-a-crowd shows up as a one-frame hitch that
  // is in neither PROF.ai nor PROF.render.
  HITS_THIS_FRAME = 0;
  // Measured at the top of the frame, like the other one-per-frame diagnostics: this is the value
  // the diagnostic bar shows as updFails, and clearing it inside the update branch would leave it
  // pinned at the last failing count for the rest of the run after a recovery, which reads as
  // "the bar is still there". It also lets the bar disappear on its own instead of sticking.
  const ARENA_LIVE = arenaReady();
  const dt = Math.min(.033, Math.max(.001, (now - last) / 1000)); last = now;
  // An unusable arena must never reach update(): every AI movement path dereferences D's methods,
  // and each throw would count as an update failure, paint the diagnostic bar, and eventually trip
  // the UPDATE_FAILS auto-pause. The arena fixes itself as soon as startZone() publishes a complete
  // one, so the correct response is to keep rendering the last frame and skip simulation, not to
  // spend the run on a bar of yellow TypeErrors. The arena cannot disappear or become half-built
  // mid-frame - the only reassignment is the single publish in startZone(), synchronously at the
  // realm transition - so one check per frame is sufficient and no per-call-site retry is needed.
  if (ARENA_LIVE) UPDATE_FAILS = 0;
  if (G.state === "play" && !ARENA_LIVE) { G.state = "pause"; showScreen("pause"); }
  if (G.state === "play") {
    const t0 = now;
    // update() was outside the guard: one throw here aborted requestAnimationFrame forever,
    // leaving the canvas stuck on whatever fillRect ran last (a flat dark rectangle) while the
    // HUD kept showing stale values - which looked like "arena covered by one flat colour".
    const _tu = performance.now();
    try { update(dt); }
    catch (err) {
      UPDATE_FAILS++;                 // consecutive, see the declaration; a good frame clears it
      // surface it on the canvas: a silent death here is undebuggable in a sandboxed preview
      LAST_UPDATE_ERR = (err && err.stack ? String(err.stack) : String(err)).split("\n").slice(0, 4).join(" | ");
      if (UPDATE_FAILS === 12) console.warn("update error", err);
      if (UPDATE_FAILS > 120) { G.state = "pause"; showScreen("pause"); }
    }
    profAdd("update", performance.now() - _tu);
    PROF.hits = HITS_THIS_FRAME;    // audio node requests in the frame just timed, see above
    // If a frame ever takes >2s, the tab is effectively hung. Stop simulating (rendering
    // continues, so the picture stays alive) instead of wedging the browser forever.
    if (performance.now() - t0 > 2000) { BAD_FRAME_MS++; LAST_FRAME_MS = performance.now() - t0; if (BAD_FRAME_MS === 4) { G.state = "pause"; showScreen("pause"); } }
    else { BAD_FRAME_MS = 0; LAST_FRAME_MS = performance.now() - t0; }
  }
  try {
    const _tr = performance.now();
    render();
    profAdd("render", performance.now() - _tr);
    RENDER_FAILS = 0;
  } catch (err) {
    // a decorative/edge-case draw error must never leave a frozen or solid-colour screen.
    // Never null out D (the arena): renderBase would then paint one flat wall colour over
    // everything, which is exactly the "screen turns solid colour" symptom.
    RENDER_FAILS++;
    LAST_RENDER_ERR = (err && err.stack ? String(err.stack) : String(err)).split("\n").slice(0, 4).join(" | ");
    if (RENDER_FAILS === 12) console.warn("render error", err);
    try { CX.setTransform(1, 0, 0, 1, 0, 0); CX.globalAlpha = 1; CX.globalCompositeOperation = "source-over"; CX.filter = "none"; CX.shadowBlur = 0; } catch (e2) { }
  }
  // Diagnostic overlay: only paints when something actually went wrong, so the arena is
  // never covered by debug UI. If it does appear, it names the failing pass and its state.
  if (UPDATE_FAILS || RENDER_FAILS || (D && !ARENA_LIVE)) try {
    FRAME_SEEN = (FRAME_SEEN || 0) + 1;
    CX.setTransform(1, 0, 0, 1, 0, 0);
    CX.globalAlpha = 1; CX.globalCompositeOperation = "source-over"; CX.filter = "none"; CX.shadowBlur = 0;
    CX.fillStyle = "rgba(0,0,0,.78)"; CX.fillRect(0, 0, W, 46);
    CX.fillStyle = "#ffd76b"; CX.font = "11px monospace"; CX.textAlign = "left";
    CX.fillText("frames=" + FRAME_SEEN + " state=" + G.state + " wave=" + G.wave
      + " D=" + (D ? D.RW + "x" + D.RH + " baked=" + !!D.baked : "NULL")
      + " foes=" + G.enemies.length + " WxH=" + W + "x" + H, 6, 14);
    CX.fillText("updFails=" + UPDATE_FAILS + " rndFails=" + RENDER_FAILS
      + " lastFrameMs=" + Math.round(LAST_FRAME_MS) + " | " + (LAST_UPDATE_ERR || LAST_RENDER_ERR || "").slice(0, 120), 6, 30);
  } catch (e3) { }
  requestAnimationFrame(frame);
}

// r is the shadow's half-width. Contact shadows are kept near the body's own footprint and
// translucent, so a big creature reads as lit from the torches rather than as a dark slab.
function shadow(g, x, y, r) { if (!(r > 0)) return; g.fillStyle = "rgba(0,0,0,.26)"; g.beginPath(); g.ellipse(x, y + 2, r, r * .34, 0, 0, TAU); g.fill(); }

// The shadow used to be `e.r * e.scale` for bosses, which for a quadruped is the *body* radius
// stretched by the zoom. A wolf is drawn roughly 40 px wide before zoom while its collision radius
// is 21, so Skarnax got a ~118 px half-width ellipse - a dark slab under him wider than the art,
// which is what read as "a giant shadow". Derive the footprint from the same art extents the
// renderer strokes, and cap the darkening so a big boss cannot blot out the torchlit floor.
const SHADOW_HALF = {
  // Contact shadow half-widths are authored at PACK scale and multiplied by e.scale for a boss, so
  // the boss entry must be measured from the pack wolf art, not from the enlarged boss. The bushy
  // tail ends at x = -39 and the nose tip is at x = 35.6, so half span ≈ 37 plus stroke width.
  // This used to be 40 with the boss scale applied, which - combined with the old separate dark
  // mantle that drawFenris used to paint behind the body - is what made Skarnax own a pool of black
  // under him. The mantle is gone and this ellipse is the only shadow anyone draws.
  fenris: 40,
  troll: 24,   // drawBoss troll: ±22 barrel plus the club swing, without the club itself
  spider: 42,  // eight legs splay out to x ≈ ±42
  captain: 20, // brute body 16 plus shield at x = -26
  hollowcrown: 20,
};
function shadowHalfWidth(e) {
  const s = e.boss ? (e.scale || 1) : 1;
  const drawn = SHADOW_HALF[e.kind];
  const half = drawn !== undefined ? drawn * s : e.r * s;
  // The cap is what stops a scaled-up boss from owning the floor: beyond ~1.8 tiles of shadow the
  // ellipse reads as a void rather than a contact shadow, and the art is not that wide anyway.
  // For Skarnax at scale 2.8 the art wants 112 px, so this cap is doing the real work: a 62 px
  // half-width ellipse under a 200 px wolf reads as weight on the ground, while a full-length one
  // read as a hole in the dungeon floor.
  return Math.max(6, Math.min(half, 62));
}

// ---------------------------------------------------------------------------
// Melee reach, from the art rather than a tuned radius.
//
// The old value was `55 * e.scale + 14/22` for every boss. For Skarnax (scale 2.15) that is a
// ~132 px contact circle, i.e. he damaged the hero from four tiles away while his muzzle was
// still clearly in the air. Both bosses draw themselves at e.scale about their feet, so the
// honest reach is the drawn length of the attacking limb plus the entity radius, with a small
// allowance for the paws stepping in. Anything not given an entry here keeps the previous
// behaviour, since no other boss was reported as reaching too far.
//
// The numbers below are half-extents in the art's own local units, read off the coordinates the
// renderers stroke. They are the leading pixels of the attacking pose, because that is what the
// player is being asked to respect.
// ---------------------------------------------------------------------------
const MELEE_REACH = {
  // drawFenris: nose tip is the leading pixel at local x = 33 + 2.6 = 35.6, nose centre 33, and the
  // widest half of the body is the 23 px barrel. The nose, not a wide jaw, is what makes contact.
  // The bite pose (open jaw and fangs) is authored to the same forward extent, so this stays valid.
  fenris: { contactX: 33, noseR: 2.6, bodyHalf: 23, lunge: 8 },
};
// The old reach was measured in collision units, but bosses draw themselves at e.scale, so a big
// sprite needed a big radius to seem to touch anything. Instead the art is allowed to reach into
// the hero's own body: contact happens when |gap between centres| is at most hero radius + drawn
// bite. The hero radius is the 12 the rest of the codebase uses for him (hurtHero knocks him back
// with moveEnt(P, 12, …)), not a fudge factor, so the bite lands at ~84 px centre-to-centre with
// his nose drawn out to 100 px: the muzzle visibly overlaps the elf when damage happens. Before,
// Skarnax's reach was 55 * scale + 14 = 168 px, about five tiles, which is the "bit me from four
// tiles away while clearly not touching me" report.
const HERO_BODY_R = 12;
function meleeReach(e) {
  if (!e.boss) return 38 + 14;
  const spec = MELEE_REACH[e.kind];
  if (!spec) return 55 * (e.scale || 1) + 14;
  const s = e.scale || 1;
  // Centre-to-nose-tip distance in the art, measured from the entity origin (between the paws).
  // Only the artwork beyond the body's own silhouette is added as extra reach, and it is scaled
  // with the sprite; the entity radius already covers the body itself.
  const noseTip = Math.hypot(spec.contactX, spec.noseR) + spec.noseR;
  return HERO_BODY_R + Math.max(e.r, spec.bodyHalf) * s + Math.max(0, noseTip - spec.bodyHalf * s) + spec.lunge;
}

// decorative floating motes, deterministic so no allocation per frame
const MOTES = Array.from({ length: 46 }, (_, i) => ({
  x: (i * 379) % 960, y: (i * 613) % 540, r: .8 + (i % 5) * .55,
  sp: 6 + (i % 7) * 3.5, sway: 10 + (i % 9) * 4, ph: i * .7,
}));
function drawMotes(g) {
  const z = ZONES[realmIndex(G.zone)], c = z.accent || "#cbb89a";
  const hot = !!z.lava;
  g.save();
  g.globalCompositeOperation = "lighter";
  for (const m of MOTES) {
    const t = G.t;
    const y = (((m.y - t * m.sp) % 620) + 620) % 620 - 40;
    const x = m.x + Math.sin(t * .5 + m.ph) * m.sway;
    const a = .10 + .12 * (0.5 + 0.5 * Math.sin(t * 1.6 + m.ph));
    g.fillStyle = hexA(hot ? "#ff9a3c" : c, a);
    g.beginPath(); g.arc(x, y, m.r, 0, TAU); g.fill();
  }
  g.restore();
}

function drawHeroEnt(g, time) {
  g.save();
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over"; g.filter = "none";
  g.shadowBlur = 0; g.shadowColor = "transparent";
  shadow(g, P.x, P.y, 16);
  // hero: always fully opaque (a stray globalAlpha anywhere must never be able to hide him)
  g.save(); g.globalAlpha = 1; g.translate(P.x, P.y);
  try { drawElf(g, P, time); } catch (err) {
    // visible emergency elf: the hero must never be invisible because of a draw fault
    g.globalAlpha = 1;
    g.fillStyle = "#5f9a52"; g.beginPath(); g.ellipse(0, -10, 14, 20, 0, 0, TAU); g.fill();
    g.fillStyle = "#e8d98a"; g.beginPath(); g.arc(0, -28, 10, 0, TAU); g.fill();
    // drawing continues with the fallback silhouette
  }
  // no flicker overlay while invulnerable — it read as a black flash and hid the elf
  g.restore();
}

function drawEnemyEnt(g, e, time) {
  if (!(e.r > 0)) e.r = e.boss ? 34 : 17;
  // Fully reset the canvas state every enemy is about to depend on. "lighter" or a stray
  // globalAlpha leaking in from a particle/glow pass made enemies vanish or flash, and
  // "enemies I can't see but that hit me" is exactly that failure mode.
  g.setTransform(1, 0, 0, 1, 0, 0);
  if (G.shake > 0) g.translate(G.shX || 0, G.shY || 0);
  g.translate(-G.camX, -G.camY);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
  // Only touch the canvas filter for the few enemies actually flashing from a hit. A
  // crowded fight is precisely when a hit is landing somewhere, so assigning "none" here
  // for every enemy on screen flipped the 2D filter pipeline on and off hundreds of times
  // per frame — a big part of the "stutters then jumps" feeling. If it is already unset,
  // assigning the same value is a no-op in most engines, so this is a cheap guard.
  if (g.filter !== "none") g.filter = "none";
  g.shadowBlur = 0; g.shadowColor = "transparent";
  g.save(); g.globalAlpha = 1;
  // Contact shadow, sized to the body that drawEnemyEnt is about to render: see shadowHalfWidth.
  shadow(g, e.x, e.y, shadowHalfWidth(e));
  g.save(); g.translate(e.x, e.y);
  // Hit flash history, kept because it explains the cost model, not the current code.
  // canvas.filter = "brightness(2.2)" is NOT cheap: it forces the 2D engine to allocate and run an
  // offscreen filter pass for that sprite, and it was applied once per hit enemy. In a big fight many
  // brutes are hit in the same instant, so a single frame paid dozens of full filter rasterisations at
  // once - exactly the "freeze for a few seconds right when a lot of brutes are flashing bright, then
  // jump ahead" stall. That is why the flash went to a silhouette path, and the silhouette path is
  // what is now gone too (see the block below): what remains is a dark ring stroked over the art
  // itself, which costs one ellipse stroke and cannot put a pixel anywhere the enemy is not.
  // shadow() is still drawn before the entity transform, so the floor ellipse belongs under him.
  // Fenris are the same art for pack and boss, so both are routed to drawFenris, which applies the
  // boss enlargement (e.scale) itself. drawBoss's own "fenris" branch is unreachable and was a
  // plainer, thinner wolf than the one in drawFenris, which is why Skarnax looked like a slightly
  // bigger pack wolf instead of an ancient one. The other bosses scale themselves in drawBoss.
  if (e.boss) { if (e.kind === "fenris") drawFenris(g, e, time); else drawBoss(g, e, time); }
  else if (e.kind === "bat") drawBat(g, e, time);
  else if (e.kind === "spider") drawSpider(g, e, time);
  else if (e.kind === "brutearcher") drawBruteArcher(g, e, time);
  // Fenris are the same art for pack and boss, so both are routed to drawFenris. The boss enlargement
  // (e.scale) lives inside drawFenris; the other bosses scale themselves in their own renderers.
  else if (e.kind === "fenris") drawFenris(g, e, time);
  else drawBrute(g, e, time, e.kind === "ironjaw");
  if (e.hurtT > 0) {
    // Hit feedback. What used to be here is why the owner asked for the flash to be dropped.
    //
    // The old block re-painted the enemy's own silhouette WHITE through a viewport-sized scratch
    // layer: trace a path into the layer, fill it with "source-atop", then blit the whole layer back
    // onto the visible canvas - once per struck enemy, per frame. Whatever the comments around it
    // claimed about transforms and per-frame clears, the reported symptom survived every revision:
    // shoot a wolf and a small black wolf with a white nose blinked onto the floor somewhere else.
    // That is the signature of two things at once. (1) The silhouette path was replayed under a
    // transform captured from getTransform() at an arbitrary point in the art code above, and hounds
    // apply their own boss scale internally via g.scale(), so the traced body and the blitted body
    // disagreed about where the feet were - the wolf landed offset from the wolf. (2) The muzzle is
    // painted last in drawFenris and is the only light-coloured part of it, which is precisely "a black
    // wolf shape with a white nose". It is not a stray asset or a second enemy; it is the wolf's own
    // art, committed to a second surface and put in the wrong place. Since it could not be made
    // clean, the flash is gone, as requested.
    //
    // What replaced it has no second surface at all, so there is nothing to misplace and nothing to
    // leave behind between hits. The body dips darker for the .18s of hurtT by stroking a ring over
    // the art that is already on screen. A stroke can only darken pixels the renderer itself painted:
    // there is no fill of a disc, no gradient, no blit, and no per-frame bookkeeping that another
    // enemy's state can corrupt. A hit still reads through five independent channels - this dark
    // ring, the floating damage number, the blood burst, the knockback, and the HP pip appearing
    // above him - so dropping the white flash costs no information.
    //
    // If a bright flash is ever wanted again, do not reuse this file's live transform. Trace the
    // body into an offscreen canvas sized to that one enemy's own box, at an identity transform the
    // art code is handed explicitly, and blit it to (e.x, e.y) in world space under the same
    // explicit transform, with the layer cleared at identity immediately before tracing.
    const ha = clamp(e.hurtT / .18, 0, 1);
    if (ha > 0) {
      // The art is drawn above the origin (feet at y = 0, head near y = -2e.r), so the ring is
      // centred on the body, not on the feet: centred at 0 it would ring the empty floor under him
      // and miss him, which is a quieter version of the bug being fixed. Half-widths here are the
      // drawn extents from SHADOW_HALF, the same source shadow() uses, so the ring follows the
      // silhouette rather than the collision circle - and for a quadruped those differ a lot, since
      // a hound is roughly 80 px long on a 17 px radius. Bosses carry e.scale in the renderer, and
      // this ring is drawn in the same transform as the art, so multiplying the footprint by that
      // scale is the whole story.
      const s = e.boss ? (e.scale || 1) : 1;
      const hw = Math.max(10, shadowHalfWidth(e));
      const hh = Math.max(9, e.r * 1.15 * s);
      g.save();
      // "multiply", not "source-atop": source-atop composites against whatever the destination
      // already holds, and on the visible canvas the destination is the baked floor, the walls and
      // every enemy drawn before this one. multiply leaves those untouched and changes only the
      // pixels the body painted, so the wolf darkens where the wolf is.
      g.globalCompositeOperation = "multiply";
      // .55 at peak is a firm but not blackout dip, and ha ramps it out over hurtT.
      g.globalAlpha = ha * .55;
      g.strokeStyle = "#4a3b36";
      // Thick outline rather than a filled oval: the interior keeps its own shading, so the enemy
      // reads as having taken a hit instead of having a stamp put on him.
      g.lineWidth = Math.max(3.5, hw * .22);
      g.beginPath(); g.ellipse(0, -hh * .92, hw * .82, hh * .9, 0, 0, TAU); g.stroke();
      g.restore();
    }
  }
  g.restore();          // entity transform, paired with translate(e.x, e.y)
  g.restore();          // cull/bounds save

  // no flicker overlay while invulnerable (see drawHeroEnt)
  g.globalAlpha = 1;
  // hp pip for non-boss
  if (!e.boss && e.hp < e.maxHp) {
    const w = 30, hpw = w * clamp(e.hp / e.maxHp, 0, 1);
    g.fillStyle = "rgba(0,0,0,.55)"; g.fillRect(e.x - w / 2, e.y - e.r - 38, w, 5);
    g.fillStyle = "#8fce3a"; g.fillRect(e.x - w / 2, e.y - e.r - 38, hpw, 5);
  }
  if (e.boss) $("boss-fill").style.width = (clamp(e.hp / e.maxHp, 0, 1) * 100) + "%";
}

function drawPickup(g, pk, time) {
  const bob = Math.sin(time * .005 + pk.bob) * 3;
  g.save(); g.translate(pk.x, pk.y + bob);
  if (pk.kind === "gold") {
    strokeCircle(g, 0, -6, 8, "#e8b62a", 2); strokeCircle(g, 0, -6, 4.5, "#ffd76b", 1.4);
    g.fillStyle = "#7a5210"; g.font = "bold 8px sans-serif"; g.textAlign = "center"; g.fillText("S", 0, -3.4);
  } else if (pk.kind === "arrows") {
    g.restore();
    drawArrowPickup(g, pk.x, pk.y + bob, time);
    return;
  } else {
    // glowing drawn heart (crisp at any zoom, unlike an emoji)
    g.shadowColor = "rgba(255,70,70,.9)"; g.shadowBlur = 10;
    g.fillStyle = "#d92c38"; g.strokeStyle = "#3a070c"; g.lineWidth = 1.6;
    g.beginPath();
    g.moveTo(0, 2);
    g.bezierCurveTo(-9, -5, -6.5, -13, -1.5, -11.5);
    g.bezierCurveTo(-.4, -11.2, -.4, -10.6, 0, -10.2);
    g.bezierCurveTo(.4, -10.6, .4, -11.2, 1.5, -11.5);
    g.bezierCurveTo(6.5, -13, 9, -5, 0, 2);
    g.closePath(); g.fill(); g.stroke();
    g.shadowBlur = 0;
    g.fillStyle = "rgba(255,255,255,.55)";
    g.beginPath(); g.ellipse(-3, -8.6, 1.8, 1.1, -.5, 0, TAU); g.fill();
  }
  g.restore();
}

// ---------- HUD ----------
const HUDc = {};
function updateHud() {
  let hf = HUDc.hf;
  if (!hf) {
    HUDc.hf = hf = $("hp-fill"); HUDc.ht = $("hp-text"); HUDc.xf = $("xp-fill");
    HUDc.xt = $("xp-text"); HUDc.gt = $("gold-text"); HUDc.kt = $("kills-text");
    HUDc.dt = $("dmg-text"); HUDc.al = $("arrow-label"); HUDc.wl = $("wave-label");
    HUDc.zl = $("zone-label");
  }
  const ht = HUDc.ht;
  const hpPct = P.hp >= P.maxHp ? "100%" : (clamp(P.hp / P.maxHp, 0, 1) * 100).toFixed(1) + "%";
  if (hf.__v !== hpPct) { hf.style.width = hpPct; hf.__v = hpPct; }
  const hpTxt = Math.max(0, Math.ceil(P.hp)) + "/" + P.maxHp;
  if (ht.__v !== hpTxt) { ht.textContent = hpTxt; ht.__v = hpTxt; }
  const xf = HUDc.xf, xt = HUDc.xt;
  const xpPct = (clamp(P.xp / P.xpNext, 0, 1) * 100).toFixed(1) + "%";
  if (xf.__v !== xpPct) { xf.style.width = xpPct; xf.__v = xpPct; }
  const lv = "Lv " + P.lvl;
  if (xt.__v !== lv) { xt.textContent = lv; xt.__v = lv; }
  const gt = HUDc.gt;
  if (gt.__v !== G.gold) { gt.textContent = G.gold; gt.__v = G.gold; }
  const kt = HUDc.kt;
  if (kt.__v !== G.kills) { kt.textContent = G.kills; kt.__v = G.kills; }
  const dt_ = HUDc.dt, dmg = P.__dmgCached !== undefined ? P.__dmgCached : (P.__dmgCached = Math.round(P.dmg * heroAtkMul()));
  if (dt_.__v !== dmg) { dt_.textContent = dmg; dt_.__v = dmg; }
  const al = HUDc.al;
  if (al) {
    if (al.__v !== P.arrows) { al.textContent = "🏹 Arrows: " + P.arrows; al.__v = P.arrows; }
    const low = P.arrows <= 5;
    if (al.__low !== low) { al.classList.toggle("low-ammo", low); al.__low = low; }
  }
  const wl = HUDc.wl;
  const wt = G.boss ? "⚔ Boss battle!" : G.wave === "fight" ? "Foes: " + G.enemies.length : G.wave === "preboss" ? "Brace yourself…" : "Path clear — keep moving!";
  if (wl.__v !== wt) { wl.textContent = wt; wl.__v = wt; }
  // Realm number is the position inside the circuit, so it reads 1..6 forever; the loop is only
  // shown once the player has gone past the first six realms.
  const lp = loopNumber(G.zone);
  const zl = HUDc.zl, zt = (realmIndex(G.zone) + 1) + "/" + REALM_COUNT + " · " + ZONES[realmIndex(G.zone)].name + (lp > 1 ? " · Loop " + lp : "");
  if (zl.__v !== zt) { zl.textContent = zt; zl.__v = zt; }
}

// ---------- screens ----------
// Pending boon picks. Multiple levels from one kill grant one pick each; they are drained
// one panel at a time so nothing is silently swallowed.
// BOON_QUEUE is declared with the rest of the engine state in engine.js (so resetHero can clear
// it on a new run) and is a global lexical binding shared with this file.
function queueLevelUps(n) {
  BOON_QUEUE += Math.max(0, n | 0);
  if (G.state === "play" || G.state === "levelup") openLevelUp();
}
// Keys 1/2/3 choose the matching boon; also handled for the numpad.
function boonKeySelect(idx) {
  const list = $("boon-list");
  if (!list) return false;
  const btns = list.querySelectorAll("button.boon");
  if (idx < 0 || idx >= btns.length) return false;
  btns[idx].click();
  return true;
}
function openLevelUp() {
  if (BOON_QUEUE <= 0) { BOON_QUEUE = 0; if (G.state === "levelup") { showScreen(null); G.state = "play"; } return; }
  G.state = "levelup";
  const list = $("boon-list"); list.innerHTML = "";
  const remaining = BOON_QUEUE;
  const opts = []; const pool = PERKS.slice();
  while (opts.length < 3 && pool.length) opts.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  for (let i = 0; i < opts.length; i++) {
    const b = opts[i];
    const el = document.createElement("button"); el.type = "button"; el.className = "boon";
    // the number badge is what the 1/2/3 keys map to, so it must match the button order
    el.innerHTML = `<span class="keyhint">${i + 1}</span><span class="ico">${b.ico}</span><span class="nm">${b.nm}</span><div class="ds">${b.ds}</div>`;
    el.addEventListener("click", () => {
      if (G.state !== "levelup") return;      // ignore a stale click after the panel closed
      b.apply(P); AudioSys.buy();
      BOON_QUEUE = Math.max(0, BOON_QUEUE - 1);
      P.__dmgCached = undefined; updateHud();
      if (BOON_QUEUE > 0) openLevelUp();        // next level, next boon
      else { showScreen(null); G.state = "play"; }
    });
    list.appendChild(el);
  }
  const sub = document.querySelector("#screen-levelup .panel-sub");
  if (sub) sub.textContent = remaining > 1
    ? `The wisdom of the Sylvaniai sharpens your aim. Choose a boon — ${remaining} pick${remaining > 1 ? "s" : ""} remaining.`
    : "The wisdom of the Sylvaniai sharpens your aim. Choose a boon:";
  showScreen("levelup");
}

function continueJourney() {
  clearPendingShots();          // no stray enemy arrows from the old arena
  G.zone++; G.enemies = []; G.projectiles = []; G.pickups = []; G.boss = null; G.escorts = [];
  G.particles.length = 0; G.texts.length = 0;   // old-arena motes are meaningless and would pile up
  bossWrap.classList.add("hidden");
  startZone(); updateHud();
  showScreen(null); G.state = "play";
}

/* The quest panel at the end of a circuit of six realms. It used to be a terminal "you win"
   screen wired to Play Again; now it is a checkpoint. The same panel appears every time all six
   realms fall, its button reads Continue and the run goes on into the next loop, so the only end
   is dying. Each successive circuit gets a more precious metal for the panel and button: bronze
   (as shipped), silver, gold, and gold from then on. */
function questVictory() {
  // "winstate" (not "win") so the Enter shortcut can tell a checkpoint apart from a real game
  // over, and so nothing that treats "over" as "this run is finished" counts a checkpoint as one.
  G.state = "winstate";
  hud.classList.add("hidden"); bossWrap.classList.add("hidden");
  const clearedLoops = loopNumber(G.zone);          // circuits completed, counting this one
  const tier = VICTORY_TIERS[victoryTierIndex(clearedLoops)];
  const mins = Math.floor(G.elapsed / 60), secs = Math.floor(G.elapsed % 60);
  const winPanel = document.querySelector("#screen-win .panel");
  if (winPanel) winPanel.classList.remove("victory-bronze", "victory-silver", "victory-gold");
  if (winPanel) winPanel.classList.add(tier.cls);
  const h = $("win-title");
  if (h) {
    h.textContent = clearedLoops <= 1
      ? "🌋 The Quest is Done!"
      : "🌋 The Quest is Done — Again!";
  }
  const sub = $("win-sub");
  if (sub) {
    sub.textContent = clearedLoops <= 1
      ? "The Hollow Crown is broken, and the elven realms begin their long twilight."
      : `${tier.name} proof of it: ${clearedLoops} circuits of the six realms cleared. The realms reshape themselves behind Shasan, and the shadow re-forms ahead — braver, bolder, and thicker than before.`;
  }
  $("win-stats").innerHTML =
    `<span class="chip">🪙 Gold: ${G.gold}</span>` +
    `<span class="chip">💀 Slain: ${G.kills}</span>` +
    `<span class="chip">⭐ Level ${P.lvl}</span>` +
    `<span class="chip">🗺️ ${REALM_COUNT}/${REALM_COUNT} realms · Circuit ${clearedLoops}</span>` +
    `<span class="chip">⏱️ ${mins}m ${secs}s</span>`;
  const btn = $("btn-again");
  if (btn) btn.textContent = "➤ Continue — Circuit " + (clearedLoops + 1);
  AudioSys.win();
  showScreen("win");
}

// Continue from the quest panel: straight on into the next loop at realm 1. Everything the player
// earned carries over — level, boons, gold, quiver — only the crowd and the bosses get tougher.
function continueQuest() {
  clearPendingShots();
  if (typeof AI_GEN === "number") AI_GEN++;   // same bookkeeping startGame() does for pending shots
  const winPanel = document.querySelector("#screen-win .panel");
  if (winPanel) winPanel.classList.remove("victory-bronze", "victory-silver", "victory-gold");
  G.zone++;                       // crosses into the next circuit; realmIndex() wraps to realm 1
  G.enemies = []; G.projectiles = []; G.pickups = []; G.boss = null; G.escorts = [];
  G.particles.length = 0; G.texts.length = 0;
  bossWrap.classList.add("hidden");
  // questVictory() hid the HUD when the panel came up, and this path - unlike startGame() in
  // engine.js, which does hud.classList.remove("hidden") - never brought it back. The player spent
  // the whole next circuit with no portrait, health bar, level, realm name or arrow count.
  hud.classList.remove("hidden");
  startZone(); updateHud();
  showScreen(null); G.state = "play";
}

function gameOver(win) {
  G.state = "over";
  hud.classList.add("hidden"); bossWrap.classList.add("hidden");
  const mins = Math.floor(G.elapsed / 60), secs = Math.floor(G.elapsed % 60);
  const lp = loopNumber(G.zone);
  const stats = `<span class="chip">🪙 Gold: ${G.gold}</span><span class="chip">💀 Slain: ${G.kills}</span><span class="chip">⭐ Level ${P.lvl}</span><span class="chip">🗺️ Realm ${realmIndex(G.zone) + 1}/${REALM_COUNT}${lp > 1 ? " · Loop " + lp : ""}</span><span class="chip">⏱️ ${mins}m ${secs}s</span>`;
  if (win) {
    AudioSys.win();
    $("win-stats").innerHTML = stats;
    showScreen("win");
  } else {
    AudioSys.lose();
    $("over-sub").textContent = pick([
      "The last arrow has flown… but even the smallest person can change the future.",
      "The brutes drag him off for a good long laugh. Rest, elf.",
      "One does not simply walk into Vaighruz without a few more arrows.",
      "Hardbread will have to wait — a hero's work is never done.",
    ]);
    $("over-stats").innerHTML = stats;
    showScreen("over");
  }
}

// ---------- buttons ----------
$("btn-start").addEventListener("click", startGame);
$("btn-retry").addEventListener("click", startGame);
// The victory button continues the endless run rather than restarting it. It is reached only from
// the quest panel, which questVictory() raises, so Continue is always the right verb here.
$("btn-again").addEventListener("click", continueQuest);
$("btn-resume").addEventListener("click", togglePause);


// idle title render needs a zone
updateHud();
requestAnimationFrame(frame);