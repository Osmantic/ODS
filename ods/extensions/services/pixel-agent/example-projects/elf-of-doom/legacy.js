// Elf of Doom — shared stroke & legacy draw helpers (loaded after world.js)
"use strict";

function drawEyes(g, x, y, rx, ry, angry) {
  fillEllipse(g, x - rx * .6, y, rx * .55, ry, "#fff");
  fillEllipse(g, x + rx * .7, y, rx * .55, ry, "#fff");
  fillCircle(g, x - rx * .5, y + .5, ry * .45, "#170a0d");
  fillCircle(g, x + rx * .8, y + .5, ry * .45, "#170a0d");
  if (angry) {
    g.strokeStyle = "#170a0d"; g.lineWidth = 2; g.lineCap = "round";
    g.beginPath();
    g.moveTo(x - rx * 1.15, y - ry * 1.4); g.lineTo(x - rx * .15, y - ry * .8);
    g.moveTo(x + rx * .3, y - ry * .8); g.lineTo(x + rx * 1.3, y - ry * 1.4);
    g.stroke();
  }
}

function strokeCircle(g, x, y, r, fill, lw) {
  fillCircle(g, x, y, r, fill);
  g.lineWidth = lw || 2; g.strokeStyle = "rgba(10,4,6,.9)"; g.stroke();
}
function strokeEllipse(g, x, y, rx, ry, fill, lw) {
  fillEllipse(g, x, y, rx, ry, fill);
  g.lineWidth = lw || 2; g.strokeStyle = "rgba(10,4,6,.9)"; g.stroke();
}
function strokeRect(g, x, y, w, h, fill, lw) {
  g.fillStyle = fill; g.fillRect(x, y, w, h);
  g.lineWidth = lw || 2; g.strokeStyle = "rgba(10,4,6,.9)"; g.strokeRect(x, y, w, h);
}

// legacy parallax helpers (top-down mode renders the baked dungeon instead)
function drawMountains(g, W, H, camX, zone, time) {
  const z = ZONES[typeof zone === "number" ? zone : 0] || ZONES[0];
  const px = typeof camX === "number" ? camX : 0;
  const t = typeof time === "number" ? time : 0;
  g.save();
  for (let layer = 0; layer < 2; layer++) {
    const off = px * (0.06 + layer * 0.05);
    g.fillStyle = layer ? shade(z.wall, -6) : shade(z.wall, -16);
    g.beginPath(); g.moveTo(0, H);
    const base = H * (0.3 + layer * 0.12);
    for (let x = 0; x <= W; x += 40) {
      const n = Math.sin((x + off) * .01 + layer * 7) * 26 + Math.sin((x + off) * .037) * 12;
      g.lineTo(x, base + n);
    }
    g.lineTo(W, H); g.closePath(); g.fill();
  }
  const grad = g.createLinearGradient(0, 0, 0, H * .5);
  grad.addColorStop(0, hexA(z.accent, .10)); grad.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grad; g.fillRect(0, 0, W, H * .5);
  g.fillStyle = "rgba(200,190,180,.22)";
  for (let i = 0; i < 30; i++) {
    const x = (i * 137 + t * .008 * (10 + i % 7) - px * .1) % (W + 40) - 20;
    const y = (i * 71 + t * .02 * (1 + (i % 5))) % H;
    g.fillRect(x, y, 2, 2);
  }
  g.restore();
}

function drawGround(g, W, H, camX, camY, zone) {
  const z = ZONES[typeof zone === "number" ? zone : 0] || ZONES[0];
  const cx = typeof camX === "number" ? camX : 0, cy = typeof camY === "number" ? camY : 0;
  g.save(); g.translate(-cx, -cy);
  const x0 = Math.floor(cx / 48) * 48, y0 = Math.floor(cy / 48) * 48;
  for (let x = x0; x < cx + W + 48; x += 48)
    for (let y = y0; y < cy + H + 48; y += 48) {
      const n = ((x * 7 + y * 13) % 17) / 17;
      g.fillStyle = shade(z.floor, -10 + n * 18);
      g.fillRect(x, y, 48, 48);
      g.strokeStyle = "rgba(0,0,0,.25)"; g.lineWidth = 1;
      g.strokeRect(x + .5, y + .5, 47, 47);
    }
  g.restore();
}

function drawProp(g, kind, x, y, time) {
  // Braziers are baked into the static arena layer only. Drawing them again here at the same
  // coordinates stacked a second copy over the baked one, and the two copies are NOT identical:
  // bakeArena passes the live RNG so the flame flicker differs per prop and per bake, while this
  // path seeded a deterministic RNG from x/y. That offset double image is why the firelight never
  // lined up with the fire pits. Only the live-animated kinds (web, torch) are drawn dynamically.
  if (kind === "brazier") return;
  // G.zone counts up through the endless loop (realm 7 is index 6), so it has to be wrapped back
  // into ZONES or this lookup returns undefined past the first circuit.
  const z = ZONES[(typeof G !== "undefined" && G) ? realmIndex(G.zone) : 0] || ZONES[0];
  g.save(); g.translate(x, y);
  // Seeded from the prop's own position, so the same pile draws identically every frame. This was
  // Math.random(), which re-rolled every fragment and skull 60 times a second - the rock piles
  // jittered in place. Seeded deterministically rather than passed through the level's bake RNG on
  // purpose: bakeArena consumes its shared generator for tiles, cracks and stains in a sequence, so
  // reusing it here would advance that sequence a different number of times depending on which props
  // were on screen and in what cull order, re-rolling the whole floor from frame to frame. A
  // position-seeded stream depends only on this prop, which is what makes it stable.
  //
  // Wrapped the way the bake site wraps its own generator (see bakeDecor), because drawBakedProp
  // calls rng two ways: rng() for a [0,1) shade value and rng(a,b) for a bounded offset. Bare
  // mulberry32 ignores its arguments, so passing it un-wrapped would collapse both rubble offsets to
  // a[0,1) and stack every fragment at the top-left of the pile - stable, but the wrong shape.
  const s = mulberry32(Math.floor(x * 131 + y * 17));
  const rng = (a, b) => (a === undefined ? s() : a + s() * (b - a));
  drawBakedProp(g, kind, 0, 0, rng, z);
  g.restore();
}

function drawPickupIcon(g, kind, ico, x, y) {
  g.save(); g.translate(x, y);
  strokeCircle(g, 0, 0, 9, kind === "gold" ? "#e8b62a" : "#a02a2a", 2);
  g.font = "11px sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillStyle = "#fff"; g.fillText(ico, 0, 1);
  g.restore();
}

// ---------- arrows (projectiles + pickups) ----------
function drawArrowProj(g, pr) {
  g.save(); g.translate(pr.x, pr.y); g.rotate(pr.ang);
  g.strokeStyle = "#caa15a"; g.lineWidth = 2.5; g.lineCap = "round";
  g.beginPath(); g.moveTo(-14, 0); g.lineTo(8, 0); g.stroke();
  g.fillStyle = "#d8dde2"; // head
  g.beginPath(); g.moveTo(13, 0); g.lineTo(6, -3.4); g.lineTo(6, 3.4); g.closePath(); g.fill();
  g.strokeStyle = "#d8dde2"; g.lineWidth = 1.5;
  g.beginPath(); g.moveTo(-14, 0); g.lineTo(-18, -3.5); g.moveTo(-14, 0); g.lineTo(-18, 3.5); g.stroke();
  g.restore();
}
// Quiver: the static art (leather, three shafts, heads) is baked once into PICK_SPRITES.quiver
// below; only the sparkle's pulsing alpha stays live here.
// Order matters: quiver first, sparkle on top — the way the original procedural version drew the
// leather and shafts and then laid the pulsing glow over them. Blitting the sparkle underneath
// let the leather dim the glow, and the bundle read darker than it used to.
function drawArrowPickup(g, x, y, t) {
  g.save(); g.translate(x, y);
  const q = PICK_SPRITES.quiver;
  g.drawImage(q, -q.ox, -q.oy);
  g.globalAlpha = .5 + Math.sin(t * .006) * .2;
  g.drawImage(ARROW_SPARKLE, -24, -24);
  g.globalAlpha = 1;
  g.restore();
}
// cached sparkle sprite for arrow bundles (was a per-frame radial gradient)
var ARROW_SPARKLE = (function () {
  const c = document.createElement("canvas"); c.width = c.height = 48;
  const x = c.getContext("2d");
  const grd = x.createRadialGradient(24, 24, 2, 24, 24, 22);
  grd.addColorStop(0, "rgba(220,230,240,.5)"); grd.addColorStop(1, "rgba(220,230,240,0)");
  x.fillStyle = grd; x.beginPath(); x.arc(24, 24, 22, 0, Math.PI * 2); x.fill();
  return c;
})();

// ---------- pickup sprites (baked once at load, blitted per frame) ----------
// Same trick as the enemy sprite cache in chars.js, and for the same reason: the blurred heart
// glow was a live g.shadowBlur, which Chromium re-rasterizes for every shape it draws, so every
// heart on the floor paid a full blur pass every frame. Here the entire static art of each pickup
// — heart with its shadowBlur glow, gold coin, quiver — is drawn once into an offscreen canvas at
// startup, and the per-frame path is one drawImage. Motion stays live: the bob is a translation
// applied by drawPickup in ui.js, and the quiver sparkle above is an alpha on its own cached blit.
// Only the shape lives in the bake, so nothing about drop positions, timers or values changes.
const PICK_SPRITE_PAD = 20;   // room for the heart's glow beyond the drawn art
const PICK_HEART_BLUR = 10;   // the heart's shadowBlur, baked in rather than live
// box: [minX, minY, maxX, maxY] of the art in the pickup's own coordinates (origin at pk.x/pk.y,
// the art drawn above it). min/max are what the bake canvas is sized from, so no ink is clipped;
// the canvas origin then lands at (ox, oy) inside it and the blit subtracts those back out.
function pickSpriteBake(box, draw) {
  const x0 = Math.floor(box[0]) - PICK_SPRITE_PAD, y0 = Math.floor(box[1]) - PICK_SPRITE_PAD;
  const x1 = Math.ceil(box[2]) + PICK_SPRITE_PAD, y1 = Math.ceil(box[3]) + PICK_SPRITE_PAD;
  const c = document.createElement("canvas");
  c.width = Math.max(1, x1 - x0); c.height = Math.max(1, y1 - y0);
  const x = c.getContext("2d");
  // Whole-pixel translate: the bake context starts exactly on integer offsets, so every command
  // lands where the old per-frame drawing put it and the sprite is pixel-identical to before.
  x.save(); x.translate(-x0, -y0);
  draw(x);
  x.restore();
  c.ox = -x0; c.oy = -y0;
  return c;
}
var PICK_SPRITES = (function () {
  const sp = {};
  sp.heart = pickSpriteBake([-9.8, -13.8, 9.8, 2.8], function (x) {
    // glowing drawn heart (crisp at any zoom, unlike an emoji)
    x.shadowColor = "rgba(255,70,70,.9)"; x.shadowBlur = PICK_HEART_BLUR;
    x.fillStyle = "#d92c38"; x.strokeStyle = "#3a070c"; x.lineWidth = 1.6;
    x.beginPath();
    x.moveTo(0, 2);
    x.bezierCurveTo(-9, -5, -6.5, -13, -1.5, -11.5);
    x.bezierCurveTo(-.4, -11.2, -.4, -10.6, 0, -10.2);
    x.bezierCurveTo(.4, -10.6, .4, -11.2, 1.5, -11.5);
    x.bezierCurveTo(6.5, -13, 9, -5, 0, 2);
    x.closePath(); x.fill(); x.stroke();
    x.shadowBlur = 0;
    x.fillStyle = "rgba(255,255,255,.55)";
    x.beginPath(); x.ellipse(-3, -8.6, 1.8, 1.1, -.5, 0, TAU); x.fill();
  });
  sp.gold = pickSpriteBake([-9.4, -15.4, 9.4, 3.4], function (x) {
    strokeCircle(x, 0, -6, 8, "#e8b62a", 2);
    strokeCircle(x, 0, -6, 4.5, "#ffd76b", 1.4);
    x.fillStyle = "#7a5210"; x.font = "bold 8px sans-serif"; x.textAlign = "center";
    x.fillText("S", 0, -3.4);
  });
  sp.quiver = pickSpriteBake([-13.4, -17.6, 13.4, 16.4], function (x) {
    // quiver of arrows
    fillEllipse(x, 0, 6, 12, 9, "#6b4a26");
    x.strokeStyle = "rgba(10,4,6,.9)"; x.lineWidth = 2; x.stroke();
    for (let i = -1; i <= 1; i++) {
      x.save(); x.translate(i * 5, -4); x.rotate(i * .18 - .1);
      x.strokeStyle = "#caa15a"; x.lineWidth = 2; x.beginPath(); x.moveTo(0, 2); x.lineTo(0, -12); x.stroke();
      x.fillStyle = "#d8dde2"; x.beginPath(); x.moveTo(0, -16); x.lineTo(-3, -9); x.lineTo(3, -9); x.closePath(); x.fill();
      x.restore();
    }
  });
  return sp;
})();