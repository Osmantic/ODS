// Elf of Doom — zones, enemy data, audio, render helpers
"use strict";

// ---------- utils ----------
const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
const lerp = (a, b, t) => a + (b - a) * t;
const rand = (a, b) => a + Math.random() * (b - a);
const randi = (a, b) => Math.floor(rand(a, b + 1));
const dist = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by);
const pick = arr => arr[Math.floor(Math.random() * arr.length)];
const TAU = Math.PI * 2;

// lighten/darken a hex color by amount (-100..100)
function shade(hex, amt) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const r = clamp(((n >> 16) & 255) + amt, 0, 255);
  const g = clamp(((n >> 8) & 255) + amt, 0, 255);
  const b = clamp((n & 255) + amt, 0, 255);
  return "rgb(" + (r | 0) + "," + (g | 0) + "," + (b | 0) + ")";
}

// hex color + alpha -> rgba() string
function hexA(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
}

// ---------- realms (dungeon themes) ----------
const ZONES = [
  { name: "The Gate of Ash", wall: "#3a333b", wallTop: "#4a4350", floor: "#241f24", accent: "#6e6478",
    enemies: ["brute", "brute", "brutearcher", "ironjaw"], boss: { kind: "captain", name: "Rukhmak, Captain of the Gate" } },
  { name: "Ash Halls of Vaighruz", wall: "#3d3227", wallTop: "#4e4132", floor: "#27201a", accent: "#8a6f4a",
    enemies: ["brute", "ironjaw", "bat", "brutearcher", "brute"], boss: { kind: "troll", name: "Vhelmor the Cave-Troll" } },
  { name: "Thren-Ost Warrens", wall: "#2c2840", wallTop: "#3a3558", floor: "#1c1928", accent: "#8c7ac0",
    enemies: ["spider", "spider", "brute", "ironjaw", "brutearcher"], boss: { kind: "spider", name: "Xath-Rokka the Dread Weaver" } },
  { name: "Fenris Hollow of Har-Mazel", wall: "#3a3428", wallTop: "#4c4534", floor: "#241f16", accent: "#9a8452",
    enemies: ["fenris", "brute", "bat", "ironjaw", "spider", "brutearcher"], boss: { kind: "fenris", name: "Skarnax, Fenris of the Pits" } },
  { name: "Ember Forges", wall: "#42281f", wallTop: "#56342a", floor: "#2a1712", accent: "#c06a34", lava: true,
    enemies: ["ironjaw", "ironjaw", "bat", "spider", "fenris", "brutearcher"], boss: { kind: "troll", name: "Druskral the Ember-Troll" } },
  { name: "Mount Cinderfall — Ashspire", wall: "#4a2418", wallTop: "#5e2f1e", floor: "#301208", accent: "#e07020", lava: true, hell: true,
    enemies: ["ironjaw", "ironjaw", "fenris", "spider", "bat", "brutearcher"], boss: { kind: "hollowcrown", name: "The Hollow Crown of Vaighruz" } },
];

const ENEMY_STATS = {
  brute:    { hp: 40, spd: 82,  dmg: 7,  r: 14, gold: [2, 5],  xp: 9,  name: "Ridgeback Grunt" },
  ironjaw:  { hp: 78, spd: 70,  dmg: 11, r: 16, gold: [4, 8],  xp: 15, name: "Ironjaw" },
  bat:    { hp: 24, spd: 140, dmg: 5,  r: 11, gold: [1, 3],  xp: 7,  name: "Fell-bat" },
  spider: { hp: 52, spd: 96,  dmg: 9,  r: 15, gold: [3, 6],  xp: 12, name: "Vaighruz Spider" },
  fenris:   { hp: 70, spd: 155, dmg: 12, r: 17, gold: [4, 7],  xp: 16, name: "Fenris Hound" },
  archer: { hp: 34, spd: 68,  dmg: 8,  r: 13, gold: [3, 6],  xp: 13, name: "Ridgeback Archer" },
  brutearcher: { hp: 46, spd: 74, dmg: 10, r: 14, gold: [4, 7], xp: 15, name: "Brute Archer" },
};
const BOSS_STATS = {
  captain: { hp: 320, spd: 85,  dmg: 10, r: 21, xp: 80,  scale: 1.35 },
  troll:   { hp: 560, spd: 58,  dmg: 16, r: 29, xp: 120, scale: 1.8 },
  spider:  { hp: 460, spd: 108, dmg: 12, r: 23, xp: 110, scale: 1.5 },
  // drawFenris scales the boss about its own feet, so scale is the whole size story. 2.15 measured
  // only ~77 px tall against a 34 px tile and read as another big pack wolf; 2.8 makes him ~100 px
  // tall and ~200 px nose-to-tail, about three pack hounds long, so he is unmistakable next to them.
  // Hit detection does not read this: ui.js meleeReach derives the bite from the muzzle art, so
  // growing the sprite grows the bite only as far as the nose actually reaches (~2.7 tiles here,
  // and the nose tip is ~1.9 tiles out), instead of the old flat 55 * scale + 14.
  fenris:    { hp: 430, spd: 158, dmg: 13, r: 21, xp: 100, scale: 2.8 },
  hollowcrown:  { hp: 1100, spd: 100, dmg: 17, r: 23, xp: 400, scale: 1.6 },
};

const ROSTER_SCALE = 1.3;    // +30% enemies in every spawned wave
const ENEMY_SPEED_SCALE = 1.15 * 1.2;  // +15% (earlier request) then +20% (this one) = x1.38 overall
const BOSS_HP_SCALE = 2;     // +100% boss health
const MELEE_DMG_SCALE = 2;   // melee contact hits only
// Wolves get 3x the health pool. The Fenris Hound is the only wolf kind in this game -
// ENEMY_STATS.fenris is the only wolf kind - so this covers every wolf in the run. Applied to both
// the regular spawn and the hound BOSS, because "3x health" plainly means the creature, and a
// boss hound is still a hound. Keyed on kind rather than inlined into hp so this stays one dial.
const WOLF_HP_SCALE = 3;
// -15% wolf movement speed. The hounds felt OP purely from pace, so this is a wolf-only dial
// multiplied in after ENEMY_SPEED_SCALE at both spawn sites in engine.js. Every other kind reads
// ENEMY_SPEED_SCALE alone and is untouched. Keyed on kind "fenris" (the only wolf kind), so it
// covers pack hounds and the hound boss Skarnax alike, same convention as WOLF_HP_SCALE.
const WOLF_SPD_SCALE = .85;
// The two cave trolls (Vhelmor, Druskral) were asked to feel tanky and to get scarier once hurt.
// One HP dial next to the wolf precedent, keyed on kind "troll": the boss spawn multiplies it in,
// and the regen / enraged-behaviour side lives in ui.js behind the same kind test plus boss.
// Both BOSS_STATS.troll entries are trolls and no regular enemy is, so nothing else is affected.
const TROLL_HP_SCALE = 1.3;   // +30% health for the troll bosses
// +50% XP required to reach each level. Multiplies the XP gate itself, not the curve's growth
// rate, so every threshold (including the initial one) is uniformly 1.5x further away.
const XP_REQUIREMENT_SCALE = 1.5;
// Canonical UNSCALED level-1 gate. gainXp advances the unscaled curve and applies this scale
// exactly once, at the point of assignment, so this value must stay unscaled: pre-scaling it
// would apply the +50% twice at level 1.
const XP_BASE = 26;
// First tier of the flat late-game gate. Once the hero reaches XP_FLAT_FROM_LEVEL, gates stop
// following the ×1.45 curve, which by level ~11 demanded 30k+ XP per level and stalled leveling
// in the deeper realms. A fixed cost keeps picks coming at a steady pace. Curve value at level
// 11 was 3233 (×1.5 = gate shown in HUD), so 2500 is slightly below that — a deliberate easing,
// not a nerf of an already-climbable hill. Used as the first tier cost below; later tiers rise.
const XP_FLAT = 2500;
const XP_FLAT_FROM_LEVEL = 11; // the first flat tier starts here
// Tiered late-game gates. A single flat 2500 kept picks coming too fast deep into the run — at
// circuit 10 the hero was level 257 and the boon panels never stopped. So the flat gate steps up
// in tiers: 2500 from level 11, then 5000 after level 25, 10000 after 50 and 15000 after 75.
// Each entry is {fromLevel, cost}, and gainXp looks it up with the level the hero JUST REACHED
// (P.lvl is incremented before the lookup), so fromLevel is the level reached whose reward gate
// — i.e. the step from fromLevel to fromLevel+1 — first costs `cost`.
// So the step after 25 costs 5000, after 50 costs 10000, after 75 costs 15000. Read by
// xpGateFor() in engine.js; below the first fromLevel the ×1.45 curve still runs unchanged.
const XP_TIERED = [
  { fromLevel: XP_FLAT_FROM_LEVEL, cost: XP_FLAT }, // reaching 11..24 pays 2500 for the next level
  { fromLevel: 25, cost: 5000 },                    // reaching 25..49 pays 5000
  { fromLevel: 50, cost: 10000 },                   // reaching 50..74 pays 10000
  { fromLevel: 75, cost: 15000 },                   // reaching 75+ pays 15000
];
// Spider web slow. A web bolt that lands on the hero cuts movement speed to WEB_SLOW of normal
// for WEB_SLOW_T seconds. Kept as dials here because both the projectile hit (ui.js) and the
// movement step (ui.js) read them.
const WEB_SLOW = .55;
const WEB_SLOW_T = 2.2;
// Torchlit lighting. The additive halo pass lights a room, and the light mask (ui.js) darkens
// everything the lights do not reach.
//
// Tuned back toward the old look after the mask overshot. A 330px pool in a ~1100x700 hall left a
// small lit disc around Gabriel and murky edges everywhere else, i.e. a fog-of-war instead of a
// torchlit hall. Three dials, all deliberately weaker than they were:
//   HERO_GLOW_R  - reach of his light. Wider than a screen would lift the whole arena flat; a
//                  little over half the viewport width reads as one torch in a room.
//   LIGHT_DARK   - how black unlit floor gets. Most important value here: it has to be low enough
//                  that you can still make out the far wall, braziers and approaching brutes.
//   LIGHT_AMB    - a floor under the veil, so nothing goes fully black (see ui.js _lightPass).
// The old 470 reached well past half the viewport and was the wide flat beige disc; paired with
// the softened hero mask below, 360 keeps him a torch in a room instead of a spotlight.
const HERO_GLOW_R = 360;     // radius of the hero's torchlight pool
const BRAZIER_GLOW_R = 205;  // radius of one brazier's firelight pool
// The veil opacity, and the thing that decides whether this reads as a torchlit hall or as a
// porthole. It was raised to .62 while chasing a "too washed out" report and that is what made
// the arena collapse to a small lit disc with mud at the edges: the visible brightness ratio
// between light and dark is (1 - 0) / (1 - LIGHT_DARK), and .62 means the unlit floor keeps only
// 38% of its colour. .22 gives the old torchlit contrast back - braziers and the hero still
// clearly punch a pool of light into the room, but you can read the whole hall, walls and all.
const LIGHT_DARK = .40;      // opacity of the shadow over unlit floor
// The milky-grey film report traces here, not to LIGHT_DARK. This is a flat WHITE disc stretched
// over the whole viewport and blended into the veil with 'lighter', so it is a screen-wide white
// lift: every pixel it touches loses contrast and saturation towards grey, corners included, and
// at .08 over an already-dark hall that is exactly "faded colours under a haze". Lowering it does
// two things at once - it thins that film and it deepens the corners, because the minimum veil
// alpha is (LIGHT_DARK - LIGHT_AMB). It is kept above 0 only so a huge hall never goes fully
// black where no torch can ever reach.
const LIGHT_AMB = .03;       // ceiling on that shadow: the darkest unlit floor keeps this much
// 1.0 in the very centre is what bleached Gabriel and his immediate surroundings: the mask fully
// erased the shadow there, so the additive warm pass landed on unshadowed stone. .74 leaves about
// a quarter of the veil under him, which is what keeps his cloak, hair and the floor under his
// boots deep while still reading as "the light is coming from him".
const LIGHT_HERO_A = .74;    // how completely the hero's light clears that shadow
const LIGHT_FIRE_A = .93;
// During a boss fight a fresh wave reinforcements the boss on this cadence. The Hollow Crown is
// the final boss and gets half the interval, so the last fight is the busiest of the run.
const BOSS_REINFRESH_S = 60;
const FINAL_BOSS_REINFRESH_S = 30;
const FINAL_BOSS_KIND = "hollowcrown";
// Rukhmak's special power: he calls his reinforcements twice as often as any other boss. Keyed on
// kind rather than realm, so it follows him wherever he is posted.
const CAPTAIN_BOSS_KIND = "captain";
// Rukhmak's numbers, for readers of the constant block: 30 s in loop 1, 15 s from loop 2 on. They
// are not declared as constants because the function below derives them from the general rule, and
// a second copy of each value here could only drift from it, not guard it.

/* ---------- endless quest: the six realms repeat forever ----------
   Clearing realm 6 no longer ends the run. The quest-victory panel still appears, its button
   now reads Continue, and the next realm is realm 1 again. Each completed loop now multiplies:
     · enemy count by 1.25 at every spawn (wave, boss escort, boss reinforcements), capped at 4x
       that spawn's size — 1.25, 1.56, 1.95, 2.44, 3.05, 3.81, then 4x from loop 8 on, which on a
       capped wave base is the sequence 37, 46, 58, 73, 91, 114, 120,
     · enemy health by 1.5 on top of the per-realm ramp, becoming x2 per loop on the loop after the
       count locks, carrying on from where it stands rather than recomputing the earlier loops,
     · enemy and boss damage by 1.15,
     · enemy and boss movement speed by 1.1, capped at +50% in total,
     · boss health by 2,
     · boss reinforcements already on the realm-6 cadence from the first boss of the loop.
   Realm 6's interval is the ceiling forever: deeper loops raise crowd size and boss bulk only,
   never the clock. XP and gold are untouched by loops, as before.
   Loop 1 multiplies every factor below by exactly 1, so a first pass plays as it always did.

   Why the count is capped and health is not. An uncapped count is what made deep loops read as
   overcrowded rather than hard, and crowd rasterisation and AI are also what caused the frame
   stalls; 4x a spawn's loop 1 size is the ceiling for all of it. Health is deliberately uncapped,
   and deliberately doubles per loop once the count locks, so depth keeps being paid for after the
   arena stops filling up. The loop at which the cap binds is a property of each spawn expression,
   not a global constant: see countCapLoop, and the OWNER-SPECIFIED CURVES note below for
   how the crossover is carried forward without a jump. */
const REALM_COUNT = 6;                                    // realms per loop: always 1..6
// --- per-loop progression -------------------------------------------------
// Replaces the flat x1.5 on enemy count and x1.5 on boss HP. Each is a function of loop whose loop-1
// value is exactly 1, so the first pass is untouched.
//
// OWNER-SPECIFIED CURVES, settled after two rounds of play. probe/loop-scaling.test.js pins every
// number below, so a retune is a loud test failure rather than silent drift.
//
// (a) COUNT COMPOUNDS at x1.25 per loop and locks at 4x. Earlier drafts grew the crowd by a flat +25%
//     OF LOOP 1 each loop (30, 37, 45, 52...), which pushed the cap out to loop 13. Compounding gives
//     30, 37, 46, 58, 73, 91, 114, 120 and locks at loop 8 — matching the shipped x1.5 crowd curve at
//     loops 2 and 3 rather than undercutting it, then flattening sooner.
//
// (b) HEALTH CARRIES FORWARD THROUGH THE CROSSOVER, with no jump. x1.35 per loop up to the cap, then
//     x2 per loop after it, each loop multiplying the PREVIOUS loop's value. The naive form — swap the
//     exponent base once capped — recomputes the whole history at the new rate, which at the shipped
//     growCap of 7 makes loop 8 about 16x loop 7. That is what this shape removes: the crossover loop
//     is exactly x1.35 x2 = x2.7 over the loop before it, then x2 per loop from there on.
//
//     The crossover is a property of the SPAWN, not a global constant, so the health curve is derived
//     from the count curve: countCapLoop() reports the loop the crowd grew up to, and loopHpMul
//     switches on it. A spawn whose curve locks at loop 3 (a hypothetical retune) gets its x3 crossover
//     at loop 3. This is why loopHpMul takes a grow-cap rather than a boolean flag.
//
// (c) BOSS HEALTH is its own +100%-per-loop dial ALONE. The request moves the boss's existing
//     per-loop term from +50% to +100%; it does not ask for the new enemy-health term to apply to
//     bosses as well, so it does not. Bosses and their escorts stay in the same league because both
//     roughly double per loop.
const LOOP_COUNT_STEP = 1.25;   // x1.25 crowd per completed loop, compounding
const LOOP_COUNT_CAP = 4;       // ...but never more than 4x that spawn's loop 1 size (loop 8 onward)
const LOOP_HP_STEP = 1.35;      // x1.35 enemy HP per loop while the crowd is still growing
const LOOP_HP_STEP_CAPPED = 2;  // x2 per loop once the crowd has locked
const LOOP_DMG_STEP = 1.15;     // x1.15 enemy and boss damage per loop
const LOOP_SPD_STEP = 1.1;      // x1.1 enemy and boss movement speed per loop, compounding...
const LOOP_SPD_CAP = 0.5;       // ...capped at a +50% BONUS in total (reached at loop 6)
const LOOP_BOSS_HP_STEP = 2;    // x2 boss health per completed loop
function loopCountMul(loop) {
  return Math.min(LOOP_COUNT_CAP, Math.pow(LOOP_COUNT_STEP, Math.max(0, (loop | 0) - 1)));
}
// How many loops the crowd still GREW, i.e. the loop on which it locked. The spawn's curve is
// monotonic and caps at LOOP_COUNT_CAP, so once it locks it stays locked and one number describes it:
// 7 for the shipped x1.25 curve, which locks at loop 8. Passed to loopHpMul so the crossover belongs
// to the spawn rather than to a global loop number.
function countCapLoop(mulAtLoop) {
  let growing = 0;
  for (let L = 1; L <= 64; L++) { if (mulAtLoop(L) < LOOP_COUNT_CAP - 1e-9) growing++; else break; }
  return growing;
}
// Health carries forward through the crossover: x1.35 for every loop the crowd grew, then x2 from the
// loop it locks on. With growCap = 7 that is x1.35 through loop 8, loop 8 over loop 7 is 1.35x2 = x2.7,
// and every later loop is x2. No exponent is ever rebased, which is the whole point — the first
// version swapped the base once capped and jumped hard at the crossover.
function loopHpMul(loop, growCap) {
  const grow = Math.max(0, growCap | 0), lp = Math.max(0, (loop | 0) - 1);
  if (lp <= grow) return Math.pow(LOOP_HP_STEP, lp);
  // The loop after the crowd locks is where x2 joins the x1.5, so it is a x3 step over the loop before
  // it and every later loop is a plain x2. No exponent is rebased at any point — that is what made the
  // superseded version jump ~51x.
  return Math.pow(LOOP_HP_STEP, grow) * Math.pow(LOOP_HP_STEP_CAPPED, lp - grow);
}
function loopDmgMul(loop) { return Math.pow(LOOP_DMG_STEP, Math.max(0, (loop | 0) - 1)); }
// Compounding, capped on the BONUS: each loop is x1.1 the last, and LOOP_SPD_CAP is the cap on the
// bonus over loop 1, so the multiplier locks at exactly 1.5 (+50%) from loop 6, where 1.1^5 - 1 =
// 0.61 exceeds it. LOOP_SPD_CAP is a BONUS cap (0.5), not a total (1.5): the 1 + is added after the
// min, and reading the constant as a total multiplier silently gave +150%.
function loopSpdMul(loop) {
  return 1 + Math.min(LOOP_SPD_CAP, Math.pow(LOOP_SPD_STEP, Math.max(0, (loop | 0) - 1)) - 1);
}
function loopBossHpMul(loop) { return Math.pow(LOOP_BOSS_HP_STEP, Math.max(0, (loop | 0) - 1)); }
// G.zone keeps counting upward (0, 1, 2 ...) and is never wrapped, so one value encodes both the
// layout played and how deep the loop is. realmIndex() picks the ZONES entry; loopNumber() is 1
// on the first pass, 2 after the first full circuit, and so on.
function realmIndex(zone) { return ((zone | 0) % REALM_COUNT); }
function loopNumber(zone) { return Math.floor((zone | 0) / REALM_COUNT) + 1; }
// Boss-reinforcement cadence for a realm/loop pair, in seconds.
//
// Shipped rule, unchanged for every boss but Rukhmak: 60 s, halved to 30 s for the Hollow Crown and
// for every boss from loop 2 on. That 30 s is the floor for them — deeper loops raise crowd size and
// boss bulk, never the clock (see the endless-quest note above).
//
// Rukhmak is the one exception and the reason the general rule is kept as a separate expression
// below: his interval is exactly half of whatever any other boss in the same loop gets, so he is
// handled by scaling the general result rather than by adding another special case to it. He is
// therefore the only boss below the 30 s floor, by design; the "never faster than realm 6" ceiling
// still governs everyone else.
function bossReinIntervalS(kind, loop) {
  const general = (kind === FINAL_BOSS_KIND || (loop | 0) > 1) ? FINAL_BOSS_REINFRESH_S : BOSS_REINFRESH_S;
  return kind === CAPTAIN_BOSS_KIND ? general / 2 : general;
}
// Rukhmak's cadence expressed directly, for callers and docs that want his number without the kind
// test. Kept in terms of the general function so the two cannot drift apart.
function captainReinIntervalS(loop) {
  return bossReinIntervalS(CAPTAIN_BOSS_KIND, loop);
}
// Quest-victory panel tier, one step per completed circuit: as-shipped bronze, then silver, then
// gold. Deeper loops keep the gold treatment rather than inventing new metals.
const VICTORY_TIERS = [
  { name: "Bronze", cls: "victory-bronze" },
  { name: "Silver", cls: "victory-silver" },
  { name: "Gold",   cls: "victory-gold" },
];
function victoryTierIndex(completedLoops) {
  return clamp((completedLoops | 0) - 1, 0, VICTORY_TIERS.length - 1);
}

// ---------- loot rarity ----------
const RARITY = {
  common:    { name: "Common",    color: "#cfc4b0", mul: 1 },
  magic:     { name: "Magic",     color: "#6db4ff", mul: 1.5 },
  rare:      { name: "Rare",      color: "#ffd76b", mul: 2.2 },
  legendary: { name: "Legendary", color: "#ff8a2a", mul: 3.4 },
};

// ---------- audio (WebAudio synth) ----------
const AudioSys = {
  ctx: null, muted: false,
  _noiseBufs: null,           // pre-built noise buffers (never allocate per shot)
  _lastSfx: Object.create(null), // per-sound throttling: big fights must not spawn thousands of nodes
  ensure() {
    if (!this.ctx) { try { this.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { this.ctx = null; } }
    if (this.ctx && this.ctx.state === "suspended") { try { this.ctx.resume(); } catch (e) {} }
    this._buildNoise();
  },
  _buildNoise() {
    if (!this.ctx || this._noiseBufs) return;
    const mk = (dur, decay) => {
      const n = Math.max(1, Math.floor(this.ctx.sampleRate * dur));
      const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
      const d = buf.getChannelData(0);
      for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (decay ? (1 - i / n) : 1);
      return buf;
    };
    try {
      this._noiseBufs = {
        short: mk(.05, true),   // hit
        slash: mk(.08, true),   // bow release
        crit: mk(.1, true),
        hurt: mk(.12, true),
        die: mk(.32, true),
        boom: mk(.5, true),
        hiss: mk(.3, false),
      };
    } catch (e) { this._noiseBufs = null; }
  },
  tone(freq, dur, type, vol, slide) {
    if (!this.ctx || this.muted) return;
    try {
      const t = this.ctx.currentTime, o = this.ctx.createOscillator(), g = this.ctx.createGain();
      o.type = type || "square"; o.frequency.setValueAtTime(freq, t);
      if (slide) o.frequency.exponentialRampToValueAtTime(Math.max(30, freq + slide), t + dur);
      g.gain.setValueAtTime(vol || .12, t);
      g.gain.exponentialRampToValueAtTime(.0001, t + dur);
      o.connect(g); g.connect(this.ctx.destination); o.start(t); o.stop(t + dur + .02);
    } catch (e) {}
  },
  noise(dur, vol, filt, bufName) {
    if (!this.ctx || this.muted) return;
    try {
      if (!this._noiseBufs) this._buildNoise();
      const buf = (this._noiseBufs && (this._noiseBufs[bufName] || this._noiseBufs.short)) || null;
      if (!buf) return;
      const src = this.ctx.createBufferSource(), g = this.ctx.createGain();
      src.buffer = buf; g.gain.value = vol || .15;
      if (filt) { const f = this.ctx.createBiquadFilter(); f.type = "lowpass"; f.frequency.value = filt; src.connect(f); f.connect(g); }
      else src.connect(g);
      g.connect(this.ctx.destination); src.start();
    } catch (e) {}
  },
  // true when this sound key may play now (minMs apart). Keeps a swarm of brutes
  // being hit from creating thousands of oscillator/buffer nodes in one frame.
  _gate(key, minMs) {
    const now = (typeof performance !== "undefined" ? performance.now() : Date.now());
    const last = this._lastSfx[key];
    if (last !== undefined && now - last < minMs) return false;
    this._lastSfx[key] = now;
    return true;
  },
  slash() { if (!this._gate("slash", 45)) return; this.noise(.08, .1, 0, "slash"); this.tone(720, .07, "sawtooth", .05, -460); },
  crit() { if (!this._gate("crit", 60)) return; this.noise(.1, .18, 0, "crit"); this.tone(980, .1, "square", .08, -500); this.tone(140, .14, "sawtooth", .12, -60); },
  hit() { if (!this._gate("hit", 55)) return; this.noise(.05, .14, 0, "short"); this.tone(170, .08, "square", .09, -70); },
  hurt() { if (!this._gate("hurt", 120)) return; this.tone(200, .24, "sawtooth", .13, -130); this.noise(.12, .12, 900, "hurt"); },
  coin() { this.tone(980, .06, "sine", .08); setTimeout(() => this.tone(1320, .09, "sine", .08), 55); },
  loot() { [660, 880, 1100].forEach((f, i) => setTimeout(() => this.tone(f, .14, "sine", .08), i * 70)); },
  potion() { this.tone(420, .18, "sine", .12, 380); },
  levelup() { [523, 659, 784, 1046].forEach((f, i) => setTimeout(() => this.tone(f, .22, "triangle", .12), i * 110)); },
  blast() { if (!this._gate("blast", 90)) return; this.tone(80, .55, "sawtooth", .17, -55); this.noise(.4, .18, 700, "boom"); },
  whirl() { if (!this._gate("whirl", 90)) return; this.noise(.3, .13, 2000, "hiss"); this.tone(460, .3, "triangle", .08, 300); },
  die() { if (!this._gate("die", 70)) return; this.tone(300, .45, "sawtooth", .12, -230); this.noise(.18, .1, 600, "die"); },
  bossRoar() { this.tone(65, .8, "sawtooth", .18, -25); this.noise(.5, .16, 400, "boom"); },
  win() { [392, 523, 659, 784, 1046, 1318].forEach((f, i) => setTimeout(() => this.tone(f, .3, "triangle", .12), i * 150)); },
  lose() { [440, 349, 262, 196].forEach((f, i) => setTimeout(() => this.tone(f, .4, "triangle", .12, -30), i * 260)); },
  buy() { this.tone(660, .08, "square", .07); setTimeout(() => this.tone(880, .12, "square", .07), 70); },
  stairs() { this.tone(196, .3, "triangle", .1); setTimeout(() => this.tone(147, .4, "triangle", .1), 200); },
  pickupBuff() { [523, 784].forEach((f, i) => setTimeout(() => this.tone(f, .16, "triangle", .1), i * 80)); },
};

// ---------- painting helpers (grounded / painterly) ----------
function fillEllipse(g, x, y, rx, ry, fill) {
  g.beginPath(); g.ellipse(x, y, Math.max(.5, rx), Math.max(.5, ry), 0, 0, TAU);
  g.fillStyle = fill; g.fill();
}
function outlineEllipse(g, x, y, rx, ry, fill, lw) {
  fillEllipse(g, x, y, rx, ry, fill);
  g.lineWidth = lw || 2; g.strokeStyle = "rgba(10,4,6,.9)"; g.stroke();
}
function fillCircle(g, x, y, r, fill) { fillEllipse(g, x, y, r, r, fill); }
function outlineCircle(g, x, y, r, fill, lw) { outlineEllipse(g, x, y, r, r, fill, lw); }
function inkLine(g, pts, color, lw) {
  g.beginPath(); g.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) g.lineTo(pts[i][0], pts[i][1]);
  g.lineWidth = lw || 3; g.strokeStyle = color || "rgba(10,4,6,.9)"; g.lineCap = "round"; g.lineJoin = "round"; g.stroke();
}
function vShade(g, x, y, r, top, bot) {
  const gr = g.createLinearGradient(x, y - r, x, y + r);
  gr.addColorStop(0, top); gr.addColorStop(1, bot);
  return gr;
}
function shadowBlob(g, x, y, r) {
  const gr = g.createRadialGradient(x, y, r * .2, x, y, r);
  gr.addColorStop(0, "rgba(0,0,0,.55)"); gr.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = gr;
  g.beginPath(); g.ellipse(x, y, r, r * .45, 0, 0, TAU); g.fill();
}
function legSwing(t, speed) { const p = t * speed; return [Math.sin(p) * 5, Math.sin(p + Math.PI) * 5]; }
