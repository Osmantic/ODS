// Elf of Doom — arena generation (one big room)
"use strict";

const TILE = 34;

// The full surface an arena object must expose. `D` is reassigned in startZone() *before* the
// backdrop is baked and before the first update tick, so a partially populated arena is reachable
// from the frame loop; this list is what lets the frame loop prove otherwise in one test instead
// of discovering a missing method as a thrown TypeError mid-fight.
const ARENA_METHODS = Object.freeze([
  "circleBlocked", "freeSpot", "farSpot", "nearestFloor", "isWall", "isFloor", "solidAt",
]);

function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

// one huge open hall with a handful of pillars/rocks as cover
// Every arena method closes over the grid it was built from and takes no arena argument, so they
// are only valid on the object that returned them. `D` is a single global that engine.js reassigns
// in startZone(); methods fetched from one arena and called after D was replaced would read the old
// grid. Keep the invariant by never keeping a method reference across that boundary.
function genArena(depth) {
  const RW = 40, RH = 30;
  const grid = new Uint8Array(RW * RH).fill(0);
  // border walls (2 thick)
  for (let y = 0; y < RH; y++) for (let x = 0; x < RW; x++) {
    if (x < 2 || y < 2 || x >= RW - 2 || y >= RH - 2) grid[y * RW + x] = 1;
  }
  const isWall = (tx, ty) => tx < 0 || ty < 0 || tx >= RW || ty >= RH || grid[ty * RW + tx] === 1;
  const isFloor = (tx, ty) => !isWall(tx, ty);

  const obstacles = []; // {x,y,r} circle blockers
  const rng = mulberry32(1234 + depth * 77);
  const nObs = 6 + Math.min(6, depth);
  let placed = 0, guard = 0;
  while (placed < nObs && guard++ < 300) {
    const px = (4 + rng() * (RW - 8)) * TILE, py = (4 + rng() * (RH - 8)) * TILE;
    const r = 16 + rng() * 12;
    if (dist(px, py, (RW / 2) * TILE, (RH / 2) * TILE) < 150) continue; // keep center clear
    let ok = true;
    for (const o of obstacles) if (dist(px, py, o.x, o.y) < o.r + r + 90) { ok = false; break; }
    if (!ok) continue;
    obstacles.push({ x: px, y: py, r });
    const tx0 = Math.floor((px - r) / TILE), tx1 = Math.ceil((px + r) / TILE);
    const ty0 = Math.floor((py - r) / TILE), ty1 = Math.ceil((py + r) / TILE);
    for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++) {
      if (tx < 3 || ty < 3 || tx >= RW - 3 || ty >= RH - 3) continue;
      if (isWall(tx, ty)) continue;
      if (dist((tx + .5) * TILE, (ty + .5) * TILE, px, py) < r + 8) grid[ty * RW + tx] = 1;
    }
    placed++;
  }

  const wpx = RW * TILE, hpx = RH * TILE;
  // flat solid lookup: index math only, no closure call per sample
  const solidAt = (tx, ty) => tx < 0 || ty < 0 || tx >= RW || ty >= RH || grid[ty * RW + tx] === 1;
  const circleBlocked = (x, y, r) => {
    if (x - r < TILE * 2 || y - r < TILE * 2 || x + r > wpx - TILE * 2 || y + r > hpx - TILE * 2) return true;
    const tx = Math.floor(x / TILE), ty = Math.floor(y / TILE);
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      if (solidAt(tx + dx, ty + dy)) {
        const cx = clamp(x, (tx + dx) * TILE, (tx + dx + 1) * TILE);
        const cy = clamp(y, (ty + dy) * TILE, (ty + dy + 1) * TILE);
        if (dist(cx, cy, x, y) < r) return true;
      }
    }
    return false;
  };
  // Random-rejection sampling used to retry up to 400 exact circle tests, and several
  // wedged enemies could pay for that in the same tick. Deterministic spiral instead:
  // one ring of candidates per radius, and the whole search is hard-capped per call.
  const spiral = (x0, y0, r, rad0, radMax, step, minDist, nearX, nearY, budget) => {
    for (let rad = rad0; rad <= radMax; rad += step) {
      const ring = Math.max(6, Math.ceil(TAU * rad / (step * 1.5)));
      for (let k = 0; k < ring; k++) {
        if (--budget < 0) return null;
        const a = k * (TAU / ring);
        const x = x0 + Math.cos(a) * rad, y = y0 + Math.sin(a) * rad;
        if (x < TILE * 2 + r || y < TILE * 2 + r || x > wpx - TILE * 2 - r || y > hpx - TILE * 2 - r) continue;
        if (minDist && dist(x, y, nearX, nearY) < minDist) continue;
        if (!circleBlocked(x, y, r)) return { x, y };
      }
    }
    return null;
  };
  // relocation of a wedged enemy: bounded (worst case ~260 cheap tests, then give up)
  //
  // The fallback is deliberately NOT spawnPoint. That point is also D.spawn, where the hero
  // starts, so every search that gave up dumped its content on top of him: the black stain that
  // sat next to spawn in every realm was the shadow() ellipse of an enemy parked there by this
  // line, invisible except for its ground shadow. Same reasoning as farSpot - a search that
  // cannot satisfy its constraints should relax them, not teleport onto the player.
  const freeSpot = (nearX, nearY, minDist, r) => {
    const s = spiral(nearX, nearY, r, TILE, TILE * 7, 15, minDist, nearX, nearY, 260);
    if (s) return s;
    // relax the distance rule before giving up: a wedged enemy only needs to be standing on
    // floor, it does not also have to clear minDist from where it already is
    const relaxed = spiral(nearX, nearY, r, TILE, TILE * 15, 24, 0, 0, 0, 220);
    if (relaxed) return relaxed;
    const loose = spiral(rand(TILE * 3, wpx - TILE * 3), rand(TILE * 3, hpx - TILE * 3), r,
      TILE, Math.max(wpx, hpx), TILE * 2, 0, 0, 0, 200);
    return loose || { x: nearX, y: nearY };
  };
  // A spawn/supply point anywhere on the walkable map. Used for enemies and pickups, which must
  // appear across the whole arena.
  //
  // Two separate bugs lived in the old version of this function, and the second one is why
  // pickups still landed in one pile beside the hero after the first was fixed. Both searches
  // passed minDist TWICE: once as a real reject inside spiral(), and again as `nearX/nearY`, the
  // point the ring expands *from*. That second argument meant the first admissible ring was
  // already minDist out from the hero, so the spiral systematically returned the nearest legal
  // ring around him. Every enemy walked in from the same arc just off his right shoulder and the
  // heart and quiver of a supply drop resolved to the identical tile, because both calls asked
  // for the same thing and always received the same answer.
  //
  // Here the seed is a random point across the map and the spiral expands from THAT, so the only
  // distance constraint against the hero is the explicit reject. That genuinely spreads spawns
  // over the whole hall instead of hugging one quadrant.
  const farSpot = (avoidX, avoidY, minDist, r) => {
    // Candidate list is per-call, but the hero is never the only thing to keep clear of: a wave
    // drops four supplies and a boss drops three, each in its own call, so anything already on the
    // floor counts too. Without this, two independent calls could still settle on the same ring
    // and land a heart on a quiver, which is the pile the owner kept seeing.
    const taken = (typeof G !== "undefined" && G.pickups) ? G.pickups : [];
    const cands = [];
    // keep well inside the wall band and off the hero; the ring search still relaxes outward
    const lo = TILE * 3,
      hiX = wpx - TILE * 3, hiY = hpx - TILE * 3;
    for (let attempt = 0; attempt < 26 && cands.length < 6; attempt++) {
      const s = spiral(rand(lo, hiX), rand(lo, hiY), r, TILE, Math.max(wpx, hpx), TILE * 1.6,
        minDist, avoidX, avoidY, 200);
      if (!s || dist(s.x, s.y, avoidX, avoidY) < minDist) continue;
      // reject near-duplicates so a wave or a supply pair cannot land in one pile by re-entering
      // the same neighbourhood on a different attempt, and reject spots on top of an existing
      // pickup for the same reason
      let dupe = false;
      for (const c of cands) if (dist(c.x, c.y, s.x, s.y) < TILE * 3) { dupe = true; break; }
      if (!dupe) for (const pk of taken) if (pk.kind !== "gold" && dist(pk.x, pk.y, s.x, s.y) < TILE * 2) { dupe = true; break; }
      if (!dupe) cands.push(s);
    }
    if (cands.length) return cands[randi(0, cands.length - 1)];
    // last resort: anywhere walkable, still off the hero, without the centre teleport
    const loose = spiral(rand(lo, hiX), rand(lo, hiY), r, TILE, Math.max(wpx, hpx), TILE * 2,
      0, 0, 0, 160);
    if (loose) return loose;
    const c = randi(0, 7);
    const corners = [
      [TILE * 4, TILE * 4], [wpx - TILE * 4, TILE * 4],
      [TILE * 4, hpx - TILE * 4], [wpx - TILE * 4, hpx - TILE * 4],
      [wpx * .5, TILE * 4], [wpx * .5, hpx - TILE * 4],
      [TILE * 4, hpx * .5], [wpx - TILE * 4, hpx * .5],
    ];
    for (let i = 0; i < 8; i++) {
      const p = corners[(c + i) & 7];
      if (!circleBlocked(p[0], p[1], r) && dist(p[0], p[1], avoidX, avoidY) >= minDist) return { x: p[0], y: p[1] };
    }
    return freeSpot(avoidX, avoidY, minDist, r);
  };
  // unstick an entity that ended up inside geometry: bounded local search, no rings of
  // 16 exact samples per radius and no global fallback
  const nearestFloor = (x, y, r) => spiral(x, y, r, TILE * .5, TILE * 3.5, 8, 0, 0, 0, 120) || { x, y };

  // ambient decor (visual only, kept away from obstacles)
  const decor = [];
  const kinds = depth >= 3 ? ["brazier", "barrel", "rubble"] : ["brazier", "barrel", "rubble"];
  for (let i = 0; i < 16; i++) {
    const tx = 3 + Math.floor(rng() * (RW - 6)), ty = 3 + Math.floor(rng() * (RH - 6));
    if (!isFloor(tx, ty)) continue;
    const px = (tx + .5) * TILE, py = (ty + .8) * TILE;
    let ok = true;
    for (const o of obstacles) if (dist(px, py, o.x, o.y) < o.r + 30) { ok = false; break; }
    if (!ok) continue;
    decor.push({ kind: kinds[Math.floor(rng() * kinds.length)], x: px, y: py });
  }

  const spawnPoint = { x: (RW / 2) * TILE, y: (RH / 2) * TILE };
  // RW/RH are the grid dimensions (tiles); wpx/hpx are the arena size in pixels. Both were here,
  // which made `D.RW > 0 && D.RH > 0 && D.wpx > 0 && D.hpx > 0` read like a complete arena check
  // while saying nothing about the eight methods AI and collision call. A half-populated arena
  // passed that test and then threw "D.nearestFloor is not a function" on the first wedged brute.
  // Keep the dimensions and add the method contract, so the one liveness test the frame loop uses
  // covers the whole surface the game depends on.
  const arena = {
    RW, RH, grid, wpx, hpx, solidAt,
    spawn: spawnPoint,
    exit: { x: (RW / 2) * TILE, y: (RH / 2) * TILE },
    circleBlocked, freeSpot, farSpot, nearestFloor, isWall, isFloor, decor, obstacles, rooms: [],
    baked: null,
  };
  if (!(arena.RW > 0 && arena.RH > 0 && arena.wpx > 0 && arena.hpx > 0) ||
      !isFinite(arena.wpx) || !isFinite(arena.hpx) ||
      !arena.spawn || !isFinite(arena.spawn.x) || !isFinite(arena.spawn.y) ||
      ARENA_METHODS.some(k => typeof arena[k] !== "function")) {
    throw new Error("genArena produced an incomplete arena (depth=" + depth + ")");
  }
  return arena;
}

// ---------- baked floor/wall art for the arena ----------
function bakeArena(D, depth, seed) {
  const z = ZONES[Math.min(depth, ZONES.length - 1)];
  const cv = document.createElement("canvas");
  cv.width = D.wpx; cv.height = D.hpx;
  const g = cv.getContext("2d");
  const rng = mulberry32(seed || 1);

  // floor base
  g.fillStyle = z.floor; g.fillRect(0, 0, D.wpx, D.hpx);
  // flagstone tiles
  for (let ty = 0; ty < D.RH; ty++) for (let tx = 0; tx < D.RW; tx++) {
    if (D.isWall(tx, ty)) continue;
    const x = tx * TILE, y = ty * TILE;
    const v = (rng() - .5) * 12;
    g.fillStyle = shade(z.floor, 8 + v);
    g.fillRect(x + 1, y + 1, TILE - 2, TILE - 2);
    g.strokeStyle = "rgba(0,0,0,.28)"; g.lineWidth = 2;
    g.strokeRect(x + 1.5, y + 1.5, TILE - 3, TILE - 3);
    if (rng() < .1) { // cracks
      g.strokeStyle = "rgba(0,0,0,.3)"; g.lineWidth = 1.2;
      g.beginPath();
      g.moveTo(x + rng() * TILE, y + rng() * TILE);
      g.lineTo(x + rng() * TILE, y + rng() * TILE);
      g.stroke();
    }
  }
  // ambient floor speckle
  for (let i = 0; i < 240; i++) {
    const x = rng() * D.wpx, y = rng() * D.hpx;
    if (!D.isFloor(Math.floor(x / TILE), Math.floor(y / TILE))) continue;
    g.fillStyle = `rgba(255,235,200,${.02 + rng() * .03})`;
    g.beginPath(); g.arc(x, y, 1 + rng() * 2.4, 0, TAU); g.fill();
  }

  // subtle elven carpet at the spawn ground, and faint scorch patches for grit
  {
    const cx = D.spawn.x, cy = D.spawn.y;
    const rug = g.createRadialGradient(cx, cy + 6, 20, cx, cy + 6, 190);
    rug.addColorStop(0, "rgba(70,120,70,.18)"); rug.addColorStop(.6, "rgba(50,95,60,.09)"); rug.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = rug; g.beginPath(); g.ellipse(cx, cy + 6, 190, 120, 0, 0, TAU); g.fill();
    g.strokeStyle = "rgba(180,210,160,.10)"; g.lineWidth = 2;
    g.beginPath(); g.ellipse(cx, cy + 6, 150, 92, 0, 0, TAU); g.stroke();
    g.beginPath(); g.ellipse(cx, cy + 6, 110, 66, 0, 0, TAU); g.stroke();
  }
  // Faint scorch patches for grit. These used to be placed with freeSpot(spawn, 140, 16), and that
  // call can never succeed: the arena centre is guaranteed clear of obstacles out to 150 px (see the
  // `dist(...) < 150` guard in obstacle placement), so every one of the 14 probes fails its radius
  // test and falls through to freeSpot's last-resort return -- the probe centre itself. All 14
  // therefore stacked on the hero's spawn tile and their alpha composited into one opaque near-black
  // ellipse, which is the "dark burnt blob next to where I spawn in every realm". It persisted under
  // the baked floor layer, so nothing could clear it.
  //
  // Two changes: place from the arena's own rng so the scatters differ per realm and per seed, and
  // keep them off the spawn rug, where a burn has no business being anyway. Clearance is tested
  // against the obstacle list directly rather than through freeSpot: freeSpot searches outward, so
  // it answers "there is floor somewhere nearby" and would let a burn be painted on top of the
  // pillar it was supposed to avoid. Each attempt is checked before it is drawn, so a failed probe
  // paints nothing instead of painting at a fallback point.
  {
    const cx = D.spawn.x, cy = D.spawn.y;
    const clearOfProps = (x, y, rr) => {
      for (const o of D.obstacles) if (dist(x, y, o.x, o.y) < o.r + rr * .5) return false;
      return true;
    };
    let placed = 0;
    for (let i = 0; i < 140 && placed < 14; i++) {
      const sx = rng() * D.wpx, sy = rng() * D.hpx;
      const ds = dist(sx, sy, cx, cy);
      if (ds < 215 || ds > 520) continue;                 // grit around the arena, not on the rug
      const rr = 26 + rng() * 40;
      if (D.circleBlocked(sx, sy, rr * .5) || !clearOfProps(sx, sy, rr)) continue;
      const sc = g.createRadialGradient(sx, sy, 2, sx, sy, rr);
      // Half the old centre alpha. At .20 fourteen patches read as soot even when properly spread;
      // at .10 they stay a hint of scorch on the stone.
      sc.addColorStop(0, "rgba(20,8,6,.10)"); sc.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = sc; g.beginPath(); g.ellipse(sx, sy, rr, rr * .6, rng() * TAU, 0, TAU); g.fill();
      placed++;
    }
  }

  // walls: side face + top cap
  for (let ty = 0; ty < D.RH; ty++) for (let tx = 0; tx < D.RW; tx++) {
    if (!D.isWall(tx, ty)) continue;
    const x = tx * TILE, y = ty * TILE;
    const openBelow = D.isFloor(tx, ty + 1);
    g.fillStyle = z.wall; g.fillRect(x, y, TILE, TILE);
    // brick lines
    g.strokeStyle = "rgba(0,0,0,.4)"; g.lineWidth = 2;
    g.strokeRect(x + 1, y + 1, TILE - 2, TILE - 2);
    g.strokeStyle = "rgba(255,255,255,.05)";
    g.beginPath(); g.moveTo(x + 2, y + TILE / 2); g.lineTo(x + TILE - 2, y + TILE / 2); g.stroke();
    if (openBelow) { // top cap facing the room
      const gr = g.createLinearGradient(0, y, 0, y + TILE);
      gr.addColorStop(0, z.wallTop); gr.addColorStop(1, shade(z.wall, -16));
      g.fillStyle = gr; g.fillRect(x, y + TILE * .45, TILE, TILE * .55);
      g.fillStyle = "rgba(0,0,0,.35)"; g.fillRect(x, y + TILE - 5, TILE, 5);
    }
    // ambient occlusion onto adjacent floor
    if (D.isFloor(tx, ty + 1)) {
      const gr = g.createLinearGradient(0, (ty + 1) * TILE, 0, (ty + 1) * TILE + 16);
      gr.addColorStop(0, "rgba(0,0,0,.4)"); gr.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = gr; g.fillRect(x, (ty + 1) * TILE, TILE, 16);
    }
    if (D.isFloor(tx, ty - 1)) {
      const gr = g.createLinearGradient(0, ty * TILE, 0, ty * TILE - 14);
      gr.addColorStop(0, "rgba(0,0,0,.35)"); gr.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = gr; g.fillRect(x, ty * TILE - 14, TILE, 14);
    }
  }

  // obstacles (pillars / rock piles) baked with shadows
  for (const o of D.obstacles) drawBakedPillar(g, o, rng, z);

  // props
  for (const pr of D.decor) drawBakedProp(g, pr.kind, pr.x, pr.y, rng, z);

  return cv;
}

function drawBakedPillar(g, o, rng, z) {
  const { x, y, r } = o;
  // ground shadow
  g.fillStyle = "rgba(0,0,0,.42)";
  g.beginPath(); g.ellipse(x + 5, y + r * .55, r * 1.25, r * .55, 0, 0, TAU); g.fill();
  if (rng() < .5) {
    // stone pillar
    const gr = g.createLinearGradient(x - r, 0, x + r, 0);
    gr.addColorStop(0, shade(z.wallTop, -26)); gr.addColorStop(.45, z.wallTop); gr.addColorStop(1, shade(z.wall, -34));
    g.fillStyle = gr;
    g.beginPath(); g.ellipse(x, y, r, r * .78, 0, 0, TAU); g.fill();
    g.strokeStyle = "rgba(0,0,0,.7)"; g.lineWidth = 3; g.stroke();
    g.fillStyle = shade(z.wallTop, 18);
    g.beginPath(); g.ellipse(x, y - r * .5, r * .92, r * .6, 0, 0, TAU); g.fill();
    g.strokeStyle = "rgba(0,0,0,.55)"; g.lineWidth = 2.5; g.stroke();
    g.strokeStyle = "rgba(0,0,0,.25)"; g.lineWidth = 2;
    g.beginPath(); g.moveTo(x - r * .5, y - r * .3); g.lineTo(x - r * .5, y + r * .4);
    g.moveTo(x + r * .4, y - r * .25); g.lineTo(x + r * .4, y + r * .45); g.stroke();
  } else {
    // rock pile
    for (let i = 0; i < 3; i++) {
      const rx = x + rand(-r * .5, r * .5), ry = y + rand(-r * .35, r * .3);
      const rr = r * rand(.42, .68);
      g.fillStyle = shade(z.wallTop, -14 + rng() * 26);
      g.beginPath(); g.ellipse(rx, ry, rr, rr * .8, rng() * TAU, 0, TAU); g.fill();
      g.strokeStyle = "rgba(0,0,0,.6)"; g.lineWidth = 2.5; g.stroke();
    }
  }
}

// kept for legacy prop drawing compatibility (ui.js/legacy.js reference these)
function drawBakedProp(g, kind, x, y, rng, z) {
  // `rng` must be a function, and must be the same function every frame for a given prop.
  //
  // The rock piles ("rubble" and "skullpile") place each stone with rng(), so whatever generator is
  // passed in defines their shape. drawProp - the dynamic path, used for every prop actually shown -
  // used to hand it Math.random, which is not a seeded generator at all: every fragment and every
  // skull was re-rolled on each of the 60 frames a second, so a pile stood in one spot and vibrated.
  // Passing the same seeded mulberry32 the bake uses (see drawProp) makes the fragments
  // deterministic, so a pile is one fixed arrangement of stones simply redrawn identically each
  // frame. The bake path already passed the level's seeded generator, which is why the baked copies
  // never shook and only the live-drawn ones did.
  //
  // Note rng is called two ways here: rng() for a [0,1) shade value, and rng(a,b) for a bounded
  // position. mulberry32 ignores its arguments and returns [0,1), so callers rely on the wrapper in
  // drawProp / the bake site to supply the bounded form; do not swap in a bare mulberry32 here.
  g.save(); g.translate(x, y);
  switch (kind) {
    case "brazier": {
      g.fillStyle = "rgba(0,0,0,.4)";
      g.beginPath(); g.ellipse(2, 4, 12, 5, 0, 0, TAU); g.fill();
      g.fillStyle = "#3a3230"; g.fillRect(-2.5, -14, 5, 16);
      g.fillStyle = "#2c2624"; g.beginPath(); g.moveTo(-10, -18); g.lineTo(10, -18); g.lineTo(6, -8); g.lineTo(-6, -8); g.closePath(); g.fill();
      g.strokeStyle = "#170a0d"; g.lineWidth = 2; g.stroke();
      g.fillStyle = "#e8781e";
      g.beginPath(); g.moveTo(0, -34); g.quadraticCurveTo(8, -26, 3, -19); g.quadraticCurveTo(0, -23, -4, -19); g.quadraticCurveTo(-8, -26, 0, -34); g.fill();
      g.fillStyle = "#ffc23c";
      g.beginPath(); g.moveTo(0, -30); g.quadraticCurveTo(4, -25, 1, -20); g.quadraticCurveTo(-1, -23, -2, -20); g.quadraticCurveTo(-4, -25, 0, -30); g.fill();
      break;
    }
    case "barrel": {
      g.fillStyle = "rgba(0,0,0,.38)";
      g.beginPath(); g.ellipse(2, 4, 12, 5, 0, 0, TAU); g.fill();
      g.fillStyle = "#6b4a2c";
      g.beginPath(); g.ellipse(0, -10, 10, 12, 0, 0, TAU); g.fill();
      g.strokeStyle = "#241207"; g.lineWidth = 2.5; g.stroke();
      g.strokeStyle = "#3f2c18"; g.lineWidth = 1.6;
      g.beginPath(); g.moveTo(-9, -14); g.lineTo(9, -14); g.moveTo(-10, -7); g.lineTo(10, -7); g.stroke();
      break;
    }
    case "skullpile":
      g.fillStyle = "rgba(0,0,0,.35)";
      g.beginPath(); g.ellipse(0, 3, 16, 6, 0, 0, TAU); g.fill();
      for (let i = 0; i < 2; i++) {
        const sx = rng(-7, 7), sy = rng(-6, 1);
        g.fillStyle = shade("#cfc4b0", -20 + rng() * 20);
        g.beginPath(); g.arc(sx, sy, 5, 0, TAU); g.fill();
      }
      break;
    case "rubble":
      for (let i = 0; i < 3; i++) {
        g.fillStyle = shade(z.wall, -8 + rng() * 20);
        const rx = rng(-10, 10), ry = rng(-4, 3);
        g.beginPath(); g.moveTo(rx, ry - 4); g.lineTo(rx + 5, ry); g.lineTo(rx + 1, ry + 4); g.lineTo(rx - 4, ry + 2); g.closePath(); g.fill();
        g.strokeStyle = "rgba(0,0,0,.5)"; g.lineWidth = 1.6; g.stroke();
      }
      break;
    case "chain":
      g.strokeStyle = "#5a5560"; g.lineWidth = 3; g.lineCap = "round";
      g.beginPath();
      for (let i = 0; i < 4; i++) { g.moveTo(-12 + i * 7, -4 + (i % 2) * 5); g.lineTo(-8 + i * 7, 0 + (i % 2) * 5); }
      g.stroke();
      break;
  }
  g.restore();
}