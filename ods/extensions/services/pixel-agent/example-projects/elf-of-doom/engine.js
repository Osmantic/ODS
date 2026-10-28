// Elf of Doom — engine: input, state, combat (keyboard-only archer)
"use strict";

const CV = document.getElementById("game"), CX = CV.getContext("2d");
// The boon queue lives on the global object, not as a lexical binding. Each file here is a
// separate classic script, and top-level let/const in one is NOT visible to the others: declaring
// it with let in ui.js and touching it from resetHero() threw "BOON_QUEUE is not defined", which
// aborted resetHero halfway - before x/y were assigned - so the hero kept whatever coordinates he
// last had and the halo appeared stuck to the lower right of him. window.* is shared by all of them.
window.BOON_QUEUE = 0;
const W = 960, H = 540;
const DUNGEON = { w: 1500, h: 1200, playTop: 68 };
let D = null, DW = 1500, DH = 1200;

// ---------- dom ----------
const $ = id => document.getElementById(id);
const screens = { title: $("screen-title"), levelup: $("screen-levelup"), over: $("screen-over"), win: $("screen-win"), pause: $("screen-pause") };
const hud = $("hud"), bossWrap = $("boss-wrap");
function showScreen(name) {
  for (const k in screens) screens[k].classList.toggle("hidden", k !== name);
  if (!name) for (const k in screens) screens[k].classList.add("hidden");
}

// ---------- input (keyboard only: arrows + J/Space) ----------
const keys = {};
let slashHeld = false;
addEventListener("keydown", e => {
  const k = e.key.toLowerCase();
  if (["arrowup", "arrowdown", "arrowleft", "arrowright", " ", "shift"].includes(k === " " ? " " : k)) e.preventDefault();
  if (e.repeat) return;
  if (keys[k]) return; keys[k] = true;
  if (k === "j" || k === " ") slashHeld = true;
  if (k === "escape" || k === "p") togglePause();
  // M mutes from anywhere, so the title-screen control list is accurate
  if (k === "m") { AudioSys.muted = !AudioSys.muted; const mb = $("mute-btn"); if (mb) mb.textContent = AudioSys.muted ? "🔇" : "🔊"; }
  // 1/2/3 pick a boon on the level-up panel (main row and numpad)
  // G.state is "over" while any modal panel is up, so the boon shortcut has to be gated on the
  // level-up screen itself. Without this, pressing 1/2/3 on the quest-victory panel ran the boon
  // picker against the old boon list, clicked a stale button, and started a fresh game mid-panel.
  if (G.state === "levelup" && document.getElementById("screen-levelup") &&
      !document.getElementById("screen-levelup").classList.contains("hidden") &&
      (k === "1" || k === "&" || k === "2" || k === "@" || k === "3" || k === "#")) {
    const idx = (k === "1" || k === "&") ? 0 : (k === "2" || k === "@") ? 1 : 2;
    if (typeof boonKeySelect === "function") boonKeySelect(idx);
    return;
  }
  // Enter on the quest panel continues the endless run (matching its Continue button) rather than
  // throwing away a deep run to restart. Every other end state still starts a fresh game.
  if (k === "enter") {
    if (G.state === "winstate") { if (typeof continueQuest === "function") continueQuest(); return; }
    if (G.state === "title" || G.state === "over" || G.state === "win") startGame();
  }
});
addEventListener("keyup", e => { const k = e.key.toLowerCase(); keys[k] = false; if (k === "j" || k === " ") slashHeld = false; });
// Losing focus must STOP the game, not just drop the keys. Previously only the key state was
// cleared, so clicking outside the tab left the simulation running: brutes kept closing in and
// shooting an idle hero, who died while the player was on another window.
function pauseForLostFocus() {
  for (const k in keys) keys[k] = false; slashHeld = false;
  if (G.state === "play") { G.state = "pause"; if (typeof showScreen === "function") showScreen("pause"); }
}
addEventListener("blur", pauseForLostFocus);
addEventListener("visibilitychange", () => { if (document.hidden) pauseForLostFocus(); });

$("mute-btn").addEventListener("click", () => { AudioSys.muted = !AudioSys.muted; $("mute-btn").textContent = AudioSys.muted ? "🔇" : "🔊"; });

// ---------- game state ----------
const PERKS = [
  { ico: "🏹", nm: "Elven Fletching", ds: "+5 arrow damage", apply: s => s.dmg += 5 },
  { ico: "💨", nm: "Grace of the Sylvaniai", ds: "+12% move speed", apply: s => s.spd *= 1.12 },
  { ico: "🎯", nm: "Keen Eye", ds: "+25% draw speed", apply: s => { s.atkDur *= .75; s.atkCd *= .75; } },
  { ico: "❤️", nm: "Sylvanic Hardbread", ds: "+25 max HP, heal full", apply: s => { s.maxHp += 25; s.hp = s.maxHp; } },
  { ico: "🩸", nm: "Hunter's Vigor", ds: "Heal 3 HP per kill", apply: s => s.lifesteal += 3 },
  // "Hunter's Blessing" replaced "Swift Draw" (+20% fire rate), which stacked with Keen Eye into
  // a generic rate-up nobody had to think about. This one is a trade: you give up the HP, damage
  // and speed options for one pick, and every quiver you pick up from then on is worth double.
  // ammoSec doubles per pick (x2, x4, x8...), so 6-arrow drops become 12 then 24 then 48. It is a
  // multiplier on *drops*, not on the quiver: it never refills anything by itself, so it only pays
  // out if you actually walk over to the supplies, and it does nothing at all in a wave with no drops.
  { ico: "🦌", nm: "Hunter's Blessing", ds: "Quivers found are worth double — stacks ×2, ×4, ×8", apply: s => { s.ammoSec = Math.max(1, s.ammoSec | 0) * 2; } },
  { ico: "🏹", nm: "Double Shot", ds: "Loose 2 arrows per shot", apply: s => s.multi += 1 },
];

const G = { state: "title", t: 0, zone: 0, wave: "clear", waveTimer: 0, enemies: [], projectiles: [], particles: [], texts: [], pickups: [], props: [], boss: null, shake: 0, shX: 0, shY: 0, freezeT: 0, announce: null, kills: 0, gold: 0, elapsed: 0, camX: 0, camY: 0 };
const P = {}; // hero
function resetHero() {
  Object.assign(P, {
    x: DW / 2, y: DH / 2, vx: 0, vy: 0, face: 1, moving: false,
    hp: 50, maxHp: 50, lvl: 1, xp: 0, xpNext: Math.round(XP_BASE * XP_REQUIREMENT_SCALE), dmg: 22, spd: 205,
    drawT: 0, atkT: 0, atkDur: 0.26, atkCd: 0.32, atkCdT: 0, multi: 1,
    invuln: 0, lifesteal: 0,
    // Hunter's Blessing multiplier on arrow drops. 1 = no blessing; each pick doubles it. Reset
    // here so a new run never inherits the last run's blessing (same reason lifesteal is reset).
    ammoSec: 1,
    arrows: 24, maxArrows: 24, hitFx: 0, shootAng: 0, webT: 0,
  });
  BOON_QUEUE = 0;   // a new run must never inherit picks left over from the last one (window global)
  // Re-anchor the hero and camera explicitly. Object.assign above sets x/y, but a halo that is
  // centred on him only looks right if those are the arena centre and the camera actually agrees
  // with them, so set both here rather than relying on the caller's follow-up lerp.
  P.x = DW / 2; P.y = DH / 2;
  G.camX = clamp(P.x - W / 2, 0, Math.max(0, DW - W));
  G.camY = clamp(P.y - H / 2, 0, Math.max(0, DH - H));
}
resetHero();

function startGame() {
  AudioSys.ensure();
  if (typeof clearPendingShots === "function") clearPendingShots();   // no arrows from the previous run
  if (typeof AI_GEN === "number") AI_GEN++;
  G.zone = 0; G.kills = 0; G.gold = 0; G.elapsed = 0; G.boss = null;
  G.enemies = [];
  G.projectiles = [];
  G.particles = [];
  G.texts = [];
  G.pickups = [];
  G.boss = null;
  G.shockwaves = [];
  resetHero(); startZone();
  G.state = "play"; showScreen(null); hud.classList.remove("hidden"); bossWrap.classList.add("hidden");
}
function startZone() {
  // cancel any enemy shot still waiting to be loosed: it must never appear in the new arena
  if (typeof clearPendingShots === "function") clearPendingShots();
  if (typeof AI_GEN === "number") AI_GEN++;
  // Build and validate the whole arena in a local, and publish it to the global `D` in one
  // assignment only once it is complete. `D` used to be published straight from genArena(), which
  // meant anything that ran before the backdrop was baked - or anything that survived a failure in
  // the generation path - saw a half-populated arena. A missing or non-callable method then threw
  // "D.nearestFloor is not a function" from steerAround on the first brute that wedged against a
  // pillar, so a single bad realm build became per-frame update failures for the rest of the run.
  // Endless loop: the arena layout is chosen by realmIndex(), so realm 7 is a fresh realm-1 hall
  // rather than an out-of-range ZONES lookup. genArena's obstacle seed stays on the raw zone, so
  // each circuit still gets its own arrangement of pillars.
  const lay = realmIndex(G.zone);
  const fresh = genArena(lay);
  fresh.baked = bakeArena(fresh, lay, Math.floor(Math.random() * 1e9));
  if (!(fresh.RW > 0 && fresh.RH > 0 && fresh.wpx > 0 && fresh.hpx > 0) ||
      !isFinite(fresh.wpx) || !isFinite(fresh.hpx) ||
      !fresh.spawn || !isFinite(fresh.spawn.x) || !isFinite(fresh.spawn.y) ||
      ARENA_METHODS.some(k => typeof fresh[k] !== "function")) {
    throw new Error("arena for realm " + G.zone + " failed its readiness check");
  }
  D = fresh;                                  // single publish: no partially built arena is ever live
  DW = D.wpx; DH = D.hpx;
  DUNGEON.w = DW; DUNGEON.h = DH; DUNGEON.playTop = TILE * 2;
  P.x = D.spawn.x; P.y = D.spawn.y;
  G.camX = clamp(P.x - W / 2, 0, Math.max(0, DW - W));
  G.camY = clamp(P.y - H / 2, 0, Math.max(0, DH - H));
  G.enemies = [];
  G.projectiles = [];
  G.particles = [];
  G.texts = [];
  G.pickups = [];
  G.boss = null;
  G.bossReinT = undefined;   // cleared with the boss so a stale timer can never fire a wave without one
  G.shockwaves = [];
  G.escorts = [];
  G.props = D.decor;
  G.wave = "fight"; G.waveTimer = 0; G.waveNum = 0; G.supplyT = 20;
  G.pickDead = true;
  // Arrows carry across zones. This was an unconditional `P.arrows = 24`, so entering a new
  // realm threw away every arrow the player had saved up or picked up, which read as a
  // reset/loss of progress. Refill to the quiver size instead of hard-coding it down.
  const quiver = Math.max(24, P.maxArrows || 0);
  P.maxArrows = quiver;
  P.arrows = Math.max(P.arrows || 0, quiver);
  // scatter health and arrows around the arena
  for (let i = 0; i < 4; i++) {
    const s = D.farSpot(P.x, P.y, 210, 16);
    G.pickups.push({ x: s.x, y: s.y, kind: "heart", val: 30, t: Infinity, bob: rand(0, 6) });
  }
  for (let i = 0; i < 4; i++) {
    const s = D.farSpot(P.x, P.y, 210, 16);
    G.pickups.push({ x: s.x, y: s.y, kind: "arrows", val: arrowDrop(6), t: Infinity, bob: rand(0, 6) });
  }
  spawnWave();
// realmIndex() again: past the first circuit G.zone is >= 6 and this lookup would be undefined.
announce(ZONES[realmIndex(G.zone)].name, "Gabriel draws his bow…");
}
function spawnWave() {
  const z = ZONES[realmIndex(G.zone)];
  G.waveNum = (G.waveNum || 0) + 1;
  // +30% roster size (ROSTER_SCALE). The old 22 cap was set against the *pre-scale* curve, where
  // it only bound at about 14.7 raw; after scaling it clamped nearly every mid/late wave and
  // silently cancelled the increase (zone 5 wave 4 stayed at 22). Raised to 30 so the increase
  // lands, while keeping a hard ceiling: crowd rasterisation and AI are what caused the stalls.
  // The loop multiplier is applied *outside* that ceiling on purpose. In loop 1 it is exactly 1,
  // so the shipped curve is untouched; in deeper loops the crowd keeps growing past 30 instead
  // of the +50% being silently eaten by the clamp, which is the whole point of the endless mode.
  const n = Math.min(30, (6 + G.zone + G.waveNum + randi(0, 2)) * 1.5 * ROSTER_SCALE | 0) * loopEnemyMul(loopNumber(G.zone)) | 0;
  for (let i = 0; i < n; i++) spawnEnemy(pick(z.enemies));
  if (G.waveNum > 1) announce("Wave " + G.waveNum, "More brutes pour into the hall");
  // replenish supplies each wave. Each call resolves independently against candidates spread
  // over the hall, so these four land apart from one another rather than in one grab.
  for (let i = 0; i < 2; i++) {
    // one anchor shared by all four drops: each then keeps minDist from the hero AND TILE*3 from
    // every earlier pick, so a wave's supplies land apart instead of in one grab
    const s = D ? D.farSpot(P.x, P.y, 200, 16) : { x: P.x + rand(-320, 320), y: P.y + rand(-260, 260) };
    G.pickups.push({ x: s.x, y: s.y, kind: "heart", val: 30, t: Infinity, bob: rand(0, 6) });
  }
  for (let i = 0; i < 2; i++) {
    const s = D ? D.farSpot(P.x, P.y, 200, 16) : { x: P.x - rand(140, 340), y: P.y - rand(120, 280) };
    G.pickups.push({ x: s.x, y: s.y, kind: "arrows", val: arrowDrop(6), t: Infinity, bob: rand(0, 6) });
  }
  updateHud();
}
// A timed boss-fight reinforcement: a real wave (same size, same supply drop), but it does NOT
// advance G.waveNum or reuse the wave banner, so wave pacing and the pre-boss progression stay
// untouched. Capped against a smaller ceiling than a normal wave because this lands on top of
// the boss and its escort rather than replacing them - the crowd is what caused the frame stalls.
function spawnReinforceWave(isFinal) {
  const z = ZONES[realmIndex(G.zone)];
  // Same treatment as a normal wave: loop scaling lands outside the ceiling, and the boss clock
  // itself is never accelerated past realm 6 — the reinforcement just arrives in bigger packs.
  const n = Math.min(14, (6 + G.zone + (G.waveNum || 1) + randi(0, 2)) * 1.5 * ROSTER_SCALE | 0) * loopEnemyMul(loopNumber(G.zone)) | 0;
  for (let i = 0; i < n; i++) spawnEnemy(pick(z.enemies));
  announce(isFinal ? "The Hollow Crown calls for aid!" : "Reinforcements!", "More brutes pour into the hall");
  G.shake = Math.max(G.shake, 5);
  updateHud();
}
function spawnEnemy(kind) {
  const st = ENEMY_STATS[kind];
  const spot = D ? D.farSpot(P.x, P.y, 300, st.r + 4) : { x: rand(80, DW - 80), y: rand(80, DH - 80) };
  const mult = 1 + G.zone * 0.12;
  // wolf hounds are 3x tankier than their base stat line
  const hpScale = (kind === "fenris" ? WOLF_HP_SCALE : 1);
  G.enemies.push({
    kind, x: spot.x, y: spot.y, hp: st.hp * mult * hpScale, maxHp: st.hp * mult * hpScale, spd: st.spd * ENEMY_SPEED_SCALE * (kind === "fenris" ? WOLF_SPD_SCALE : 1), dmg: st.dmg * (1 + G.zone * .06),
    r: st.r,
    // Collision radius stays at the art's torso, but separation uses a stance radius so a hound
    // holds off at roughly nose length instead of pressing its muzzle into every packmate. A wolf
    // is drawn ~71 px long before zoom against a 17 px collision radius, so with r alone several
    // of them can overlap into one mass that all bite from the same pixel. Bosses are excluded in
    // enemySeparation (their art already dwarfs the pack), so this is not inflating him further.
    sepR: st.r + (kind === "fenris" ? 12 : kind === "bat" ? 3 : 6),
    face: 1, faceHold: 0, atkT: 0, atkDur: .5, atkCdT: rand(.3, 1.2), hurtT: 0, wob: rand(0, 9),
    aiT: rand(1, 3), dashT: 0, dashDx: 0, dashDy: 0, spitT: rand(2, 4), isBoss: false,
  });
  return G.enemies[G.enemies.length - 1];
}
function spawnBoss() {
  const z = ZONES[realmIndex(G.zone)], bs = BOSS_STATS[z.boss.kind];
  G.wave = "boss";
  const spot = D ? D.farSpot(P.x, P.y, 320, bs.r + 6) : { x: DW - 240, y: DH / 2 };
  // +50% boss HP per completed loop. The loop term replaces the old per-zone term for the boss:
  // realm 6 was already the shipped difficulty peak, and multiplying a growing zone term by a
  // growing loop term would compound two curves at once. One loop factor keeps each circuit
  // exactly 1.5x the last.
  const bossHp = bs.hp * 2 * BOSS_HP_SCALE * (z.boss.kind === "fenris" ? WOLF_HP_SCALE : 1) * loopBossHpMul(loopNumber(G.zone));
  const e = {
    kind: z.boss.kind, boss: true, name: z.boss.name,
    x: spot.x, y: spot.y, hp: bossHp, maxHp: bossHp,
    // Bosses were scaling their minions but not themselves: ENEMY_SPEED_SCALE doubled the wave
    // while a boss still walked at its raw BOSS_STATS.spd, so Skarnax crawled next to pack hounds and
    // the Hollow Crown trailed the brutes summoned around him. Same dial, so a boss keeps its
    // intended position relative to the crowd instead of becoming the slowest thing on screen.
    spd: bs.spd * ENEMY_SPEED_SCALE * (z.boss.kind === "fenris" ? WOLF_SPD_SCALE : 1), dmg: bs.dmg * (1 + G.zone * .05),
    r: bs.r, sepR: bs.r + 14, face: -1, faceHold: 0, atkT: 0, atkDur: .6, atkCdT: 1, hurtT: 0, wob: 2,
    aiT: 2, dashT: 0, dashDx: 0, dashDy: 0, spitT: 2, phase: 1, scale: bs.scale,
  };
  G.enemies.push(e); G.boss = e;
  // Boss fights must never stall into a quiet duel: a fresh wave arrives on a timer so pressure
  // is continuous. The final boss halves the interval. Reset here so each boss owns its own clock.
  // From loop 2 on, every boss gets realm 6's aggressive cadence (see bossReinIntervalS). That
  // interval is the floor: deeper loops add bulk and pack size, never a shorter clock.
  G.bossReinT = bossReinIntervalS(z.boss.kind, loopNumber(G.zone));
  // escort of brutes storms in with the boss so it can't be kited from range;
  // staggered with a spark flare as each one arrives
  const escort = Math.min(12, 6 + G.zone * 2) * loopEnemyMul(loopNumber(G.zone)) | 0;
  G.escorts = [];
  for (let i = 0; i < escort; i++) {
    const e = spawnEnemy(pick(ZONES[realmIndex(G.zone)].enemies));
    if (e) {
      const when = .25 + i * .14;
      G.escorts.push({ e, t: when, life: when });
    }
  }
  G.shake = Math.max(G.shake, 8);
  // Boss loot, spread over the hall. These used freeSpot(e.x, e.y, 200, 16) three times with the
  // same arguments, which is a ring search from one fixed centre in one fixed order - all three
  // resolved to the same tile, so Skarnax's hearts and quiver landed in a single heap. farSpot
  // resolves each call against candidates scattered across the arena, and since it also keeps
  // clear of pickups already lying on the floor, three drops in the same instant cannot stack.
  for (let i = 0; i < 2; i++) {
    const s = D ? D.farSpot(e.x, e.y, 220, 16) : { x: e.x + rand(-260, 260), y: e.y + rand(-220, 220) };
    G.pickups.push({ x: s.x, y: s.y, kind: "heart", val: 30, t: Infinity, bob: rand(0, 6) });
  }
  for (let i = 0; i < 1; i++) {
    const s = D ? D.farSpot(e.x, e.y, 220, 16) : { x: e.x - rand(140, 300), y: e.y - rand(120, 260) };
    G.pickups.push({ x: s.x, y: s.y, kind: "arrows", val: arrowDrop(10), t: Infinity, bob: rand(0, 6) });
  }
  bossWrap.classList.remove("hidden"); $("boss-name").textContent = e.name;
  AudioSys.bossRoar();
  announce(e.name, "A great shadow falls across the land!");
}

// ---------- collision ----------
function moveEnt(o, r, dx, dy, speed, dt) {
  let blocked = false;
  if (!D) {
    o.x = clamp(o.x + dx * speed * dt, 80, DW - 80);
    o.y = clamp(o.y + dy * speed * dt, 80, DH - 80);
    o.stuck = false;
    return false;
  }
  const nx = o.x + dx * speed * dt;
  if (!D.circleBlocked(nx, o.y, r)) o.x = nx; else blocked = true;
  const ny = o.y + dy * speed * dt;
  if (!D.circleBlocked(o.x, ny, r)) o.y = ny; else blocked = true;
  o.stuck = blocked;
  return blocked;
}
function nearestFloor(x, y, r) {
  if (!D || !D.circleBlocked(x, y, r)) return { x, y };
  for (let rad = TILE; rad <= TILE * 6; rad += TILE) {
    for (let a = 0; a < TAU; a += Math.PI / 8) {
      const px = x + Math.cos(a) * rad, py = y + Math.sin(a) * rad;
      if (!D.circleBlocked(px, py, r)) return { x: px, y: py };
    }
  }
  return { x: D.spawn.x, y: D.spawn.y };
}

function announce(title, sub) { G.announce = { title, sub, t: 2.8 }; }

// ---------- particles / texts ----------
function burst(x, y, n, color, spd, life, size) {
  for (let i = 0; i < n; i++) {
    const a = rand(0, TAU), v = rand(.3, 1) * (spd || 140);
    G.particles.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 40, t: life || .5, life: life || .5, color, size: size || 4 });
  }
}
function popText(x, y, str, color, big) { G.texts.push({ x, y, str, color, t: .9, big }); }
// Scatter for loot dropped by a kill. It used to be x + rand(-10, 10) on both axes, a 20px jitter,
// so a cluster of brutes dying together left a heap you emptied in one stride. Spread each drop to a
// distinct spot nearby; a pickup is only ever worth walking to if walking to it is a decision.
function dropPickup(x, y, kind, val) {
  // Loot belongs where the kill happened. This scatter used to be rand(46, 130) -- "several steps
  // away" -- so a coin could land further from the brute that dropped it than from the next room. It
  // now settles just outside the corpse's own footprint, which keeps a cluster of simultaneous
  // deaths from resolving to one pixel without sending anything wandering.
  const ang = rand(0, Math.PI * 2), rad = rand(8, 26);
  let px = x + Math.cos(ang) * rad, py = y + Math.sin(ang) * rad;
  // If that spot is geometry, do not teleport to the death point either (the corpse position is
  // legal for a body, and the pickup has its own radius): take the nearest legal tile to it.
  if (D && D.circleBlocked && D.circleBlocked(px, py, 12)) {
    let ok = false;
    for (let rr = 10; rr <= 40 && !ok; rr += 10) {
      for (let a = 0; a < TAU; a += Math.PI / 6) {
        const tx = x + Math.cos(ang + a) * rr, ty = y + Math.sin(ang + a) * rr;
        if (!D.circleBlocked(tx, ty, 12)) { px = tx; py = ty; ok = true; break; }
      }
    }
    if (!ok) { const s = D.freeSpot(x, y, 26, 14); px = s.x; py = s.y; }
  }
  // Blessing is applied at drop time, not at pickup time, so the number that pops over the
  // quiver and the number that lands in the quiver are always the same number.
  if (kind === "arrows") { val = arrowDrop(val); }
  G.pickups.push({ x: px, y: py, kind, val, t: 20, bob: rand(0, 6) });
}

// ---------- Hunter's Blessing ----------
// Single place where a quiver's value is resolved, so every arrow source in the game respects the
// blessing and none of them can double-apply it. Integer shift keeps 6 -> 12 -> 24 -> 48 exact
// without floating-point drift, and the 9999 cap exists only so a pathological number of picks
// cannot produce Infinity in the HUD; that cap is a sanity guard, not balance.
const ARROW_DROP_CAP = 9999;
function arrowDrop(n) {
  const m = Math.max(1, (typeof P !== "undefined" && P.ammoSec | 0) || 1);
  return Math.min(ARROW_DROP_CAP, (n | 0) * m);
}

// ---------- supply drops: never run dry ----------
G.supplyT = 20;
function supplyTick(dt) {
  G.supplyT -= dt;
  if (G.supplyT > 0) return;
  G.supplyT = 20;
  if (!D || G.pickups.length > 60) return;
  // One call per drop, and the second item is placed relative to the first. Both items used to
  // call freeSpot(P.x, P.y, 90, 16) with identical arguments, and freeSpot expands rings outward
  // from the same centre in the same order, so it returned the identical tile twice: a heart
  // standing on a quiver, 90px to the hero's right, every 20 seconds.
  //
  // farSpot alone did not finish the job, because it only enforces spacing against candidates it
  // generated inside a single call. Two calls in the same frame are independent, so both could
  // still settle on the same ring and reproduce the pile. So the pair now shares one anchor and
  // each drop keeps TILE*2 from the item already placed, and - the part that actually mattered -
  // farSpot consults pickups already on the floor, which it did not before at all.
  const a = D.farSpot(P.x, P.y, 170, 16);
  G.pickups.push({ x: a.x, y: a.y, kind: "arrows", val: arrowDrop(12), t: 45, bob: rand(0, 6) });
  burst(a.x, a.y - 8, 5, "rgba(220,190,140,.7)", 60, .5, 3);
  const h = D.farSpot(a.x, a.y, 150, 16);
  G.pickups.push({ x: h.x, y: h.y, kind: "heart", val: 30, t: 45, bob: rand(0, 6) });
}

// ---------- actions ----------
function startShot() {
  if (P.atkCdT > 0) return;
  if (P.arrows <= 0) { popText(P.x, P.y - 60, "Out of arrows!", "#ff8a5e", false); AudioSys.hurt(); return; }
  let v = moveVec();
  if (!v.l) { // standing still: shoot in the direction we last faced/aimed
    const a0 = P.shootAng || (P.face >= 0 ? 0 : Math.PI);
    v = { dx: Math.cos(a0), dy: Math.sin(a0), l: 1 };
  }
  P.face = v.dx >= 0 ? 1 : -1;
  P.shootAng = Math.atan2(v.dy, v.dx);
  P.atkT = P.atkDur; P.atkCdT = P.atkCd;
  P.arrows--;
  AudioSys.slash();
  const n = P.multi;
  for (let i = 0; i < n; i++) {
    const spread = n > 1 ? (i - (n - 1) / 2) * .12 : 0;
    const a = P.shootAng + spread + rand(-.02, .02);
    G.projectiles.push({ x: P.x + Math.cos(a) * 16, y: P.y - 16 + Math.sin(a) * 12, vx: Math.cos(a) * 540, vy: Math.sin(a) * 540, dmg: P.dmg * heroAtkMul(), t: 1.4, ang: a, fromEnemy: false, hitSet: new Set() });
  }
  updateHud();
}
function heroAtkMul() { return 1; }
function cacheHeroDmg() { P.__dmgCached = Math.round(P.dmg * heroAtkMul()); }

function hurtEnemy(e, dmg, ang) {
  dmg = Math.round(dmg * rand(.92, 1.12));
  e.hp -= dmg; e.hurtT = .18;
  popText(e.x + rand(-8, 8), e.y - e.r - 30, String(dmg), "#ffe9a8");
  burst(e.x, e.y - e.r, 7, "#a4e04a", 160, .45, 3.5);
  burst(e.x, e.y - e.r, 3, "#5aa020", 120, .4, 3);
  AudioSys.hit();
  if (!e.boss) moveEnt(e, e.r, Math.cos(ang), Math.sin(ang), 90, .12);
  if (e.hp <= 0) killEnemy(e);
}
function killEnemy(e) {
  e.dead = true;
  G.kills++;
  if (P.lifesteal) P.hp = Math.min(P.maxHp, P.hp + P.lifesteal);
  burst(e.x, e.y - e.r, 16, "#8fce3a", 220, .6, 4.5);
  burst(e.x, e.y - e.r, 6, "#3f5e14", 150, .55, 4);
  AudioSys.die();
  gainXp(ENEMY_STATS[e.kind] ? ENEMY_STATS[e.kind].xp : (BOSS_STATS[e.kind] || { xp: 20 }).xp);
  if (e.boss) {
    G.boss = null; bossWrap.classList.add("hidden");
    G.wave = "cleared"; G.waveTimer = 0;
    for (let i = 0; i < 6; i++) dropPickup(e.x, e.y - 20, "gold", randi(8, 14));
    dropPickup(e.x + 24, e.y, "heart", 30);
    dropPickup(e.x - 24, e.y, "arrows", 10);
    G.shake = Math.max(G.shake, 10);
    announce("Victory!", "The path ahead is clear.");
  } else {
    const st = ENEMY_STATS[e.kind];
    // Plain purse, as it was before the lucky-drop pass: the 20% roll belongs to the quiver,
    // not to gold.
    dropPickup(e.x, e.y - 10, "gold", randi(st.gold[0], st.gold[1]));
    if (Math.random() < .12) dropPickup(e.x, e.y - 20, "heart", 12);
    // 20% of slain foes drop a full quiver of 6 arrows.
    if (Math.random() < .20) dropPickup(e.x, e.y + 6, "arrows", 6);
  }
  updateHud();
}
function gainXp(n) {
  P.xp += n;
  // One boon per level, not one per kill-burst. The original called openLevelUp() inside the
  // while loop, so a multi-level gain opened several panels and the last overwrote the rest.
  // Queue the count instead and let the UI drain it, so N levels means exactly N picks.
  let levels = 0;
  while (P.xp >= P.xpNext) {
    P.xp -= P.xpNext; P.lvl++; levels++;
    // Advance the canonical UNSCALED curve, then scale once when assigning. The next gate is
    // derived from the curve value, never from the previously-scaled number: scaling an already
    // scaled value made the +50% compound each level (level 6 reached +169% instead of +50%).
    // `|| XP_BASE` keeps a pathological round-to-zero from wedging leveling entirely.
    const curve = Math.round(P.xpNext / XP_REQUIREMENT_SCALE);
    P.xpNext = Math.round(Math.round(curve * 1.45 + 12) * XP_REQUIREMENT_SCALE) || XP_BASE;
  }
  if (levels > 0) {
    P.__dmgCached = undefined;
    // A level-up that lands while the boon panel is already open must not be lost or
    // double-issued. The panel is modal (state === "levelup"), so any level-up arriving here
    // while it is up is folded into BOON_QUEUE and presented as the next panel once the
    // current pick is made: exactly one panel per level, in order, no duplicates.
    //
    // The earlier "7 picks for 2 levels" came from counting levels as floor(P.xp / P.xpNext)
    // over an XP balance that had already been carried across several kills. With a small
    // xpNext that quotient overstates the number of levels actually crossed, so one kill could
    // issue a handful of picks. The loop now counts each threshold it actually crosses, so
    // N levels means exactly N picks.
    AudioSys.levelup();
    queueLevelUps(levels);
  }
  updateHud();
}
function hurtHero(dmg, srcX, srcY) {
  if (P.invuln > 0 || G.state !== "play") return;
  P.hp -= Math.round(dmg); P.invuln = 1.1; P.hitFx = .3; G.shake = Math.max(G.shake, 5);
  AudioSys.hurt();
  const a = Math.atan2(P.y - srcY, P.x - srcX);
  moveEnt(P, 12, Math.cos(a), Math.sin(a), 150, .12);
  burst(P.x, P.y - 16, 8, "#ff6b5e", 160, .5, 4);
  updateHud();
  if (P.hp <= 0) { P.hp = 0; gameOver(false); }
}
function togglePause() {
  if (G.state === "play") { G.state = "pause"; showScreen("pause"); }
  else if (G.state === "pause") { G.state = "play"; showScreen(null); }
}
