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
// +50% XP required to reach each level. Multiplies the XP gate itself, not the curve's growth
// rate, so every threshold (including the initial one) is uniformly 1.5x further away.
const XP_REQUIREMENT_SCALE = 1.5;
// Canonical UNSCALED level-1 gate. gainXp advances the unscaled curve and applies this scale
// exactly once, at the point of assignment, so this value must stay unscaled: pre-scaling it
// would apply the +50% twice at level 1.
const XP_BASE = 26;
// Spider web slow. A web bolt that lands on the hero cuts movement speed to WEB_SLOW of normal
// for WEB_SLOW_T seconds. Kept as dials here because both the projectile hit (ui.js) and the
// movement step (ui.js) read them.
const WEB_SLOW = .55;
const WEB_SLOW_T = 2.2;
// Torchlit lighting. The additive halo pass lights a room, and the light mask (ui.js) darkens
// everything the lights do not reach.
//
// Tuned back toward the old look after the mask overshot. A 330px pool in a ~1100x700 hall left a
// small lit disc around Faelar and murky edges everywhere else, i.e. a fog-of-war instead of a
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
// 1.0 in the very centre is what bleached Faelar and his immediate surroundings: the mask fully
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