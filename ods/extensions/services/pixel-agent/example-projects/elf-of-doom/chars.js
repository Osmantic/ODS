// Elf of Doom — characters (cartoon canvas art)
"use strict";

// ---------- hero: Shasan with elven bow ----------
function drawElf(g, p, time) {
  const s = p.face; // 1 right, -1 left
  const moving = p.moving;
  const legs = moving ? legSwing(time, .022) : [0, 0];
  const bob = moving ? Math.abs(Math.sin(time * .011)) * 2 : Math.sin(time * .003) * 1.2;

  // rolling ball pose
  if (p.rollT > 0) {
    g.save(); g.translate(0, -14); g.rotate(p.rollAng);
    strokeCircle(g, 0, 0, 16, "#e8d9b0"); // blond hair ball
    strokeCircle(g, 5 * s, -3, 5, "#3f7d5f", 1.8); // cloak lump
    strokeCircle(g, 7 * s, 2, 3.5, "#f4d3a6", 1.5); // hand poke
    g.restore(); return;
  }

  g.save(); g.translate(0, -bob); g.scale(s, 1);

  // feet (elven boots)
  inkLine(g, [[-5, -14], [-7 + legs[0] * .4, -3]], "#7a6a4a", 4.5);
  inkLine(g, [[5, -14], [7 + legs[1] * .4, -3]], "#7a6a4a", 4.5);
  strokeEllipse(g, -8 + legs[0] * .4, -1.5, 5.5, 3, "#5d4a2e", 2);
  strokeEllipse(g, 8 + legs[1] * .4, -1.5, 5.5, 3, "#5d4a2e", 2);

  // cloak behind
  g.beginPath(); g.moveTo(-3, -46); g.quadraticCurveTo(-22, -32, -14, -10);
  g.quadraticCurveTo(-5, -14, -1, -16); g.closePath();
  g.fillStyle = "#2f5d47"; g.fill(); g.lineWidth = 2.5; g.strokeStyle = "#12241c"; g.stroke();

  // body / elven tunic
  strokeEllipse(g, 0, -22, 11, 12.5, "#cfe0c8"); // soft linen
  strokeEllipse(g, 0, -24, 10, 10.5, "#3f7d5f"); // green tunic
  strokeEllipse(g, 0, -20, 6.5, 7, "#dfe9d4", 1.6); // inner fold
  strokeEllipse(g, 0, -13, 9.5, 3.2, "#6b4a26", 1.6); // belt
  strokeCircle(g, 0, -13, 2.2, "#e8b62a", 1.2); // gold clasp
  // quiver on back
  g.save(); g.translate(-9, -30); g.rotate(-.35);
  strokeRect(g, -3.5, -12, 7, 22, "#6b4a26", 2);
  for (let i = -1; i <= 1; i++) { inkLine(g, [[i * 2, -12], [i * 2.6, -18]], "#caa15a", 1.6); }
  g.restore();

  // arms + bow, aimed along the shoot direction.
  //
  // We are inside scale(s, 1) above, so the local x-axis already points along `face`:
  // forward = cos(shootAng)*s. The old code rotated by the raw shootAng here, so when facing
  // left the flip mirrored the rotation and the bow pointed 180deg away from the shot - shooting
  // left aimed right. (Shooting straight up/down happened to look fine only because cos == 0.)
  // Rotating by the mirror-corrected angle keeps the aim true in both facings, and the upright
  // flip below keeps the bow's belly and string from inverting.
  const drawP = p.atkT > 0 ? Math.sin((1 - p.atkT / p.atkDur) * Math.PI) : 0; // 0..1 draw anim
  // Same PI guard as the reticle: `|| 0` turned an aim-left angle of PI into 0 via string
  // truthiness, so the bow pointed right when it should point left. Falls back to the facing.
  const sa = Number.isFinite(p.shootAng) ? p.shootAng : (s >= 0 ? 0 : Math.PI);
  const aim = Math.atan2(Math.sin(sa), Math.cos(sa) * s);
  g.save(); g.translate(2, -27); g.rotate(aim); // +x is "forward"
  if (s < 0) g.scale(1, -1); // keep bow upright when facing left
  // bow arm forward
  inkLine(g, [[0, 0], [13 + drawP * 2, -1]], "#f4d3a6", 4);
  // elven bow at hand
  g.save(); g.translate(15 + drawP * 2, -1);
  g.strokeStyle = "#d9c27a"; g.lineWidth = 2.6; g.lineCap = "round";
  g.beginPath(); g.moveTo(0, -12); g.quadraticCurveTo(7, 0, 0, 12); g.stroke();
  g.strokeStyle = "#8a7550"; g.lineWidth = 1.6; // grip wrap
  g.beginPath(); g.moveTo(2.4, -3); g.lineTo(2.4, 3); g.stroke();
  // string, pulled by draw
  g.strokeStyle = "rgba(240,240,230,.95)"; g.lineWidth = 1.2;
  g.beginPath(); g.moveTo(0, -12); g.lineTo(-5 * drawP, 0); g.lineTo(0, 12); g.stroke();
  // nocked arrow while drawing
  if (drawP > .05 && p.arrows > 0) {
    g.strokeStyle = "#caa15a"; g.lineWidth = 1.8;
    g.beginPath(); g.moveTo(-5 * drawP, 0); g.lineTo(14, 0); g.stroke();
    g.fillStyle = "#d8dde2"; g.beginPath(); g.moveTo(18, 0); g.lineTo(12, -2.6); g.lineTo(12, 2.6); g.closePath(); g.fill();
  }
  g.restore();
  // draw hand pulls string back
  inkLine(g, [[0, 1], [6 - drawP * 10, 2 - drawP * 1]], "#f4d3a6", 3.6);
  strokeCircle(g, 5 - drawP * 10, 2 - drawP, 2.6, "#f4d3a6", 1.3);
  g.restore();

  // head: fair elf, long blond hair, pointed ear
  strokeCircle(g, 1, -43, 11.5, "#f4d3a6");
  for (let i = 0; i < 8; i++) { // flowing blond hair
    const a = -Math.PI * 1.02 + i * .3;
    strokeCircle(g, 1 + Math.cos(a) * 10.5, -45 + Math.sin(a) * 8.5, 4, "#e8d9b0", 1.5);
  }
  strokeCircle(g, -8, -40, 4.2, "#e8d9b0", 1.4); // hair behind
  inkLine(g, [[-9, -44], [-16, -48]], "#e8d9b0", 3); // long strand
  // pointed ear
  g.beginPath(); g.moveTo(9, -44); g.lineTo(16, -48); g.lineTo(10, -40); g.closePath();
  g.fillStyle = "#f4d3a6"; g.fill(); g.lineWidth = 1.6; g.strokeStyle = "rgba(10,4,6,.8)"; g.stroke();
  drawEyes(g, 3, -45, 4.3, 3, p.atkT > 0);
  strokeCircle(g, 8, -41.5, 1.7, "#e0a878", 1); // nose
  if (p.atkT > 0) { g.fillStyle = "#170a0d"; g.beginPath(); g.ellipse(4, -37, 2.6, 1.8, 0, 0, TAU); g.fill(); }
  else inkLine(g, [[1, -37], [6, -36.5]], "#170a0d", 1.6);
  // slim circlet
  g.strokeStyle = "#c9a13a"; g.lineWidth = 1.6;
  g.beginPath(); g.arc(1, -44, 10.5, Math.PI * 1.15, Math.PI * 1.85); g.stroke();

  g.restore();
}
// ---------- ridgeback grunt / ironjaw ----------
function drawBrute(g, e, time, ironjaw) {
  const s = e.face, legs = legSwing(time + e.wob, .017);
  g.save(); g.scale(s, 1);
  inkLine(g, [[-5, -13], [-7 + legs[0] * .4, -2]], "#2e3a26", 5);
  inkLine(g, [[5, -13], [7 + legs[1] * .4, -2]], "#2e3a26", 5);
  strokeEllipse(g, -8 + legs[0] * .4, -1, 5, 3, "#232d1d", 2);
  strokeEllipse(g, 8 + legs[1] * .4, -1, 5, 3, "#232d1d", 2);
  strokeEllipse(g, 0, -21, ironjaw ? 13 : 11, ironjaw ? 13 : 11, ironjaw ? "#5d6b4a" : "#6f8a4a"); // hunched body
  strokeEllipse(g, 0, -18, 7.5, 7, ironjaw ? "#49563a" : "#5a7239", 1.8);                    // belly armor
  const sw = e.atkT > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
  inkLine(g, [[5, -26], [9 + sw * 8, -30 - sw * 5]], ironjaw ? "#5d6b4a" : "#6f8a4a", 4.5);
  // scimitar
  g.save(); g.translate(10 + sw * 8, -31 - sw * 5); g.rotate(.7 - sw * 2.6);
  g.beginPath(); g.moveTo(0, 0); g.quadraticCurveTo(4, -12, -2, -20); g.quadraticCurveTo(1, -10, -2.5, -1); g.closePath();
  g.fillStyle = "#9aa3ab"; g.fill(); g.lineWidth = 1.8; g.strokeStyle = "#170a0d"; g.stroke(); g.restore();
  inkLine(g, [[-6, -26], [-11, -19]], ironjaw ? "#5d6b4a" : "#6f8a4a", 4.5);
  // head
  const hy = ironjaw ? -38 : -35;
  strokeEllipse(g, 1, hy, ironjaw ? 11 : 10, ironjaw ? 9.5 : 8.5, ironjaw ? "#5d6b4a" : "#7d9a55");
  strokeEllipse(g, 6, hy + 3, 4.5, 3.2, ironjaw ? "#49563a" : "#5a7239", 1.5); // snout
  g.fillStyle = "#fff"; // tusks
  g.beginPath(); g.moveTo(4, hy + 5); g.lineTo(5.5, hy + 1); g.lineTo(6.6, hy + 5.4); g.fill();
  g.beginPath(); g.moveTo(8, hy + 5); g.lineTo(9.5, hy + 1.4); g.lineTo(10.4, hy + 5.2); g.fill();
  drawEyes(g, 2, hy - 2, 4, 2.7, true);
  if (ironjaw) { // iron helm
    g.beginPath(); g.arc(1, hy - 2, 11, Math.PI, 0); g.closePath();
    g.fillStyle = "#4c4e57"; g.fill(); g.lineWidth = 2.2; g.strokeStyle = "#170a0d"; g.stroke();
    inkLine(g, [[1, hy - 13], [1, hy - 18]], "#4c4e57", 3);
  }
  g.restore();
}

// ---------- brute archer ----------
// The archer, in three pieces. drawBruteArcher calls them in this order and is what it always was;
// the split exists for the sprite cache, which used to bake the whole figure once per aim heading and
// so re-baked a tracking archer almost every frame (see SPRITE_AIM_STEPS). Only the bow arm turns
// with the aim, so only the bow arm is keyed by it now: the body bakes per facing/frame/attack step,
// the head per facing, the arm per attack step at rest (aim 0, unrotated), and the live blit stamps
// body and arm and head — the arm under the same shoulder translate/rotate/flip the middle block has
// always used. Each piece carries its own save/restore and draws nothing outside itself, so a piece
// is baked by calling *it*, never by setting a flag that hides parts of the whole figure.
function drawBruteArcher(g, e, time) {
  drawBruteArcherBody(g, e, time);
  drawBruteArcherBowArm(g, e, time);
  drawBruteArcherHead(g, e, time);
}

// Legs, torso and the back quiver. No aim anywhere in here: the quiver is body-relative.
function drawBruteArcherBody(g, e, time) {
  const s = e.face, legs = legSwing(time + e.wob, .016);
  g.save(); g.scale(s, 1);
  inkLine(g, [[-5, -13], [-7 + legs[0] * .4, -2]], "#33301f", 5);
  inkLine(g, [[5, -13], [7 + legs[1] * .4, -2]], "#33301f", 5);
  strokeEllipse(g, -8 + legs[0] * .4, -1, 5, 3, "#26231a", 2);
  strokeEllipse(g, 8 + legs[1] * .4, -1, 5, 3, "#26231a", 2);
  strokeEllipse(g, 0, -21, 11, 11, "#7a6f45"); // lean bowman body
  strokeEllipse(g, 0, -18, 7, 6.5, "#5c5433", 1.8);
  // quiver on back
  g.save(); g.translate(-9, -28); g.rotate(-.32);
  strokeRect(g, -3.5, -10, 7, 19, "#5a3f22", 2);
  for (let i = -1; i <= 1; i++) inkLine(g, [[i * 2, -10], [i * 2.6, -16]], "#b7975a", 1.6);
  g.restore();
  g.restore();
}

// The bow arm, bow, string and nocked arrow: the limb that tracks the target. `e.aimAng` is applied
// *outside* the drawing, in this same block, exactly as before — the shoulder pivot, the aim rotation
// and the left-facing flip all stay here so the live path and the cached blit cannot drift apart. A
// bake passes aimAng 0 and gets the unrotated rest arm; the blit re-creates this transform with the
// live heading. It reads e.face only for the flip, and draws no body geometry of its own.
function drawBruteArcherBowArm(g, e, time) {
  const s = e.face;
  const drawP = e.atkT > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
  g.save(); g.translate(SPRITE_ARCHER_ARM_ANCHOR[0], SPRITE_ARCHER_ARM_ANCHOR[1]); g.rotate(e.aimAng || 0);
  if (s < 0) g.scale(1, -1);
  inkLine(g, [[0, 0], [12 + drawP * 2, -1]], "#7a6f45", 4);
  g.save(); g.translate(14 + drawP * 2, -1);
  g.strokeStyle = "#7a5a30"; g.lineWidth = 2.4; g.lineCap = "round";
  g.beginPath(); g.moveTo(0, -11); g.quadraticCurveTo(6.5, 0, 0, 11); g.stroke();
  g.strokeStyle = "rgba(230,225,205,.9)"; g.lineWidth = 1.1;
  g.beginPath(); g.moveTo(0, -11); g.lineTo(-4.5 * drawP, 0); g.lineTo(0, 11); g.stroke();
  if (drawP > .05) {
    g.strokeStyle = "#b7975a"; g.lineWidth = 1.7;
    g.beginPath(); g.moveTo(-4.5 * drawP, 0); g.lineTo(12, 0); g.stroke();
    g.fillStyle = "#b8bec4"; g.beginPath(); g.moveTo(16, 0); g.lineTo(10, -2.4); g.lineTo(10, 2.4); g.closePath(); g.fill();
  }
  g.restore();
  inkLine(g, [[0, 1], [5 - drawP * 8, 2]], "#7a6f45", 3.4);
  g.restore();
}

// The other arm, head, tusks, eyes and leather cap. Baked per facing, never per aim: it takes no
// aimAng, so an aiming archer's head cannot churn the cache.
function drawBruteArcherHead(g, e, time) {
  const s = e.face;
  // other arm + head
  g.save(); g.scale(s, 1);
  inkLine(g, [[-6, -26], [-11, -19]], "#7a6f45", 4.5);
  strokeEllipse(g, 1, -35, 9.5, 8.2, "#87794e");
  strokeEllipse(g, 6, -32, 4.2, 3, "#655a38", 1.5);
  g.fillStyle = "#fff"; // tusks
  g.beginPath(); g.moveTo(4, -30); g.lineTo(5.5, -33.5); g.lineTo(6.6, -29.6); g.fill();
  g.beginPath(); g.moveTo(8, -30); g.lineTo(9.4, -33.2); g.lineTo(10.3, -29.8); g.fill();
  drawEyes(g, 2, -37, 4, 2.7, true);
  // leather cap
  g.beginPath(); g.arc(1, -37, 10, Math.PI, 0); g.closePath();
  g.fillStyle = "#4a3a24"; g.fill(); g.lineWidth = 2; g.strokeStyle = "#170a0d"; g.stroke();
  g.restore();
}

// ---------- fell-bat ----------
// The bat's y-offset splits in two. The constant lift (SPRITE_BAT_LIFT) is part of the silhouette
// and gets baked into the cell. The ±4 px hover (SPRITE_BAT_BOB) is motion, not shape, so it is
// applied when the cell is drawn — see the note above SPRITE_pose for why baking it made the bob
// jump in step with the wing frames instead of gliding. The flap is real wing geometry and is
// untouched: same .03 time rate, same wob phase, so a cached bat beats its wings like an uncached
// one. SPRITE_BAT_LIFT and SPRITE_batBob are declared with the cache below, where both are read.
const SPRITE_BAT_BOB = 4;
function drawBat(g, e, time) {
  const flap = Math.sin(time * .03 + e.wob) * .8;
  // One translate, same order relative to g.scale as the original. scale(-1, 1) leaves a pure
  // y-translation alone, so this is numerically identical to the previous expression at either
  // facing. During a bake SPRITE_batBob returns 0, keeping the hover out of the frame.
  g.save(); g.translate(0, SPRITE_BAT_LIFT + SPRITE_batBob(e, time)); g.scale(e.face, 1);
  for (const dir of [-1, 1]) {
    g.save(); g.scale(1, 1); g.rotate(dir > 0 ? flap * .5 : -flap * .5);
    g.beginPath(); g.moveTo(0, -4);
    g.quadraticCurveTo(dir * 14, -16 - flap * 8, dir * 26, -6 - flap * 10);
    g.quadraticCurveTo(dir * 16, -2, dir * 20, 6 - flap * 4);
    g.quadraticCurveTo(dir * 10, 0, 0, 4); g.closePath();
    g.fillStyle = "#3a2338"; g.fill(); g.lineWidth = 2.2; g.strokeStyle = "#170a0d"; g.stroke(); g.restore();
  }
  strokeEllipse(g, 0, 0, 7, 8, "#4a2d45");
  strokeCircle(g, 0, -6, 5.5, "#3a2338", 2);
  inkLine(g, [[-3, -10], [-5, -15]], "#3a2338", 2.4); inkLine(g, [[3, -10], [5, -15]], "#3a2338", 2.4);
  g.fillStyle = "#ffd76b"; g.beginPath(); g.arc(-2, -6, 1.4, 0, TAU); g.arc(2.4, -6, 1.4, 0, TAU); g.fill();
  g.restore();
}

// ---------- ashland spider ----------
function drawSpider(g, e, time) {
  g.save(); g.translate(0, -14);
  g.strokeStyle = "#170a0d"; g.lineWidth = 3; g.lineCap = "round";
  for (const dir of [-1, 1]) for (let i = 0; i < 4; i++) {
    const ph = Math.sin(time * .014 + i * 1.6 + e.wob) * 4;
    g.beginPath(); g.moveTo(dir * 6, -6 + i * 3);
    g.quadraticCurveTo(dir * (16 + i * 2), -18 - i * 2 + ph, dir * (22 + i * 3), -2 + i * 3 + ph * .5);
    g.stroke();
  }
  strokeEllipse(g, 0, -10, 13, 11, "#3a2540");                       // abdomen
  g.fillStyle = "rgba(200,160,255,.5)"; g.beginPath();               // marking
  g.moveTo(0, -18); g.lineTo(4, -10); g.lineTo(0, -3); g.lineTo(-4, -10); g.closePath(); g.fill();
  strokeCircle(g, 0, -22, 7, "#2a1a30", 2);                          // head
  g.fillStyle = "#ff4a6a";
  for (const [dx, dy] of [[-3, -1], [3, -1], [-4.5, 2], [4.5, 2]]) { g.beginPath(); g.arc(dx, -22 + dy, 1.5, 0, TAU); g.fill(); }
  g.restore();
}

// ---------- boss variants ----------
function drawFenris(g, e, time) {
  // A boss is drawn larger, and this routine is shared by the pack hound and Skarnax, so the
  // enlargement happens here on e.scale rather than in the caller.
  //
  // The boss wolf looked no bigger than his pack because `scale` is consumed only by shadow()
  // and the hit flash; nothing in the character renderers reads it. The troll boss only *looked*
  // big because its hand-authored ellipse coordinates happen to be large, not because scale was
  // doing anything - which is exactly why the wolf boss, drawn on the pack wolf's coordinates,
  // arrived pack-sized.
  //
  // Scaling is about the feet, not the body centre: the anchor is the sprite origin, so the paws
  // stay planted on the floor instead of the giant wolf sinking through it or hovering.
  const zoom = e.boss ? (e.scale || 1) : 1;
  // Bite pose: shared by the renderer and fillEnemySilhouette so the flash always covers the open
  // jaw. Same formula as the swing curve used by the attack timer in updateEnemy (sin of the
  // normalised attack progress).
  const bite = e.atkT > 0 && e.atkDur > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
  // Quadruped wolf: low slung body, long muzzle, erect ears, bushy tail, four gaited legs.
  // Deliberately nothing like drawBrute's upright biped + helmet, which is what the hound used to
  // render as because the dispatcher never routed the hound kind here.
  const s = e.face, legs = legSwing(time + e.wob, .026);
  g.save(); g.scale(s * zoom, zoom);
  // Boss-only presence, drawn first so the body layers over it: heavier legs, a taller bristling
  // spine crest, a darker belly and an iron collar with rings. Those are the marks that separate
  // him from the pack once he is scaled up. It used to be a dark mantle ellipse *behind* the
  // silhouette, and that is what put a solid opaque shape outside the wolf's outline for a hit
  // flash to catch, so all boss differentiation is now attached to the body.
  if (e.boss) {
    g.lineCap = "round";
    for (let i = 0; i < 4; i++) {
      const lx = -15 + i * 10, sw2 = legs[i % 2] * (i < 2 ? 1 : -1) * .6;
      inkLine(g, [[lx, -18], [lx + sw2, -2]], "#211b20", 8);
      inkLine(g, [[lx + sw2, -3], [lx + sw2 + 3.4, -1]], "#171317", 5.5);   // broad paw
    }
    // ridge along the spine and a heavier dewlap, so the outline itself is different at a glance
    for (let i = 0; i < 9; i++) inkLine(g, [[-18 + i * 4.5, -33], [-21 + i * 4.5, -49 - (i % 2) * 6]], "#241c22", 3.8);
    for (let i = 0; i < 5; i++) inkLine(g, [[-16 + i * 7, -26], [-19 + i * 7, -33]], "#2b2229", 3);
    // low, heavier bellyline, which is what reads as mass on a quadruped
    fillEllipse(g, -1, -21, 19, 7.5, "#2b2229");
    // iron collar where the head meets the ruff
    g.save(); g.translate(13, -33); g.rotate(-.22);
    strokeRect(g, -4, -3.4, 8, 6.8, "#4a4d55", 2);
    g.fillStyle = "#c8a24a";
    g.beginPath(); g.arc(0, 4.4, 2.6, 0, TAU); g.fill();
    g.restore();
  }
  // four legs with a proper dog/wolf gait: front pair opposite to rear pair
  for (let i = 0; i < 4; i++) {
    const lx = -15 + i * 10, sw2 = legs[i % 2] * (i < 2 ? 1 : -1) * .6;
    inkLine(g, [[lx, -18], [lx + sw2, -2]], "#332b31", 5.5);
    inkLine(g, [[lx + sw2, -3], [lx + sw2 + 3, -1]], "#241e22", 4);   // paw
  }
  // bushy tail, raised and bristling
  inkLine(g, [[-20, -28], [-33, -35]], "#463a42", 6);
  inkLine(g, [[-33, -35], [-39, -42]], "#3a3038", 5);
  for (let i = 0; i < 4; i++) inkLine(g, [[-26 - i * 3, -32 - i], [-31 - i * 3, -39 - i]], "#33292f", 2.2);
  // long low body
  strokeEllipse(g, 0, -25, 23, 12, "#463a42");
  strokeEllipse(g, -2, -27, 15, 7, "#5a4a55", 1.8);
  // shaggy spine
  for (let i = 0; i < 7; i++) inkLine(g, [[-16 + i * 5, -34], [-18 + i * 5, -41 - (i % 2) * 3]], "#33292f", 2.6);
  // haunch and shoulder masses so it reads as an animal, not a blob
  strokeEllipse(g, -13, -22, 8, 9, "#4f4149", 1.6);
  strokeEllipse(g, 12, -23, 8, 9, "#4f4149", 1.6);
  if (e.boss) {
    // Scarred boss markings, added after the body so they sit on the fur rather than beside it:
    // a pale streak across the flank and two older healed scars on the shoulder. Drawn inboard of
    // the barrel edge, because the hit flash is the filled silhouette and any mark past it would
    // strobe on empty floor.
    g.strokeStyle = "rgba(214,200,190,.5)"; g.lineWidth = 2.4; g.lineCap = "round";
    g.beginPath(); g.moveTo(-9, -30); g.lineTo(1, -20); g.stroke();
    g.strokeStyle = "rgba(198,180,170,.34)"; g.lineWidth = 1.6;
    g.beginPath(); g.moveTo(7, -27); g.lineTo(13, -22); g.stroke();
    g.beginPath(); g.moveTo(10, -28); g.lineTo(15, -24); g.stroke();
    // pale guard hairs along the crest, kept on the upper barrel so the silhouette gets a lit edge
    // rather than a halo; y = -32 is inside the barrel (centre -25, half-height 12)
    for (let i = 0; i < 4; i++) inkLine(g, [[-14 + i * 8, -30], [-17 + i * 8, -32.5]], "rgba(150,132,144,.6)", 1.5);
  }
  // neck and wedge-shaped wolf head with a long muzzle
  strokeEllipse(g, 17, -32, 9, 8, "#514249");
  strokeEllipse(g, 25, -30, 9, 5.5, "#3f363d", 1.8);                  // muzzle
  strokeEllipse(g, 33, -31, 2.6, 2.2, "#241e22", 1.2);                // nose
  strokeEllipse(g, 12, -40, 4.5, 6.5, "#463a42", 1.8);                // erect ear
  strokeEllipse(g, 20, -41, 4, 5.5, "#463a42", 1.8);                  // second ear
  // ruff around the neck
  for (let i = 0; i < 4; i++) inkLine(g, [[8 + i * 3, -36], [5 + i * 3, -43 - (i % 2) * 2]], "#5a4a55", 2.2);
  if (e.boss) {
    // A brow ridge and a heavier jowl: the head is where "ancient hound" reads, and at boss scale a
    // plain pack head on a big body looked like a copy-paste. Both strokes stay inside the head and
    // muzzle ellipses (centre 17,-32 r 9x8 and 25,-30 r 9x5.5) so the flash silhouette still
    // covers every boss pixel.
    inkLine(g, [[15, -37], [25, -35]], "#2f272d", 2.4);
    inkLine(g, [[19, -27], [29, -26]], "#5f4e59", 2.2);
    // Broader skull, thicker neck ruff and a second ear tip: these are the strokes that make his
    // head read as a different animal rather than the same sprite enlarged. Kept within about
    // 5 px of the shared head/ruff geometry, because that is the slack the flash silhouette's
    // stroke half-width already covers - anything further out would strobe on empty floor.
    //
    // The skull was a filled ellipse before, which is the one thing a silhouette flash cannot fix:
    // fillEnemySilhouette clips to pixels the renderer actually painted, so a fill that extends
    // past the filled ellipses in the flash path leaves a crescent of body that never lights up.
    // Now it is a stroke of the same width the flash path strokes, so body and flash edge match.
    strokeEllipse(g, 16, -33.5, 10, 8.6, "#4a3d45", 2.2);                    // heavier skull
    inkLine(g, [[7, -35], [3, -44]], "#4a3d45", 3.4);                        // outer ruff tuft
    inkLine(g, [[11, -38], [8, -46]], "#55454f", 2.6);
    inkLine(g, [[23, -42], [26, -48]], "#463a42", 2.6);                      // second ear tip
  }
  if (e.boss) {
    // open jaw: the bite is his tell, so the reach the player has to respect is visible art.
    // Everything here stays inside the muzzle ellipse plus a small stroke margin, both so the flash
    // silhouette covers it and because meleeReach in ui.js is measured from the closed muzzle - if
    // the jaw ever grows past it, the bite must be re-measured.
    if (bite > .05) {
      g.fillStyle = "#5a2a2c";                                  // open mouth interior
      g.beginPath();
      g.moveTo(21, -30); g.lineTo(31, -29.4); g.lineTo(30, -26.2 - bite * 1.2); g.lineTo(21, -26.8); g.closePath();
      g.fill();
      strokeEllipse(g, 25, -23.6 - bite * 1.2, 5.5, 2.2 + bite * .8, "#7d3a3d", 1.4);   // lower jaw
      g.fillStyle = "#fff";
      for (let i = 0; i < 2; i++) {                             // upper fangs over the gape
        const fx = 24 + i * 4;
        g.beginPath(); g.moveTo(fx, -27.2); g.lineTo(fx + 1.3, -23.4 - bite); g.lineTo(fx + 2.6, -27.1); g.fill();
      }
    }
  }
  drawEyes(g, 21, -34, 3.2, 2.4, true);
  if (e.boss) {
    // ember-bright pupils over the shared eye whites, so his eyes carry at two tiles' distance
    fillCircle(g, 19.1, -33.5, 1.5, "#ff7a3c");
    fillCircle(g, 23.2, -33.5, 1.5, "#ff7a3c");
  }
  // fangs off the muzzle
  g.fillStyle = "#fff";
  g.beginPath(); g.moveTo(24, -27); g.lineTo(25.4, -22.5); g.lineTo(26.8, -26.8); g.fill();
  g.beginPath(); g.moveTo(28.5, -27); g.lineTo(29.9, -23); g.lineTo(31.2, -26.6); g.fill();
  g.restore();
}

// ---------------------------------------------------------------------------
// Enemy silhouette path builders.
//
// Currently dead code, and left in place deliberately rather than deleted: ui.js drawEnemyEnt
// documents why the white hit flash was dropped (a black wolf with a white nose blinking onto the
// floor away from the wolf that was hit), and this is the geometry that flash replayed. It is
// unused by anything now - grep finds no caller outside these comments - so it renders nothing and
// costs nothing. Removing it would erase the record of what the flash actually traced, which is the
// useful part of the story; reviving the flash would mean rebuilding it against one enemy's own
// offscreen box rather than a viewport-sized layer.
//
// The history, kept because it explains the shape of the code: the white flash began as a radial
// sprite blitted at the enemy's position and presented across four attempts as a solid white ball
// on empty floor somewhere unrelated to the brute you hit, while the brute stayed dark. Any disc has
// that failure mode - it can be misplaced, it can spill past a thin body, and both additive and
// source-over blending paint the floor underneath it. It was then rebuilt as this path, replaying
// each renderer's solid body shapes so the flash could only cover pixels the enemy occupied. That
// removed the spill and the empty-floor blob, but the wolf still arrived offset and lit at the
// muzzle, because the path was committed through a scratch layer under a transform captured from
// the live context rather than one it was traced in.
//
// The geometry below mirrors the body strokes in the matching draw* function above (torso, head,
// snout, legs, arms). It deliberately omits weapons, eyes, tusks, helms and other ornaments: those
// are accessories, and a flash that briefly ignores them reads better than one that strobes the
// scimitar white. Coordinates are entity-local, matching the translate(e.x, e.y) in ui.js; the
// bosses that draw themselves at e.scale are wrapped in g.scale here as their renderer does, so a
// silhouette can never be a normal-sized shape on a giant wolf.
//
// Each builder returns false for a kind with no geometry - the hollowcrown has none - so a caller can
// fall back rather than fill an empty path.
// ---------------------------------------------------------------------------
function fillEnemySilhouette(g, e, time) {
  const sw = e.atkT > 0 && e.atkDur > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
  g.beginPath();
  const k = e.kind;
  if (e.boss && k === "troll") {
    const zoom = e.scale || 1;
    g.save(); g.scale(e.face * zoom, zoom);
    for (const lx of [-8, 8]) g.rect(lx - 4.5, -20, 9, 18);
    g.ellipse(0, -38, 22, 22, 0, 0, TAU);
    g.rect(10, -48, 8, 22);
    g.ellipse(2, -66, 13, 11, 0, 0, TAU);
    g.restore();
  } else if (k === "bat") {
    // One span over both wings at their widest, plus the body. A bat is 60px tall and its wings
    // beat, so matching each membrane frame by frame would risk a sliver of lit floor at the tips.
    const zoom = e.boss ? (e.scale || 1) : 1;
    g.save(); g.scale(e.face * zoom, zoom);
    g.rect(-25, -42, 50, 28);
    g.ellipse(0, -26, 9, 8, 0, 0, TAU);
    g.restore();
  } else if (k === "spider") {
    // Boss spider (Xath-Rokka the Dread Weaver) draws herself at e.scale, same trap as the wolf.
    const zoom = e.boss ? (e.scale || 1) : 1;
    g.save(); g.scale(zoom, zoom);
    for (let i = 0; i < 4; i++) g.rect(-27, -8 - i * 3.5 - 1.6, 54, 3.2);
    g.ellipse(0, -13, 11, 8, 0, 0, TAU);
    g.ellipse(9, -18, 7, 6, 0, 0, TAU);
    g.restore();
  } else if (k === "fenris") {
    // Body only, not a box: the old flash was a radial disc, and even a rectangle that stayed
    // inside the animal would read as a white slab because a wolf is not a rectangle. Replaying
    // the barrel, haunches, shoulder, neck, head and muzzle gives a flash that has his shape.
    // Legs are omitted - they are 5px strokes whose reach changes with the gait, so they are the
    // one part that could put lit pixels past the body.
    //
    // The zoom is the part that was missing before: e.scale is applied by the renderer, not by the
    // caller, so a silhouette that ignores it paints a pack-sized flash on a giant wolf - the boss
    // reads as un-hit, or as a small white mark beside his own head. Mirror by face and scale about
    // the feet, exactly as drawFenris does.
    const zoom = e.boss ? (e.scale || 1) : 1;
    g.save(); g.scale(e.face * zoom, zoom);
    // fillEllipse, not strokeEllipse: this path is only ever filled by the caller, and the stroke
    // half would also set lineWidth/strokeStyle for no reason.
    fillEllipse(g, 0, -25, 23, 12, "#000");          // barrel
    fillEllipse(g, -13, -22, 8, 9, "#000");           // haunch
    fillEllipse(g, 12, -23, 8, 9, "#000");            // shoulder
    fillEllipse(g, 17, -32, 9, 8, "#000");            // neck/head
    fillEllipse(g, 25, -30, 9, 5.5, "#000");          // muzzle
    // The boss's open jaw is drawn past the closed muzzle, so the flash has to follow it or the
    // gape stays dark while the rest of him goes white - and the jaw is exactly the part the player
    // is watching. Same pose formula and the same ellipse parameters as drawFenris.
    if (e.boss) {
      const bite = e.atkT > 0 && e.atkDur > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
      if (bite > .05) fillEllipse(g, 25, -23.6 - bite * 1.2, 5.5, 2.2 + bite * .8, "#000");
    }
    g.restore();
  } else if (k === "brutearcher") {
    const zoom = e.boss ? (e.scale || 1) : 1;
    g.save(); g.scale(e.face * zoom, zoom);
    for (const lx of [-4, 4]) g.rect(lx - 2.5, -14, 5, 13);
    g.ellipse(0, -23, 10, 11, 0, 0, TAU);
    g.rect(4, -30, 6 + sw * 12, 8);
    g.ellipse(2, -39, 9, 8, 0, 0, TAU);
    g.restore();
  } else if (k === "brute" || k === "ironjaw" || k === "captain") {
    // "captain" is the brute-captain boss: it reuses the upright brute geometry, so it shares this
    // builder rather than silently losing its flash.
    const ironjaw = k === "ironjaw" || (e.boss && k === "captain");
    const zoom = e.boss ? (e.scale || 1) : 1;
    g.save(); g.scale(e.face * zoom, zoom);
    for (const lx of [-5, 5]) g.rect(lx - 2.5, -14, 5, 13);
    g.ellipse(0, -21, ironjaw ? 13 : 11, ironjaw ? 13 : 11, 0, 0, TAU);
    g.rect(5, -27, 5 + sw * 8, 8);
    const hy = ironjaw ? -38 : -35;
    g.ellipse(1, hy, ironjaw ? 11 : 10, ironjaw ? 9.5 : 8.5, 0, 0, TAU);
    g.ellipse(6, hy + 3, 4.5, 3.2, 0, 0, TAU);
    if (ironjaw) g.rect(-10, hy - 13, 22, 12);
    g.restore();
  } else {
    // hollowcrown and anything not yet given a builder: no silhouette, caller falls back.
    return false;
  }
  return true;
}

function drawBoss(g, e, time) {
  const k = e.kind;
  if (k === "troll") {
    const s = e.face, legs = legSwing(time + e.wob, .01);
    g.save(); g.scale(s, 1);
    inkLine(g, [[-8, -20], [-10 + legs[0] * .3, -3]], "#4a4238", 9);
    inkLine(g, [[8, -20], [10 + legs[1] * .3, -3]], "#4a4238", 9);
    strokeEllipse(g, 0, -38, 22, 22, "#5d584a");                      // bulky body
    strokeEllipse(g, 0, -32, 13, 12, "#4a453a", 2);
    const sw = e.atkT > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
    inkLine(g, [[10, -48], [20 + sw * 14, -54 - sw * 14]], "#5d584a", 8);  // club arm
    g.save(); g.translate(21 + sw * 14, -56 - sw * 14); g.rotate(-.4 + sw * 2.2);
    strokeRect(g, -4, -30, 9, 32, "#6b4a30", 2.5);
    strokeEllipse(g, .5, -30, 9, 11, "#6b4a30", 2.5);
    g.fillStyle = "#3a2a1c"; g.beginPath(); g.arc(-3, -33, 2.2, 0, TAU); g.arc(4, -28, 2.2, 0, TAU); g.arc(1, -37, 2, 0, TAU); g.fill();
    g.restore();
    inkLine(g, [[-12, -48], [-20, -34]], "#5d584a", 8);
    strokeEllipse(g, 2, -66, 13, 11, "#6b6455");                       // small head
    strokeEllipse(g, 9, -63, 5, 3.5, "#4a453a", 1.5);
    drawEyes(g, 3, -69, 4.5, 2.8, true);
    g.fillStyle = "#fff"; g.beginPath(); g.moveTo(6, -59); g.lineTo(7.6, -63); g.lineTo(9, -58.6); g.fill();
    g.restore(); return;
  }
  if (k === "fenris") {
    const s = e.face, legs = legSwing(time + e.wob, .026);
    g.save(); g.scale(s, 1);
    for (let i = 0; i < 4; i++) { const lx = -14 + i * 9.5, sw2 = legs[i % 2] * (i < 2 ? 1 : -1) * .5;
      inkLine(g, [[lx, -16], [lx + sw2, -2]], "#3a3038", 5); }
    strokeEllipse(g, 0, -24, 22, 12, "#463a42");                       // body
    strokeEllipse(g, -2, -26, 14, 7, "#5a4a55", 1.8);                  // shaggy back
    for (let i = 0; i < 6; i++) inkLine(g, [[-14 + i * 5, -32], [-16 + i * 5, -38 - (i % 2) * 3]], "#33292f", 2.4); // fur spikes
    inkLine(g, [[-20, -28], [-32, -34]], "#463a42", 5);                // tail
    strokeEllipse(g, 18, -30, 11, 9, "#514249");                        // head
    strokeEllipse(g, 26, -28, 7, 5, "#3a3038", 1.8);                    // snout
    strokeEllipse(g, 13, -37, 4, 5.5, "#463a42", 1.8);                  // ear
    drawEyes(g, 19, -33, 3.4, 2.5, true);
    g.fillStyle = "#fff";                                               // fangs
    g.beginPath(); g.moveTo(24, -25); g.lineTo(25.4, -21); g.lineTo(26.6, -24.8); g.fill();
    g.beginPath(); g.moveTo(28.5, -25); g.lineTo(29.8, -21.4); g.lineTo(31, -24.6); g.fill();
    g.restore(); return;
  }
  if (k === "spider") {
    g.save(); g.translate(0, -22);
    g.strokeStyle = "#170a0d"; g.lineWidth = 4; g.lineCap = "round";
    for (const dir of [-1, 1]) for (let i = 0; i < 4; i++) {
      const ph = Math.sin(time * .012 + i * 1.5 + e.wob) * 6;
      g.beginPath(); g.moveTo(dir * 9, -8 + i * 5);
      g.quadraticCurveTo(dir * (26 + i * 3), -34 - i * 3 + ph, dir * (38 + i * 4), -4 + i * 4 + ph * .5);
      g.stroke();
    }
    strokeEllipse(g, 0, -12, 22, 19, "#2a1a30");
    g.fillStyle = "rgba(200,120,255,.55)"; g.beginPath();
    g.moveTo(0, -28); g.lineTo(8, -12); g.lineTo(0, 2); g.lineTo(-8, -12); g.closePath(); g.fill();
    strokeCircle(g, 0, -32, 11, "#1f1226", 2.5);
    g.fillStyle = "#ff3a6a";
    for (const [dx, dy] of [[-5, -2], [5, -2], [-7, 3], [7, 3], [-2, -5], [2, -5]]) { g.beginPath(); g.arc(dx, -32 + dy, 2.2, 0, TAU); g.fill(); }
    g.restore(); return;
  }
  if (k === "captain") {
    // big ironjaw with horned helm + shield
    const s = e.face, legs = legSwing(time + e.wob, .014);
    g.save(); g.scale(s, 1);
    inkLine(g, [[-6, -16], [-8 + legs[0] * .4, -2]], "#2e3a26", 6);
    inkLine(g, [[6, -16], [8 + legs[1] * .4, -2]], "#2e3a26", 6);
    strokeEllipse(g, 0, -28, 16, 16, "#4f5c40");
    strokeEllipse(g, 0, -24, 10, 9, "#39442f", 2);
    const sw = e.atkT > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
    inkLine(g, [[8, -34], [14 + sw * 10, -42 - sw * 8]], "#4f5c40", 6);
    g.save(); g.translate(15 + sw * 10, -44 - sw * 8); g.rotate(.5 - sw * 2.7);
    g.beginPath(); g.moveTo(0, 0); g.quadraticCurveTo(6, -16, -3, -30); g.quadraticCurveTo(1, -14, -3, -2); g.closePath();
    g.fillStyle = "#aeb6bd"; g.fill(); g.lineWidth = 2.2; g.strokeStyle = "#170a0d"; g.stroke(); g.restore();
    inkLine(g, [[-9, -34], [-16, -26]], "#4f5c40", 6);
    strokeEllipse(g, -18, -26, 8, 11, "#4c4e57", 2.4); // shield
    strokeCircle(g, -18, -26, 3, "#2c2e35", 1.6);
    strokeEllipse(g, 2, -50, 13, 11, "#6b7a50");
    drawEyes(g, 3, -53, 4.6, 2.9, true);
    g.beginPath(); g.arc(2, -54, 13, Math.PI, 0); g.closePath();
    g.fillStyle = "#3c3e46"; g.fill(); g.lineWidth = 2.4; g.strokeStyle = "#170a0d"; g.stroke();
    inkLine(g, [[-9, -58], [-16, -70]], "#3c3e46", 3.4); inkLine(g, [[13, -58], [20, -70]], "#3c3e46", 3.4); // horns
    g.restore(); return;
  }
  if (k === "hollowcrown") {
    // Hollow Crown: floating cloaked wraith with crown and mace
    const fy = Math.sin(time * .006) * 5, s = e.face;
    g.save(); g.translate(0, -fy - 4); g.scale(s, 1);
    // cloak
    g.beginPath(); g.moveTo(0, -66);
    g.bezierCurveTo(-30, -50, -28, -14, -22 + Math.sin(time * .005) * 4, 0);
    g.lineTo(22 + Math.sin(time * .005 + 2) * 4, 0);
    g.bezierCurveTo(28, -14, 30, -50, 0, -66); g.closePath();
    g.fillStyle = "#14101c"; g.fill(); g.lineWidth = 3; g.strokeStyle = "#000"; g.stroke();
    g.fillStyle = "rgba(90,70,120,.25)";
    g.beginPath(); g.moveTo(-12, -40); g.quadraticCurveTo(0, -30, 12, -42); g.lineTo(10, -8); g.lineTo(-10, -8); g.closePath(); g.fill();
    // void hood
    g.beginPath(); g.arc(0, -60, 12, 0, TAU); g.fillStyle = "#000"; g.fill(); g.lineWidth = 2.6; g.strokeStyle = "#170a0d"; g.stroke();
    // cold eyes
    g.fillStyle = "#bfe9ff";
    g.beginPath(); g.arc(-4, -61, 2.2, 0, TAU); g.arc(4, -61, 2.2, 0, TAU); g.fill();
    g.shadowColor = "#7fd0ff"; g.shadowBlur = 10; g.fill(); g.shadowBlur = 0;
    // crown
    g.fillStyle = "#5a5f6e"; g.strokeStyle = "#170a0d"; g.lineWidth = 2;
    g.beginPath(); g.moveTo(-11, -70); g.lineTo(-11, -78); g.lineTo(-6, -73); g.lineTo(-2, -80); g.lineTo(2, -73); g.lineTo(6, -80); g.lineTo(11, -73); g.lineTo(11, -70); g.closePath();
    g.fill(); g.stroke();
    // mace arm
    const sw = e.atkT > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
    inkLine(g, [[10, -46], [20 + sw * 12, -52 - sw * 10]], "#14101c", 6);
    g.save(); g.translate(21 + sw * 12, -54 - sw * 10); g.rotate(-.3 + sw * 2);
    strokeRect(g, -2.5, -26, 5, 26, "#2c2735", 2); strokeCircle(g, 0, -30, 8, "#3c3747", 2.4);
    for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2; inkLine(g, [[Math.cos(a) * 6, -30 + Math.sin(a) * 6], [Math.cos(a) * 12, -30 + Math.sin(a) * 12]], "#3c3747", 3); }
    g.restore();
    g.restore(); return;
  }
}

// ---------------------------------------------------------------------------
// Authored hit shapes for the two kinds that ui.js routes to capsule tests:
// the hounds (pack and boss) and the troll boss. Every other kind has no entry
// here and keeps the plain circular body test in arrowHitsEnemy.
//
// These live next to the renderers on purpose: ui.js says the shapes must come
// from "the same constants that author the art", because guessing them from
// e.r is what left the wolf's body and head non-interactive while only the paws
// took arrows. Coordinates are entity-local with the feet at the origin, facing
// +x, in the same space as the ellipse centres in drawFenris and drawBoss above.
// ui.js does the rest: it multiplies by WOLF_ZOOM(e) and mirrors by e.face, so
// these are authored at PACK scale only. Capsule radius scales with the zoom
// too, which is why the boss is still hittable at e.scale ~2.8.
//
// The owner's rule for this pass is "if an arrow touches the wolf it counts",
// which turns the old policy on its head. These sets used to be a few circles
// anchored on body ellipses with radii run deliberately *under* the half-extents
// so arrows landed on fur rather than empty floor, with the legs, tail, ears,
// spine spikes and crest left out because a swinging limb or a swinging club is
// an accessory the player could lose a hit on. That is exactly what the owner is
// now objecting to: shots through the head, the spiky fur on the back, the tail
// and the legs flew straight through, which made regular hounds harder to hit
// than before and left most of Skarnax unfetchable. So coverage now follows the
// full visible extent instead of an inset core:
//   - every stroked ellipse is covered at its own half-extents plus the ~1 px the
//     dark stroke adds, and adjacent circles overlap so there are no gaps between
//     them for an arrow to thread;
//   - the spiky spine, the boss crest, the ruff tufts, the tail, the ears and the
//     legs are each covered by a capsule that spans the stroke the renderer draws;
//   - the one thing still deliberately out is the boss's club itself, and for the
//     same reason fillEnemySilhouette omits weapons: the swing pose is authored
//     nowhere in these coordinates, so covering it would mean guessing at an arc.
//     The troll's arm at rest was already only a shoulder-sized circle.
// Anything that visibly belongs to the animal is hittable; only held accessories
// are not.
// ---------------------------------------------------------------------------

// A circle on the body axis becomes a capsule from (x,y) to (x,y). Keeps both
// shape sets readable and one code path in pushHitShape.
function hitCircle(x, y, r) {
  return { line: [x, y, x, y], r };
}

// A stroked limb, tail or fur spike becomes a capsule between its two endpoints
// with the half-width the renderer actually strokes. This is the shape that was
// missing whenever the owner lost a shot through a leg or the tail: a capsule
// along the ink, not a circle approximating it.
function hitLimb(x1, y1, x2, y2, halfWidth) {
  return { line: [x1, y1, x2, y2], r: halfWidth };
}

// Pack-scale hound, from the geometry in drawFenris (which both the pack wolves and
// Skarnax use, the boss only via the zoom in WOLF_ZOOM). The nose is included so
// a head-on arrow still connects at the leading edge, which is where the bite
// reach is measured from anyway.
function wolfHitShapes() {
  return [
    // --- body masses, at the half-extents the renderer strokes ---
    // The barrel is a 23x12 ellipse, so it needs an *ellipse* capsule, not a circle: one
    // r = 12.5 disc would carry the half-height but reach the same 12.5 px fore and aft, which
    // is inside the art and leaves the long axis to be stitched together by the haunch and
    // shoulder below, with gaps to thread at the extremes. A degenerate capsule from (-11.75, -25)
    // to (11.75, -25) with r = 1.75 is exactly that ellipse's swept outline (±13.5, half-height
    // 13.75), so the body itself is one shape built from the ellipse the renderer strokes.
    { line: [-11.75, -25, 11.75, -25], r: 1.75 },
    hitCircle(-13, -22, 9.5),     // haunch (8x9 plus stroke)
    hitCircle(12, -23, 9.5),      // shoulder (8x9 plus stroke), overlapping the barrel ends
    hitCircle(17, -32, 9.5),     // neck/head (9x8 plus stroke)
    hitCircle(25, -30, 6.5),     // muzzle (9x5.5 plus stroke)
    hitCircle(33, -31, 3.5),     // nose (2.6x2.2), leading edge
    // --- spiky fur on the back: seven 2.6 px strokes leaning up and back ---
    hitLimb(-16, -34, -18, -41, 1.9),
    hitLimb(-11, -34, -13, -44, 1.9),
    hitLimb(-6, -34, -8, -41, 1.9),
    hitLimb(-1, -34, -3, -44, 1.9),
    hitLimb(4, -34, 2, -41, 1.9),
    hitLimb(9, -34, 7, -44, 1.9),
    hitLimb(14, -34, 12, -41, 1.9),
    // --- neck ruff, four tufts ---
    hitLimb(8, -36, 5, -43, 1.7),
    hitLimb(11, -36, 8, -45, 1.7),
    hitLimb(14, -36, 11, -43, 1.7),
    hitLimb(17, -36, 14, -45, 1.7),
    // --- ears ---
    hitLimb(12, -43, 12, -37, 5.2),   // erect ear as a swept capsule: (4.5x6.5), long axis is vertical
    hitLimb(20, -43.5, 20, -38.5, 4.7), // second ear (4x5.5), same treatment
    // --- bushy tail, bristling up and back ---
    hitLimb(-20, -28, -33, -35, 3.6),
    hitLimb(-33, -35, -39, -42, 3.1),
    hitLimb(-26, -32, -31, -39, 1.7),
    hitLimb(-29, -33, -34, -40, 1.7),
    hitLimb(-32, -34, -37, -41, 1.7),
    hitLimb(-35, -35, -40, -42, 1.7),
    // --- four legs, each spanning its full gait sweep ---
    // A leg swings ±3 px at the paw (legSwing amplitude 5 times the .6 lean in drawFenris)
    // and the thigh is pinned to the body, so one capsule per leg from the hip out to the
    // widest paw position covers every pose the gait can put it in. The inner end lands
    // inside the barrel/haunch/shoulder circles, so a shot low on the belly still counts.
    hitLimb(-15, -18, -21, -1.5, 3.3),
    hitLimb(-5, -18, -11, -1.5, 3.3),
    hitLimb(5, -18, 11, -1.5, 3.3),
    hitLimb(15, -18, 21, -1.5, 3.3),
  ];
}

// Boss-only extra coverage, appended by ui.js on top of wolfHitShapes for the
// pack wolf's art. Skarnax is drawn by drawFenris at e.scale, so these use the same
// local units; his crest, belly mass, collar, heavier skull, outer ruff, second
// ear tip and thicker boss legs are separate strokes that a pack-sized shape set
// does not reach.
function bossWolfHitShapes() {
  return [
    hitCircle(-1, -21, 8),        // heavier bellyline (19x7.5); its width is carried by the
                                  // barrel/haunch/shoulder circles above
    // iron collar where the head meets the ruff, stroked 8x6.8 at the rest pose
    hitCircle(13, -33, 5),
    // taller bristling spine crest, nine 3.8 px strokes leaning up and back
    hitLimb(-18, -33, -21, -49, 2.5),
    hitLimb(-13.5, -33, -16.5, -55, 2.5),
    hitLimb(-9, -33, -12, -49, 2.5),
    hitLimb(-4.5, -33, -7.5, -55, 2.5),
    hitLimb(0, -33, -3, -49, 2.5),
    hitLimb(4.5, -33, 1.5, -55, 2.5),
    hitLimb(9, -33, 6, -49, 2.5),
    hitLimb(13.5, -33, 10.5, -55, 2.5),
    hitLimb(18, -33, 15, -49, 2.5),
    // dewlap row under the throat
    hitLimb(-16, -26, -19, -33, 2.1),
    hitLimb(-9, -26, -12, -33, 2.1),
    hitLimb(-2, -26, -5, -33, 2.1),
    hitLimb(5, -26, 2, -33, 2.1),
    hitLimb(12, -26, 9, -33, 2.1),
    // broader skull and heavier jowl
    hitCircle(16, -33.5, 10.4),   // heavier skull (10x8.6)
    hitLimb(19, -27, 29, -26, 1.8),
    // outer ruff tufts and the second ear tip
    hitLimb(7, -35, 3, -44, 2.3),
    hitLimb(11, -38, 8, -46, 1.9),
    hitLimb(23, -42, 26, -48, 1.9),
    // the boss's own heavier legs, which swing ±3 px like the pack's
    hitLimb(-15, -18, -21, -1.5, 4.6),
    hitLimb(-5, -18, -11, -1.5, 4.6),
    hitLimb(5, -18, 11, -1.5, 4.6),
    hitLimb(15, -18, 21, -1.5, 4.6),
  ];
}

// Boss troll only: the regular trolls in the world are not routed here, and the
// troll boss's own e.scale is applied by ui.js via WOLF_ZOOM, matching the
// g.scale(e.face * zoom, zoom) in its renderer and in fillEnemySilhouette.
// Coordinates are the hand-authored ellipses and strokes in drawBoss's troll
// branch. Head sits high, so it needs its own circle or the only hittable part of
// a 560 hp troll is his gut. Same full-coverage rule as the fenris: legs, ears,
// muzzle, brow and the off-side arm are in; only the swinging club is out.
function bossHitShapes(e) {
  return [
    hitCircle(0, -38, 23.5),     // bulky body (22x22 plus its 2 px stroke); near-circular, so one
                                 // disc is the shape the renderer actually strokes
    { line: [0, -38, 0, -26], r: 7 },  // lower belly mass (13x12), degenerate along its short axis
    { line: [-4, -66, 8, -66], r: 6.5 }, // head (13x11), a capsule across the long axis
    hitCircle(9, -63, 5),        // muzzle/brow patch (5x3.5)
    hitLimb(-12, -48, -20, -34, 4.6),   // off-side arm
    hitLimb(10, -48, 21, -56, 4.6),      // club arm at rest, arm only (not the club)
    hitLimb(-8, -20, -13, -2, 5.1),      // rear leg across its full stride
    hitLimb(8, -20, 13, -2, 5.1),        // front leg across its full stride
  ];
}

// The one zoom rule shared by the renderer, the flash silhouette and the hit
// shapes: bosses scale by e.scale, everything else is 1. ui.js reads this for
// every hit shape, and it must keep matching `const zoom = e.boss ? (e.scale ||
// 1) : 1` in drawFenris and fillEnemySilhouette, or the arrows sample a pack-sized
// wolf standing inside a giant one.
function WOLF_ZOOM(e) {
  return e.boss ? (e.scale || 1) : 1;
}

// ================================================================================
// Enemy sprite cache
// --------------------------------------------------------------------------------
// The art above is entirely procedural. drawFenris alone is roughly eighty stroke()/fill()
// calls with round joins, plus an ellipse eye carrying a shadowBlur, repeated once per wolf
// per frame; in a crowd that rasterisation is what PROF.drawEnemies reports, while PROF.ai
// for the same crowd is a fraction of it. The art is also deterministic given (kind, facing,
// animation phase, aim, scale), so each pose can be baked once into a small offscreen canvas
// and blitted thereafter. ui.js owns the blit (drawEnemyEnt -> SPRITE_*); this file owns the
// baking, next to the art whose extents and clocks it has to know.
//
// The cache key is the whole design, so it is stated exactly:
//
//   kind | facing | scale bucket | aim bucket | quantised frame clock
//
// A key on anything continuous misses every frame and re-bakes forever, which is strictly
// slower than not caching. So the continuous animation values are folded into a bounded set
// of quantised frames of each kind's own animation period (FRAME_BUCKETS below), which makes
// a wolf cycle through a dozen poses it shares with every other wolf of its kind, and the bow
// angle is bucketed to whole degrees because a few degrees of head tilt is invisible at this
// art scale. Mirroring is done at blit time with scale(-1,1), not as a key dimension, so
// facing costs nothing extra. hurtT is deliberately absent: the hit ring is stroked over the
// blit, exactly as it is over the procedural art today.
//
// What is NOT cached, with reasons rather than silence:
//   - The hero. One entity on screen; caching him saves nothing and risks the one sprite the
//     player watches most closely.
//   - Bosses, and the fenris at boss scale. Their renderers apply their own g.scale() plus a
//     shadowBlur that is authored for the enlarged body, so their ink extents are not bounded
//     by the pack geometry the bake sizes from. They are one entity each anyway, never the
//     crowd cost.
//   - The emissive props, which looked like the next win and are not. drawProp's `time`
//     argument is forwarded only to drawTorches(), a three-line determinate helper that reads
//     no clock: every prop is already static, renderWorld already culls it to the viewport, and
//     dungeon.js paints its own baked copies. Caching buys one drawImage per on-screen prop and
//     risks the bake match, so it stays as it is. Measured before optimising, per the plan.
// ================================================================================

// Frames baked per kind, from that kind's own visible animation period. The period is the LCM
// of the sinusoid periods in its art, rounded to something clean, so a quantised frame is a
// real pose of the cycle rather than an arbitrary sample:
//   brute/bat/spider leg and wing cycles ~0.35-0.42 s; fenris legs ~0.39 s with a ~0.7 s ear
//   sway, and the bat's wing beat is the fastest thing in the file at ~0.21 s.
const SPRITE_FRAMES = Object.freeze({
  brute: 12, brutearcher: 12, bat: 12, spider: 12, fenris: 14,
});
const SPRITE_PERIOD = Object.freeze({      // ms, see above
  brute: 360, brutearcher: 360, bat: 210, spider: 450, fenris: 700,
});
// The phase the art is actually at, in rad per ms of frame time, for the limb that decides which
// frame this is. SPRITE_PERIOD above is a tidy approximation of each body's animation cycle and is
// fine for naming a cycle; it is *not* the clock the renderers read, and bucketing on it drifts
// against the real one. Derived from the arguments each renderer passes to legSwing/limbSwing
// (world.js: sin(time * rate + phase)), which is the only place any of them looks at time:
//   bat         .03  — drawBat's wing beat, sin(time*.03 + wob)*.8. Its true period is 2pi/.03 =
//                       209 ms; SPRITE_PERIOD.bat of 210 ms was a rounding, and over a 12-frame
//                       cycle the sample drifted about 0.03 rad from the flap it was standing in.
//   fenris      .026 — drawFenris legs, limbSwing(g, 4, .26, e, time, .026). Not the .009 ear sway:
//                       the legs are the larger motion and define the frame.
//   brute       .017 — drawBrute legs, legSwing(g, 5, .19, e, time, .017).
//   ironjaw     .017 — same art as brute.
//   spider      .022 — drawSpider legs, legSwing(g, 6, .15, e, time, .022).
//   brutearcher .017 — drawBruteArcher legs, legSwing(g, 5, .19, e, time, .017); its bow aim is
//                       bucketed separately from e.aimAng and does not use this clock.
// With a frame bucket of period/nframes ms, the pose is sampled at `rate` x that many ms into the
// bucket, so a bucket spans `rate` x period/nframes radians of real limb travel: bat .03 x 17.5 ms
// = 0.52 rad, fenris .026 x 50 = 1.3 rad, brute .017 x 30 = 0.51 rad, spider .022 x 37.5 = 0.83 rad.
// Those are the pose errors the walk cycle accepts, unchanged from the previous buckets; what
// changes is that the pose within a bucket is now the correct one rather than a fixed guess.
const SPRITE_CLOCK = Object.freeze({
  brute: .017, ironjaw: .017, bat: .03, spider: .022, fenris: .026, brutearcher: .017,
});
// Only brutearcher uses aim (drawFenris takes no aim input).
//
// Quantised over the FULL circle, not over the art's travel: drawBruteArcher reads aimAng as a raw
// atan2 heading (ui.js sets it from the direction to the player), so bucketing only the ±0.18 rad
// that the bow arm physically sweeps would leave every other heading in a bucket of its own and turn
// every aiming frame into a cache miss.
//
// Step count, from the art rather than from taste. The limb that carries the aim is a forearm of
// length 26 whose angle is the heading plus a ±0.09 rad offset, so the hand sits at most ~50 px from
// the shoulder and a bucket edge error of half a step moves it 50 * sin(step/2) pixels:
//   16 steps (22.5°) -> hand moves ~9.8 px between neighbouring buckets
//   24 steps (15°)   -> ~6.5 px
//   36 steps (10°)   -> ~4.4 px
// A 10° snap is still faintly visible on a slow aim, and the archer aims continuously, so 36 is the
// setting used. Cost is 2 facings x 12 frames x 36 aim = 864 cells for the archer's idle cycle, so
// SPRITE_MAX_CELLS has to be sized to hold that plus everyone else rather than being a round number.
const SPRITE_AIM_STEPS = 36;
// Ink padding. The art fits its body box, but drawBrute's eyes carry shadowBlur 5, and a
// shadow smear extends past the shape that casts it, so the pad has to cover the blur rather
// than the stroke. Bake time measures the actual ink and grows the canvas if anything still
// touches the edge; this constant is the first guess, not a claim.
const SPRITE_PAD = 12;
// Art body boxes, in the art's own coordinates: x centred on 0, feet at y = 0, head up in
// negative y. Measured from the geometry in the renderers (bat: wing tips ±27 with the body
// bobbing to y = -38; fenris: tail to -39, nose to +36, ears to -47).
const SPRITE_ART = Object.freeze({
  brute: { x0: -22, x1: 26, y0: -62, y1: 2 },
  brutearcher: { x0: -24, x1: 34, y0: -50, y1: 2 },
  bat: { x0: -28, x1: 28, y0: -42, y1: -12 },
  spider: { x0: -26, x1: 26, y0: -32, y1: 1 },
  fenris: { x0: -42, x1: 40, y0: -50, y1: 2 },
});
// Hard ceilings on one bake. A key that produces a box past these is drawn procedurally
// instead of allocating something absurd in the middle of a fight.
// Cap on a *cropped* cell, i.e. the sprite after the ink trim, not the bake canvas it came from.
// The largest authored body box is fenris at 94 x 84, and the crop can only shrink that, so 192
// leaves ~2x headroom for an art edit while still catching something that would want a viewport-
// sized canvas. Over-limit is skipped and drawn procedurally, never truncated — a slow-path enemy,
// not a clipped one. (The separate limit inside SPRITE_bake is on the uncropped bake canvas, which
// is this plus the pad on every side; see SPRITE_MAX_BAKE_SIDE.)
const SPRITE_MAX_SIDE = 192;
// Ceiling on baked cells. The pose space is closed and, at these constants, at most
// kinds(5) x facings(2) x scale buckets(21) x aim(6) x frames(12) x swings(8); the reachable
// set is far smaller because normal enemies all sit at scale 1 and warm-up bakes it eagerly
// (SPRITE_warm below), so nothing on screen can ever be evicted to make room. Raised from 96 on
// the owner's instruction: a big fight visits every kind and pose, and 96 forced fallbacks.
// Memory stays bounded — one cell is a cropped canvas of roughly 110x90 px (~40 KB), so even a
// few hundred cells is single-digit MB.
// Attack swing buckets across the attack's arc (1..SPRITE_SWING_STEPS; 0 = not attacking). The
// art's swing curve is a half sine over the whole arc, so 8 buckets put the shared pose within
// 1/16 of the arc of the live pose — about a 3-degree limb error at peak swing, the same class of
// error the walk-cycle bucketing already accepts, and it is what lets a dozen wolves mid-attack
// share a handful of cells instead of taking a miss every frame.
const SPRITE_SWING_STEPS = 8;
// Kinds whose art carries an orientation-specific prop drawn *outside* the body mirror, and
// therefore must never be mirrored at the blit: drawBruteArcher draws its bow arm in unmirrored
// space on purpose, g.rotate(e.aimAng) with a g.scale(1, -1) counter-flip, precisely so the bow
// keeps pointing at the true heading when the body turns around. Blitting a left-facing archer
// cell through g.scale(-1, 1) mirrors that arm around the sprite centre instead of the shoulder:
// the archer faces left while the bow points right, i.e. aims away from what it is shooting at.
// Those kinds bake each facing as its own cell instead, which is both correct and free — the body
// still mirrors, because the renderer mirrors it, and the prop keeps aiming.
//
// The same argument turned out to apply to every routed kind, not just the archer: a bitmap flip is
// not the same image as the renderer's left-facing draw (see SPRITE_FACINGS).
// Every routed kind now bakes each facing it can be seen in, so this set is empty and nothing is
// blit-mirrored. It is kept as a table because the blit still needs a per-cell record of whether a
// flip is legal — the cell carries the answer, so SPRITE_blit never has to re-derive it from a kind
// name and cannot get it wrong for a kind it was not told about.
//
// Blit mirroring was tried and rejected on the owner's comparison against the original art. It is
// not a pixel-exact substitute for what the renderers do: the art draws its *strokes* asymmetrically
// and its *props* in the body's own mirror space, so a bitmap flip lands the outline about 8 px off
// on the brute and about 5 px off the other way on the fenris (the g.scale(face,1) pivot sits
// against an ink crop that is not centred on the body), and the flipped result still reads as a
// reversed photograph rather than as the left-facing drawing. Baking both facings reproduces the
// renderer exactly, at the cost of a second cell per pose, which the owner accepted.
const SPRITE_NO_MIRROR = Object.freeze({});
// Sized from the reachable key set rather than picked to feel generous:
//   idle cells = facing x frames x aim buckets, summed over kinds:
//     brute 12 + ironjaw 12 + bat 12 + spider 12 + fenris 14 = 62, one facing each because these
//     mirror at the blit, plus brutearcher 2 facings x 12 frames x 36 aim = 864   ->  926
//   swing cells = kinds x facing x SPRITE_SWING_STEPS = 6 x 2 x 8 = 96 for the on-demand attack
//     buckets (the archer's swing cells land on top of its aim grid, so the real worst case is a
//     little higher), plus headroom for off-cycle scales and for idle poses still live in a fight.
// 1,280 covers that with ~25% headroom. Each cell is a cropped canvas of roughly 90 x 74 px, about
// 27 KB of RGBA, so the ceiling is ~34 MB at full — which is the actual constraint, and why this is
// sized rather than open-ended. The earlier 2,048 was roughly twice what the key space could reach,
// which made the ceiling look untouchable while memory was the real limit.
// Sized for the bake-both-facings rule above, which doubles every mirrored kind's idle set:
//   idle = brute 2x12 + ironjaw 2x12 + bat 2x12 + spider 2x12 + fenris 2x14 = 124,
//          brutearcher 2 facings x 12 frames x 36 aim = 864                    ->  988
//   on-demand attack swings add up to kinds x facing x SPRITE_SWING_STEPS       ->  ~100
//   plus headroom for off-cycle scales and for idle poses still live in a big fight.
// 2,048 covers that with ~2x headroom on the attack path. At roughly 90 x 74 px a cell (~27 KB of
// RGBA) the full ceiling is ~54 MB of canvas, which is the real constraint; memory, not cell count,
// is what this number is actually sizing.
const SPRITE_MAX_CELLS = 2048;
// How much of the map survives an eviction pass, and how many passes are tolerated before the cache
// is declared useless for this session. Full-price behaviour at the ceiling is one bake for the new
// pose plus a fraction of a second of headroom, so occasional eviction is fine; sustained eviction
// means the working set really does exceed the map, and continuing to bake every frame would be the
// slow path wearing a cache's clothes. SPRITE_THRASH_LIMIT turns the cache off in that case, which
// is a deliberate, measurable degradation rather than a silent one.
const SPRITE_EVICT_FRACTION = 0.75;
// Generous, because warm-up itself can trip a few evictions on a tight map, and because the failure
// this guards against — the working set genuinely exceeding the cache — would otherwise bake on every
// lookup forever. At the sized ceiling above, even a tenth of this means the key space is wrong,
// which is exactly the thing worth being told about.
const SPRITE_THRASH_LIMIT = 500;

// Which facings of each kind get their own baked cell. Both, for every routed kind, on the owner's
// instruction after a side-by-side against the original drawing: the blit mirror landed left-facing
// brutes ~8 px too far right and left-facing fenris ~5 px too far left, and even when nudged into
// place did not read as the real left-facing art.
//
// Both symptoms have one cause. The renderers mirror by g.scale(face, 1) about the entity origin,
// and that is what the cached bitmap has to reproduce — but the cell is cropped to its ink, and the
// ink is not symmetric about x = 0: brute spans about -14..+42 (pivot 56 px from the right edge, 42
// from the left), fenris about -45..+37 (37 from the right, 45 from the left). Flipping the crop and
// re-anchoring it puts the feet where a symmetric sprite's would be, not where this one's are, so
// the error comes out as +(pivot - leftInk) ≈ +8 on the brute and -(rightInk - (pivot - leftInk))
// ≈ -5 on the fenris: different signs, different sizes, exactly as reported. No anchor formula fixes
// that class of error, because the shape itself is mirrored while its pivot is not.
//
// Baking the renderer at e.face = -1 has no such approximation: it is the same call the procedural
// path makes, so the pixels are the ones the original drawing produced, pivot included. The cost is
// one extra cell per pose, which the owner accepted; the pose space stays closed, so warm-up covers
// it and the ceiling just has to be big enough (see SPRITE_MAX_CELLS).
// Entity kind -> the kind whose art draws it, after SPRITE_ROUTE's merges. ironjaw is not an art:
// drawBrute(g, e, time, true) paints it, so its body box, its frame count and its cache bucket are
// the brute's. Anything absent here is drawn by an art of its own name. Keeping this as a table
// means the pose, the key and the bake all read one mapping instead of each re-deriving it.
const SPRITE_KIND_ART = Object.freeze({ ironjaw: "brute" });

// The bow arm's pivot, in art units: the shoulder the arm is translated to before it rotates toward
// the target. Named once and used by both the drawing function and the blit that re-creates the
// transform, so the two cannot drift apart.
const SPRITE_ARCHER_ARM_ANCHOR = Object.freeze([3, -25]);

// --- The archer's sprite split -------------------------------------------------------------
// drawBruteArcher is three functions called in the order it has always drawn them (body, bow arm,
// head). For every other kind the cache bakes the whole figure as one sprite keyed by facing, frame,
// attack step *and aim heading* — and for the archer that last axis is continuous, recomputed toward
// the player every frame, so a tracking archer asked for a fresh bake on nearly every frame. The
// three parts are baked and stamped separately now:
//   body — legs, torso, quiver:   per facing, walk frame, attack step. No aim.
//   arm  — bow arm, bow, string:  per attack step, baked unrotated (aim 0).
//   head — head, tusks, cap:      per facing. No aim, no frame.
// The aim heading is in none of these keys; it survives as a live transform on the arm stamp, using
// the same shoulder translate / rotate / left-facing flip drawBruteArcherBowArm applies.
// Each part is baked by calling *its own function* — never by a flag that hides parts of the whole
// figure. A hide-the-arm flag also hides the arm's own save/transform scope, which is how the last
// attempts ended up baking a head where a bow should be; a function cannot skip its own frame.
const SPRITE_ARCHER_PARTS = Object.freeze({
  // fn draws the piece; key builds its cache key. The aim heading appears in none of them; it only
  // re-appears as a live transform when the arm sprite is stamped (SPRITE_blitArcherParts).
  body: { fn: drawBruteArcherBody, key: function (face, fi, atkB, sc) { return "b" + fi + "|" + sc + "|" + face; } },
  arm:  { fn: drawBruteArcherBowArm, key: function (face, fi, atkB, sc) { return "a" + atkB + "|" + sc + "|" + face; } },
  head: { fn: drawBruteArcherHead, key: function (face, fi, atkB, sc) { return "h" + sc + "|" + face; } },
});

// The aim heading the arm is baked at: none. The arm sprite is recorded unrotated and the blit
// rotates it by the live heading, so a tracking archer never asks for a new sprite.
const SPRITE_ARM_REST_AIM = 0;

// Per-part cache keys for one archer pose, read by SPRITE_pose and used by SPRITE_archerPartCell.
// The "ap|" prefix keeps a part key from ever colliding with a whole-figure key.
function SPRITE_archerKeys(face, fi, atkB, sc) {
  const out = {};
  for (const name in SPRITE_ARCHER_PARTS) {
    const p = SPRITE_ARCHER_PARTS[name];
    out[name] = "ap|" + p.key(face, fi, atkB, sc);
  }
  return out;
}

// The arm bake's source entity: the pose's own entity with the aim forced to the rest heading, so the
// bake records an unrotated arm that one live rotation can place anywhere. Only aimAng is overridden —
// face, atkT and wob must come through or the baked arm is the wrong limb state. `part` reaches the
// piece function through pose.part (see SPRITE_drawArt), so the source needs no marker fields.
function SPRITE_archerSource(e, pose, part) {
  const src = Object.create(e);
  src.bakeTime = pose.bakeTime;
  // Only the arm needs an override: baked at the rest heading so the blit's live rotation is the
  // single source of aim. Body and head take the pose's own source unchanged.
  if (part === "arm") src.aimAng = SPRITE_ARM_REST_AIM;
  return src;
}

const SPRITE_FACINGS = Object.freeze({
  brute: [1, -1], ironjaw: [1, -1], bat: [1, -1], spider: [1, -1], fenris: [1, -1], brutearcher: [1, -1],
});
// The facing an entity is drawn at. Enemies only ever hold face = ±1, but reading through a value
// outside that set should not be a cache miss, so anything else is served the right-facing cell —
// the art's own canonical direction, and what the renderers produce at face = 1.
function SPRITE_facing(e) {
  return e && e.face < 0 ? -1 : 1;
}

const SPRITE_CACHE = new Map();
let SPRITE_BAKES = 0, SPRITE_SKIPS = 0, SPRITE_MISSES = 0, SPRITE_EVICTED = 0;
let SPRITE_THRASHED = false;
// The scratch canvas every bake draws into, then copies out. Reusing one canvas avoids
// allocating a fresh backing store per sprite cell in the middle of a fight; the copy into the
// per-cell canvas is a single drawImage and is what makes the cached sprite independent.
let SPRITE_SCRATCH = null;

// Bounds of everything the scratch canvas actually received, by scanning alpha. Needed because
// a shadowBlur smear extends past the shape that casts it and the authored body boxes are
// geometry, not ink. Returns {x0,y0,x1,y1} or null when there is nothing to read.
function SPRITE_ink(g, w, h) {
  let d = null;
  try { d = g.getImageData(0, 0, w, h).data; } catch (err) { return null; }
  if (!d) return null;
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) {
    const row = y * w * 4;
    for (let x = 0; x < w; x++) {
      if (d[row + x * 4 + 3] > 6) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < 0) return null;
  return { x0: x0, y0: y0, x1: x1, y1: y1 };
}

// Which renderer this entity's art comes from, mirroring drawEnemyEnt's dispatch. Returns null
// for anything that must stay procedural (bosses, kinds with no entry above).
//
// The routing key is the *art*, not the entity: ironjaw shares drawBrute with brute and differs
// only in the 6th argument (`iron`). That argument is a render input, so it has to reach the
// renderer through the pose and be part of the cache key — otherwise an ironjaw would be served
// the plain brute sprite and lose its metal plates. `art` selects the renderer, `ink` carries the
// per-art-variant flag, and the key includes the entity kind so variants never collide.
function SPRITE_ROUTE(e) {
  if (!e || e.boss) return null;
  const k = e.kind;
  if (k === "bat" || k === "spider" || k === "brutearcher" || k === "fenris" ||
      k === "brute" || k === "ironjaw") return k;
  return null;
}

// Parts of SPRITE_BAT_LIFT + SPRITE_batBob: the constant lift, and the live hover term. Kept
// separate because the cache bakes the lift (it never varies) and applies the hover per frame. The
// time rate (.008) and the wob phase are the same numbers the original drawBat used.
const SPRITE_BAT_LIFT = -26;
function SPRITE_batBob(e, time) {
  return Math.sin(time * .008 + (e ? e.wob || 0 : 0)) * SPRITE_BAT_BOB;
}

function SPRITE_drawArt(g, pose, e, time) {
  const kind = pose.kind;
  if (kind === "bat") drawBat(g, e, time);
  else if (kind === "spider") drawSpider(g, e, time);
  else if (kind === "brutearcher") {
    // A parts bake draws ONE piece, and which piece comes from the bake's own function, not from a
    // flag that hides parts of the whole figure. That distinction is the point: a "skip the arm"
    // flag also skips the arm's shoulder translate and aim rotation, which is what made earlier
    // attempts bake a head floating where a bow should be. Each of the three functions draws its
    // own piece from its own body of code, so the bake cannot drift from the live drawing.
    const pf = pose.part && SPRITE_ARCHER_PARTS[pose.part];
    if (pf) pf.fn(g, e, time);
    else drawBruteArcher(g, e, time);
  }
  else if (kind === "fenris") drawFenris(g, e, time);
  else drawBrute(g, e, time, pose.ink);
}

// Set every piece of context state the renderers rely on, at an identity transform. Bakes must
// not inherit filter/shadow/composite/line state from whatever drew last on the shared scratch
// context, because that would bake a smeared or clipped sprite and then reuse it for hundreds
// of frames. The art sets most of this itself, but "most" is what makes bugs here expensive.
function SPRITE_state(g) {
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
  g.filter = "none";
  g.shadowColor = "transparent"; g.shadowBlur = 0;
  g.shadowOffsetX = 0; g.shadowOffsetY = 0;
  g.lineCap = "butt"; g.lineJoin = "miter"; g.miterLimit = 10; g.lineWidth = 1;
  g.strokeStyle = "#000"; g.fillStyle = "#000";
  g.textAlign = "start"; g.textBaseline = "alphabetic";
  g.imageSmoothingEnabled = true;
}

// The quantised pose of one entity, or null when it must be drawn live.
//
// `time` is the frame clock in ms that the renderers consume. The frame bucket uses each
// entity's own e.wob as a phase offset so a crowd does not march in lockstep, and because wob
// is baked into the offset the bucket is stable across frames: consecutive frames of one wolf
// walk through the same ordered set of buckets, which is the animation.
//
// The attack swing is quantised through its own parameter rather than the frame clock, because
// atkT advances by dt every frame and would otherwise be a new key every frame. The bucket stores
// the swing phase (0..1 across atkDur) and an atkT' reconstructed at that bucket's centre, which
// reproduces the art's sin((1 - atkT/atkDur) * PI) shape at a representative angle of each bucket:
// a winding-up wolf reads as winding up while sharing sprite slots with its neighbours. Because
// the reconstruction is exact in phase terms, the shared pose is at most half a bucket's worth of
// swing away from the live one — a quarter of the attack's arc at worst, and the same order of
// error the walk-cycle bucketing already accepts.
function SPRITE_pose(e, time) {
  const ek = SPRITE_ROUTE(e);
  if (!ek) return null;
  // `kind` below is the *art* being drawn; `ek` is the entity. They differ only for ironjaw, and
  // most of what follows (frames, period, clock, body box, renderer) belongs to the art while the
  // helmet flag and the cache key belong to the entity. Confusing the two is how ironjaws got baked
  // with plain brute art, so both names stay in scope rather than one being overwritten.
  const kind = SPRITE_KIND_ART[ek] || ek;
  const s = +(e.scale || 1);
  if (!isFinite(s) || s <= 0 || s > 2) return null;      // bosses: see the note above

  const nframes = SPRITE_FRAMES[kind];
  // The frame clock this art actually runs on, in rad/ms — see SPRITE_CLOCK above. Bucketing on it
  // rather than on the ms approximation in SPRITE_PERIOD is what keeps a cached pose in step with the
  // original drawing, so SPRITE_PERIOD no longer participates in the pose at all (it stays as
  // documentation of each body's visible cycle, and as the fallback SPRITE_warm reads).
  const rate = SPRITE_CLOCK[kind];
  const off = (e.wob || 0) * 1000;
  // Work in *cycles of the art's own limb*, not in ms. One cycle is TAU/rate ms of frame time, so
  // the bucket index and the sampled phase come from the same quantity and cannot drift apart: the
  // bucket spans exactly 1/nframes of a flap (TAU/nframes radians of limb travel) and the sample sits
  // at its centre. Deriving the bucket from ms and the sample from radians, as this used to, made the
  // two disagree by the rounding in SPRITE_PERIOD — the bat's 210 ms against a true 209.44 ms — and
  // that disagreement is the "flapping out of step with the original" the owner saw: the picture was
  // held on a pose the live art had already moved past, then jumped to catch up.
  const cyc = TAU / rate;                       // ms per full limb cycle for this art
  const ph = (((time + off) / cyc) % 1 + 1) % 1; // 0..1 through the limb cycle
  const fi = Math.min(nframes - 1, Math.floor(ph * nframes));
  const bakeTime = ((fi + .5) / nframes - (e.wob || 0)) * cyc;

  // Attack swing: 8 buckets across the attack's own arc, 0 meaning "not attacking". The phase is
  // (1 - atkT/atkDur), which runs 0 at the wind-up to 1 at the follow-through; the guard on
  // e.atkT > 0 keeps a resting wolf out of bucket 0's neighbourhood, and clamping keeps a
  // slightly-overdue dt from producing a phase outside (0,1).
  let atkB = 0;
  if (e.atkT > 0 && e.atkDur > 0) {
    const phase = clamp(1 - e.atkT / e.atkDur, 0, 1);
    atkB = Math.min(SPRITE_SWING_STEPS, 1 + Math.floor(phase * SPRITE_SWING_STEPS));
  }
  // Aim is not part of the pose: the archer's bow arm is baked once per body frame at the rest
  // heading and turned to the live aim when stamped, so no aim heading enters any key and no aim
  // bucket needs to exist. aimAng is carried as 0 so the replay draws the unrotated arm; the blit
  // applies the entity's live heading to that arm (SPRITE_blitArcherParts).
  const aimAng = 0;
  const sc = Math.round(s * 10);
  // The key carries the entity kind (so an ironjaw is never served a plate-less brute cell), the
  // facing the art was drawn in, the scale to 0.1, the walk frame and the attack bucket. Every
  // reachable value of each is enumerable and bounded, which is what lets SPRITE_warm cover the set
  // eagerly and lets the cell ceiling be a real bound rather than a hope. Facing is part of the key
  // for every kind (SPRITE_FACINGS) and nothing is blit-mirrored, so a cell is always the image of
  // the exact pose it is stamped for.
  //
  // Aim is absent from the key entirely: the archer is keyed as three pieces (pose.parts, from
  // SPRITE_archerKeys) and the live aim heading is applied when the arm is stamped, never baked.
  // Facing comes from the entity's own face through SPRITE_facing, clamped to ±1 so a stray
  // numeric face cannot smear into the key.
  const face = SPRITE_facing(e);
  const key = e.kind + "|" + face + "|" + sc + "|" + fi + "|" + atkB;

  // Bat hover, not baked. The ±4 px hover is a translation of the whole body: it carries
  // no silhouette of its own, and baking it would quantise a smooth 1267 ms hover into the walk
  // cycle's 12 frames — so the bat visibly jumped 1-2 px sideways-in-time with every wing frame,
  // out of step with the original. It is applied when the cell is blitted (SPRITE_blit reads
  // SPRITE_batBob with the live time), while the flap keeps its original .03 time and wob math and
  // stays inside the baked frame, because that genuinely is wing geometry.
  //
  // What the bake must NOT do is override what the renderer sees for the hover. An earlier version
  // pinned src.bakeBatBob to SPRITE_BAT_LIFT, reasoning that drawBat's translate already contains
  // the lift and the blit adds the live hover. It double-counted: drawBat's LIFT places the art at
  // its true height inside the cell (and the ink crop carries that height through), so feeding the
  // renderer an extra -26 moved the body 26 px further up inside its own bitmap — bats appeared
  // ~25 px above their shadow, the lift applied twice. drawBat is now replayed with no bake-time
  // hover field at all: lift once, hover supplied live at blit, nothing else.

  // Body box in device px, from the authored art extents. For mirrorable art the key drops facing
  // and the box is measured in the art's own (canonical right) facing.
  //
  // The box is only a starting frame for the crop; the real extents come from the ink scan, which
  // is what protects the look. That matters more than it sounds, because these authored boxes are
  // not tight and the art does not sit inside them: the bat's wings reach ~30 px from its origin
  // against a box that only allows 13 on the +x side, and the archer's club pokes past the top of
  // its box. Both are still captured — the generous canvas is sized so the art's body box is
  // centred in it, which leaves at least half a box of slack on every side, and the ink scan reads
  // the union of what was drawn. A box that under-reports its own art is therefore harmless here;
  // what would not be harmless is a *tight* box combined with a crop to the box, which is what a
  // naive implementation would do and why the crop is to the ink instead.
  const art = SPRITE_ART[SPRITE_KIND_ART[kind] || kind] || SPRITE_ART[kind];
  const w = Math.ceil((art.x1 - art.x0) * s) + SPRITE_PAD * 2;
  const h = Math.ceil((art.y1 - art.y0) * s) + SPRITE_PAD * 2;
  if (w > SPRITE_MAX_SIDE || h > SPRITE_MAX_SIDE) return null;
  return {
    kind: kind, key: key, face: face, w: w, h: h,
    // feet anchor: the art's (0,0) sits at (ax, ay) in the bake canvas, SPRITE_PAD in from the
    // left edge (so x0 lands at the pad) and SPRITE_PAD up from the bottom, leaving the pad for
    // stroke width and blur on every side.
    ax: Math.round(SPRITE_PAD - art.x0 * s), ay: h - SPRITE_PAD,
    bakeTime: bakeTime, swing: atkB, scale: s,
    // Archer only: the three-part bake (body / bow arm / head), each with its own key that contains
    // no aim heading. Null for every other kind, which keeps baking the whole figure as one sprite.
    parts: kind === "brutearcher" ? SPRITE_archerKeys(face, fi, atkB, s) : null,
    // Render inputs the replay has to reproduce exactly. atkT' is the bucket centre inverted out
    // of the art's own phase definition — atkT = (1 - phase) * atkDur — so the renderer's
    // sin((1 - atkT/atkDur) * PI) evaluates at the centre of this bucket, and the stored pose is
    // at most half a bucket (1/16 of the arc) from whatever the entity was actually doing. It is
    // strictly inside (0, atkDur), so a baked attack pose is never the zero-valued rest pose and
    // never reads as a different attack.
    aimAng: aimAng, atkDur: e.atkDur || 1,
    // The helmet. drawBrute(g, e, time, ironjaw) is one renderer for two looks, and the only thing
    // that distinguishes them is this 4th argument. It has to be asked from the *entity*: `kind` is
    // already collapsed onto the shared "brute" art by SPRITE_KIND_ART, which is right for frames,
    // period, clock and body box but erases exactly the distinction that puts the metal helm on. The
    // art kind is what used to be consulted here, which is how ironjaws got baked plate-less.
    // The routed kind is still in `ek` precisely so this line can tell the two apart.
    ink: ek === "ironjaw",
    atkT: atkB ? (1 - (atkB - .5) / SPRITE_SWING_STEPS) * (e.atkDur || 1) : 0,
    // Carried to the crop so the cell can tell the blit whether mirroring is legal for this art.
    noMirror: SPRITE_NO_MIRROR[e.kind] === true,
  };
}

// Bake one pose. Returns {cv, ax, ay, w, h, mirror, snap} or null to mean "draw this one live".
//
// The pose is replayed into the scratch canvas at an explicit identity transform with the feet
// anchor placed so the authored body box lands inside the pad, then the ink bounds are measured
// and the canvas is cropped to ink + pad. Cropping matters for more than tidiness: an enemy is
// roughly 70x60 of art inside a box that has to accommodate blur, and blitting the tight sprite
// is fewer composited pixels per frame per actor.
//
// The art functions apply g.scale(e.face, 1) internally, so each facing is baked as its own cell
// (SPRITE_FACINGS) and nothing is mirrored at blit. The bake renders on a COPY of the entity
// (SPRITE_bakeSource), never on the entity itself: a bake that wrote to the live enemy would leave
// it facing right with a borrowed attack clock if any line between the write and its restore threw,
// and "drawing an enemy must never change that enemy" is the invariant this class of bug keeps
// producing. A copy cannot leak, so there is nothing to restore and nothing to corrupt.
//
// A bake canvas may be a little larger than a finished cell: the crop only has to fit inside
// SPRITE_MAX_SIDE, and it starts from a canvas that already carries the pad.
const SPRITE_MAX_BAKE_SIDE = SPRITE_MAX_SIDE + SPRITE_PAD * 2;

function SPRITE_bake(src, pose) {
  const sc = SPRITE_SCRATCH || (SPRITE_SCRATCH = document.createElement("canvas"));
  // Generous first pass: body box plus room for a shadow smear, then crop.
  const gw = pose.w + SPRITE_PAD * 2, gh = pose.h + SPRITE_PAD * 2;
  // The uncropped bake canvas is the cell plus the pad on every side, so this limit has to be the
  // cell limit plus 2*PAD. Comparing against SPRITE_MAX_SIDE instead rejects every pose whose
  // padded box exceeds it — which, because pose.w already includes most of the pad, is every pose
  // there is. That failure is silent by construction: the bake returns null, spriteFor counts a
  // skip, and the game just keeps drawing procedurally at full speed with an empty cache, so it
  // looks like a slow cache rather than a disabled one.
  if (gw > SPRITE_MAX_BAKE_SIDE || gh > SPRITE_MAX_BAKE_SIDE) return null;
  // Grow-only. Every kind's bake box is roughly the same size, so this assigns the same dimensions
  // on nearly every call and browsers short-circuit that without clearing; where it does differ,
  // width is not clamped to the old bitmap, so the larger of the two sizes is always available and a
  // small first bake can never truncate a later big one. The clearRect below is what actually resets
  // the region, which is why sizing before it is safe.
  if (sc.width < gw) sc.width = gw;
  if (sc.height < gh) sc.height = gh;
  let g = null;
  try { g = sc.getContext("2d", { willReadFrequently: true }); } catch (err) { return null; }
  if (!g) return null;
  SPRITE_state(g);
  // Clear the whole backing store, not just gw x gh: the canvas may be larger than this bake.
  g.clearRect(0, 0, sc.width, sc.height);

  const art = SPRITE_ART[pose.kind];
  // Whether the blit may mirror this cell, recorded once here from the art that was actually baked.
  // A pose built without the flag is treated as *not* mirrorable, which is the safe direction: the
  // cost of defaulting the other way is a silently wrong picture (a left-facing enemy drawn from a
  // right-facing bake, or a bow pointing away from its shot), whereas defaulting this way only ever
  // costs one extra cell.
  // to flip it after SPRITE_NO_MIRROR went empty — the flag was being ignored by the table rather
  // than by the default, so the bug survived the table change.
  if (pose.noMirror === undefined) pose.noMirror = true;
  // Place the art's (0,0) so its body box sits centred in the generous canvas, which is also what
  // leaves at least half a body box of slack on every side for art that overflows its authored box.
  const ax = Math.round(gw / 2 - ((art.x0 + art.x1) / 2) * pose.scale);
  const ay = gh - SPRITE_PAD - Math.round(Math.max(0, art.y1) * pose.scale);

  // Draw the pose into the scratch canvas. SPRITE_replay writes only onto `src`, a throw-away copy,
  // so there is no restore path to get wrong and no window in which the live entity is standing in
  // a borrowed pose.
  g.translate(ax, ay);
  try {
    SPRITE_replay(g, src, pose);
  } finally {
    SPRITE_state(g);                       // drop any shadow/filter/transform the art left behind
    SPRITE_BAKES++;
  }
  // Where the authored body box is centred in the scratch canvas. The bake deliberately places the
  // box centred, so this is gw/2 by construction; carrying it to the cell is what lets the blit
  // mirror about the *body* rather than about the cropped bitmap. See SPRITE_crop and SPRITE_blit.
  const cx = gw / 2, cy = gh - SPRITE_PAD;
  // Whether the blit may mirror, decided here from the art actually baked and passed into the crop so
  // cell.mirror is set as part of construction rather than patched afterwards. Today every routed
  // kind bakes each facing it can be seen in (SPRITE_FACINGS), so this is false for all of them; a
  // kind absent from that table also gets false, because the safe failure is one extra cell and
  // never a backwards-facing enemy.
  // backwards-facing enemy.
  const faces = SPRITE_FACINGS[pose.kind];
  const MAY_MIRROR = !(pose.noMirror === false || !faces || faces.length > 1);
  const cell = SPRITE_crop(g, gw, gh, ax, ay, cx, cy, MAY_MIRROR);
  // cell.mirror comes back from the crop as computed above; there is deliberately no second write of
  // the flag after construction. (An earlier revision here read `cell.mirror = pose.noMirror !==
  // true`, which parses as `(cell.mirror = pose.noMirror) !== true`: the assignment happened and its
  // value was discarded in favour of a comparison, so every cell came back mirror:true whatever the
  // pose said. Carrying the flag through the crop removes the opportunity to repeat that.)
  // thrown away in favour of a boolean comparison, so every cell came back mirror:true whatever the
  // pose said — and the table change above appeared to do nothing. Kept out of the way by having the
  // cell carry the flag from the crop, so there is no second write left to get wrong.
  return cell;
}

// The throw-away entity a bake renders: every own enumerable field of the live entity, with the
// pose's quantised inputs written over them. Deliberately a shallow copy — the renderers read only
// scalars off an entity (kind, face, scale, wob, atkT/atkDur, aimAng, r, boss) and never reach into
// nested objects — so a shallow copy satisfies the whole contract and stays correct if a scalar the
// art reads is later added, without this function needing to be revisited.
function SPRITE_bakeSource(e, pose) {
  const src = Object.assign({}, e);
  // Bake at the entity's real facing: every routed kind has one cell per facing (SPRITE_FACINGS)
  // and nothing is blit-mirrored, so the renderer's own g.scale(face, 1) draws exactly the picture
  // the original drawing makes, pivot and all. Forcing face 1 here and flipping the bitmap at the
  // blit was the approximation that put left-facing brutes off where they belong.
  src.face = pose.face;
  src.aimAng = pose.aimAng;
  src.atkT = pose.atkT;
  src.atkDur = pose.atkDur;
  return src;
}

// Render `pose` through the real renderer for this art. `src` is the copy from SPRITE_bakeSource,
// so writing face / aimAng / the attack clock onto it is free. Everything else (x/y are irrelevant
// to the art, r is used only for the shadow which ui.js draws, hurtT is an overlay rather than a
// bake input) is read live off the copy.
function SPRITE_replay(g, src, pose) {
  SPRITE_drawArt(g, pose, src, pose.bakeTime);
}

// Measure the ink the scratch canvas received and crop it into a private canvas.
function SPRITE_crop(g, gw, gh, ax, ay, cx, cy, MAY_MIRROR) {
  const ink = SPRITE_ink(g, gw, gh);
  if (!ink) return null;
  const pad = SPRITE_PAD;
  let x0 = Math.max(0, ink.x0 - pad), y0 = Math.max(0, ink.y0 - pad);
  let x1 = Math.min(gw, ink.x1 + 1 + pad), y1 = Math.min(gh, ink.y1 + 1 + pad);
  const w = x1 - x0, h = y1 - y0;
  if (w < 2 || h < 2 || w > SPRITE_MAX_SIDE || h > SPRITE_MAX_SIDE) return null;
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  let dg = null;
  try { dg = cv.getContext("2d"); } catch (err) { return null; }
  if (!dg) return null;
  SPRITE_state(dg);
  dg.drawImage(g.canvas, x0, y0, w, h, 0, 0, w, h);
  SPRITE_state(dg);
  return {
    cv: cv, w: w, h: h,
    // Whether the blit may mirror this cell. Passed in by SPRITE_bake from the pose that produced
    // these pixels — the crop itself is about pixels and knows nothing about facing policy. Today
    // every kind bakes each facing it can be seen in (SPRITE_FACINGS), so this is false for all of
    // them and the blit never flips anything.
    mirror: MAY_MIRROR,
    // anchor of the entity origin (feet, x = 0) inside the cropped sprite
    ax: ax - x0, ay: ay - y0,

    // Where the entity origin lands inside the mirrored bitmap. The bake produces right-facing
    // pixels and the blit mirrors them with g.scale(-1, 1) about the entity origin, so the feet
    // column moves from ax to (w - ax). Carried as a field so SPRITE_blit is a lookup and every
    // caller cannot re-derive it and drift — three different "obvious" derivations were tried here
    // and each put the sprite up to a body-width off for some kinds.
    axMirror: w - (ax - x0),
    cx: cx - x0, cy: cy - y0,
  };
}




// The sprite for this entity's current quantised pose, or null to draw it procedurally.
//
// Never throws and never leaves a half-restored entity: the bake's own finally handles that. On a
// cache miss with the cell budget exhausted the entity is drawn live, which is the correct
// degradation — the game looks exactly as it did before this cache existed.

function spriteFor(e, time) {
  const pose = SPRITE_pose(e, time);
  if (!pose) { SPRITE_SKIPS++; return null; }
  // An archer pose is served as three separately keyed pieces (SPRITE_pose sets pose.parts) rather
  // than a whole-figure cell, so the whole-figure lookup below never sees its key. The parts are
  // resolved and stamped by SPRITE_blitArcherParts on the ui.js side; this function returning null
  // for an archer is normal, not a miss — that is why the archer branch returns before SPRITE_SKIPS.
  if (pose.parts) return null;

  let hit = SPRITE_CACHE.get(pose.key);
  if (hit) return hit;
  SPRITE_MISSES++;
  // Ceiling, with recovery. The reachable key set is larger than any comfortable constant once
  // attack swings are keyed at all (roughly 1.5k idle cells plus a swing bucket per kind per facing
  // per frame of the arc), so hitting this is a real state in a long fight, not a theoretical one.
  // Dropping the oldest entries and carrying on is what a cache does. The previous behaviour here —
  // refuse to cache anything once the map was full — silently turned a full cache back into the
  // permanent slow path: enemies kept drawing procedurally every frame and nothing new was stored.
  //
  // A Map iterates in insertion order, so the first key is the oldest bake. Eviction is bounded to
  // a fraction of the map per miss so a pathological key churn costs a slice of bakes rather than
  // re-baking the working set every frame.
  if (SPRITE_CACHE.size >= SPRITE_MAX_CELLS) {
    SPRITE_EVICTED++;
    // Sustained churn: stop trying, so a cache that cannot hold the fight costs one lookup per enemy
    // per frame instead of a bake. SPRITE_stats().thrashed reports it; this must never be silent.
    if (SPRITE_EVICTED > SPRITE_THRASH_LIMIT) { SPRITE_THRASHED = true; SPRITE_SKIPS++; return null; }
    spriteCacheEvict();
  }
  // One bake, for the pose as asked — including its facing. The canonical-facing alias that used to
  // live here folded left-facing lookups onto a right-facing cell; with every kind baked per facing
  // there is nothing left to alias, and an alias is exactly what a left-facing entity must not get.
  //
  // Bake from a copy: the live entity is never a render input, so it cannot be mutated by a miss.
  let baked = null;
  try { baked = SPRITE_bake(SPRITE_bakeSource(e, pose), pose); } catch (err) { baked = null; }
  if (!baked) { SPRITE_SKIPS++; return null; }
  SPRITE_CACHE.set(pose.key, baked);
  return baked;
}

// One archer piece, baked and cropped on its own. Same frame and anchor as the whole-figure bake —
// the piece is drawn into the identical canvas at the identical origin, and cropped to its own ink —
// so a piece's cell has exactly the same stamp maths as any other cell and the three land in register
// with each other. The arm cell additionally records where its aim pivot lands inside the bitmap,
// derived from the same SPRITE_ARCHER_ARM_ANCHOR that the drawing function and the blit use, so no
// second source for that number exists; it is device px, which is what drawImage needs.
function SPRITE_bakeArcherPart(e, pose, name) {
  const p = SPRITE_ARCHER_PARTS[name];
  if (!p) return null;
  const src = SPRITE_archerSource(e, pose, name);
  const art = SPRITE_ART.brutearcher;
  const s = pose.scale;
  const w = Math.ceil((art.x1 - art.x0) * s) + SPRITE_PAD * 2;
  const h = Math.ceil((art.y1 - art.y0) * s) + SPRITE_PAD * 2;
  if (w > SPRITE_MAX_BAKE_SIDE || h > SPRITE_MAX_BAKE_SIDE) return null;
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  const g = cv.getContext("2d");
  if (!g) return null;
  g.translate(pose.ax, pose.ay);
  pose.part = name;                      // SPRITE_drawArt routes the archer to this piece
  try { SPRITE_replay(g, src, pose); } finally { pose.part = null; }
  const cell = SPRITE_crop(g, w, h, pose.ax, pose.ay, w / 2, h - SPRITE_PAD, false, false);
  if (!cell) return null;
  if (name === "arm") {
    // The shoulder pivot inside the cropped bitmap, in device px. cell.ax/ay give the entity origin
    // inside the bitmap; the anchor is in the same art units the drawing translated by, at this
    // cell's scale.
    cell.armX = cell.ax + SPRITE_ARCHER_ARM_ANCHOR[0] * s;
    cell.armY = cell.ay + SPRITE_ARCHER_ARM_ANCHOR[1] * s;
  }
  return cell;
}

// The archer's cached drawing: three separately keyed pieces, looked up in the same Map on the same
// insertion-order LRU as every other kind. Returns {body, arm, head} when all three are available and
// null otherwise, in which case the caller draws the archer procedurally exactly as before — a part
// is never drawn without the others, so a bake failure costs the slow path, never a half-drawn brute.
//
// Unlike spriteFor this returns three cells rather than one, and it never bakes: baking a part needs
// its own canvas and its own crop, and is done once per part by SPRITE_bakeArcherPart below.
function archerSpriteFor(e, pose, time) {
  const k = pose.parts;
  let body = SPRITE_CACHE.get(k.body);
  if (body === undefined) body = archerPartMiss(e, pose, "body", k.body);
  let arm = SPRITE_CACHE.get(k.arm);
  if (arm === undefined) arm = archerPartMiss(e, pose, "arm", k.arm);
  let head = SPRITE_CACHE.get(k.head);
  if (head === undefined) head = archerPartMiss(e, pose, "head", k.head);
  if (!body || !arm || !head) { SPRITE_SKIPS++; return null; }
  return { body: body, arm: arm, head: head };
}

// One part lookup that missed: bake that one piece and store it under its own key. Shares the whole-
// figure cache's ceiling and thrash accounting, so the archer's parts cannot quietly grow the map
// past what eviction handles; a part that cannot be stored costs one bake, not a slow-path flip.
function archerPartMiss(e, pose, name, key) {
  SPRITE_MISSES++;
  if (SPRITE_CACHE.size >= SPRITE_MAX_CELLS) {
    SPRITE_EVICTED++;
    if (SPRITE_EVICTED > SPRITE_THRASH_LIMIT) { SPRITE_THRASHED = true; SPRITE_SKIPS++; return null; }
    spriteCacheEvict();
  }
  let cell = null;
  try { cell = SPRITE_bakeArcherPart(e, pose, name); } catch (err) { cell = null; }
  if (!cell) { SPRITE_SKIPS++; return null; }
  SPRITE_CACHE.set(key, cell);
  return cell;
}

// Drop every baked sprite. Required whenever the art changes without a page reload, and cheap
// enough to call on zone/level changes.
function spriteCacheClear() {
  SPRITE_CACHE.clear();
  SPRITE_BAKES = 0; SPRITE_SKIPS = 0; SPRITE_MISSES = 0; SPRITE_EVICTED = 0; SPRITE_THRASHED = false;
}

// Drop the oldest fraction of the cache so a full map keeps accepting new poses instead of freezing.
// Insertion order is the recency order here, and the loop re-checks the ceiling each pass so calling
// it when there is nothing to give back is a no-op rather than a surprise wipe.
function spriteCacheEvict() {
  const target = Math.floor(SPRITE_MAX_CELLS * SPRITE_EVICT_FRACTION);
  if (SPRITE_CACHE.size < SPRITE_MAX_CELLS) return 0;
  let n = 0;
  for (const k of SPRITE_CACHE.keys()) {
    SPRITE_CACHE.delete(k);
    if (++n >= SPRITE_MAX_CELLS - target) break;
  }
  return n;
}

// Diagnostics for the PROF readout: how much of the crowd is actually going through the cache.
function SPRITE_stats() {
  return {
    cells: SPRITE_CACHE.size, bakes: SPRITE_BAKES, skips: SPRITE_SKIPS, misses: SPRITE_MISSES,
    evicted: SPRITE_EVICTED, thrashed: SPRITE_THRASHED === true,
  };
}

// ---------------------------------------------------------------------------
// Load-time warm-up: bake the whole normal-enemy idle pose set once at level load so no fight
// ever bakes mid-frame and nothing ever falls back to the slow procedural path. The pose space
// here is closed: kinds, facings, frame counts and the archer's three pieces are all constants.
//
// The eager set is the idle cycle over both facings of every kind. Whole figures:
//   brute 2x12 + ironjaw 2x12 + bat 2x12 + spider 2x12 + fenris 2x14 = 124
// The archer is three separately keyed pieces per (facing, frame), keyed without aim:
//   body 2x12 + arm 2x12 + head 2x12                                  =  72
//   total                                                             = 196 cells
// Against SPRITE_MAX_CELLS that is a comfortable load with room left for on-demand attack swings.
// (Before the archer was split, the aim heading was part of the whole-figure key and the archer
// alone cost 2 x 12 x SPRITE_AIM_STEPS = 864 cells, 88% of the eager set. It is now 37% — and the
// aim resolution did not drop, because the live arm is turned to the exact heading every frame
// instead of being snapped to a bucket.) Load cost is one bake plus one getImageData per cell.
//
// The stand-in entity is legal per the art's own contracts: every renderer reads kind, face,
// scale, wob, atkT/atkDur and (archer only) aimAng, all of which are set below; legSwing and the
// sinusoid clocks tolerate any wob; and SPRITE_bake sizes and crops from the same SPRITE_ART
// extents it uses live. Bosses are absent because SPRITE_ROUTE excludes them.
//
// `kinds` is optional and defaults to the full routed set; both facings are always covered.
function SPRITE_warm(kinds) {
  const list = Array.isArray(kinds) && kinds.length
    ? kinds
    : ["brute", "ironjaw", "brutearcher", "bat", "spider", "fenris"];
  // A legal stand-in entity, not a guess at one: it carries exactly the fields the routed
  // renderers read (kind, face, scale, wob, atkT/atkDur, and aimAng for the archer) plus the body
  // box fields SPRITE_pose reads. wob 0 and aimAng 0 are inside every art's accepted range, and
  // x/y/r are irrelevant to a renderer that draws at its own local origin.
  const standIn = {
    kind: "brute", x: 0, y: 0, face: 1, r: 16, scale: 1,
    atkT: 0, atkDur: 1, aimAng: 0, hurtT: 0, wob: 0,
  };
  for (const kind of list) {
    const nframes = SPRITE_FRAMES[kind] || SPRITE_FRAMES.brute;
    const per = SPRITE_PERIOD[kind] || SPRITE_PERIOD.brute;
    standIn.kind = kind;
    // Both facings, baked for real: the key carries the facing and nothing is mirrored at the blit
    // any more (SPRITE_FACINGS). This loop is the eager half of that promise — an entity walking in
    // from the left must find its cell already baked, not take a mid-fight bake for a pose that was
    // known at load time.
    const faces = SPRITE_FACINGS[kind] || [1];
    for (const face of faces) {
      standIn.face = face;
      for (let fi = 0; fi < nframes; fi++) {
        // Any time that lands in bucket fi on this art's own limb clock; SPRITE_pose does the
        // bucketing and picks the sampled phase, so warm-up must not try to reproduce either. The
        // art's cycle is TAU/rate ms (SPRITE_CLOCK), *not* the tidy ms approximation in SPRITE_PERIOD
        // — feeding a time from the wrong scale fills the wrong buckets and leaves real poses cold,
        // which shows up as a mid-fight bake rather than as an error. wob is 0 for every warmed pose,
        // so the pose's own bucket and this time agree.
        const cyc = TAU / (SPRITE_CLOCK[kind] || SPRITE_CLOCK.brute);
        const warmTime = (fi + .5) / nframes * cyc;
        // Aim is not an axis of the sprite any more, so one pass per facing and frame fills the
        // eager set. (Before the split this loop ran SPRITE_AIM_STEPS times per facing and frame,
        // baking a whole aiming figure each time.) standIn.aimAng stays 0: the arm bake overrides
        // it with the rest heading, so the warm arm is the same arm a live aim is turned from.
        standIn.aimAng = 0;
        // Idle swing (0) for every frame: the attack buckets are baked on demand. Covering them
        // eagerly would multiply this loop by nframes again for a pose that lasts a few frames,
        // and with the ceiling far above the working set a miss there is one bake on the first
        // attack frame, never a slow-path fallback.
        standIn.atkT = 0;
        // Archer: warm the three pieces, because a whole-figure lookup returns null for them
        // (spriteFor defers to pose.parts). Every other kind warms its whole-figure cell.
        if (kind === "brutearcher") {
          const wp = SPRITE_pose(standIn, warmTime);
          if (wp && wp.parts) {
            for (const name of ["body", "arm", "head"]) {
              if (!SPRITE_CACHE.get(wp.parts[name])) archerPartMiss(standIn, wp, name, wp.parts[name]);
            }
          }
        } else {
          SPRITE_cell(standIn, warmTime);
        }
      }
    }
  }
}

// The cell for `e`'s current pose, or null when the pose is not cacheable. Thin wrapper so the
// call site in ui.js reads as one intent and the quantisation and miss accounting stay here.

function SPRITE_cell(e, time) {
  return spriteFor(e, time);
}

// Draw a cached cell for `e`, replacing the procedural call in drawEnemyEnt. Returns false when
// there is nothing to blit, so the caller draws the pose live; true means the sprite is on screen
// and the caller still owes only the hit ring and the health bar, which are painted over the
// sprite rather than baked into it.
//
// The world transform already carries the camera translate, so the sprite goes at (e.x, e.y).
// The shadow is drawn first by the caller, as it was before.
//
// Two cases, decided by cell.mirror rather than by guessing from the kind:
//
//   mirror — the cell holds canonical right-facing pixels, so a left-facing entity is drawn by
//   mirroring the blit. The anchor has to be re-derived after the flip (cell.ax measured from the
//   far edge) or every left-facing enemy draws a body width out of place — the one thing here that
//   is easy to get wrong.
//
//   !mirror — the cell was baked at the entity's own facing, because this art has an orientation-
//   specific prop that must not be caught up in a blit mirror (a bow that has to keep pointing at
//   its target). No flip: the cell's own anchor is used. This is what fixes left-facing archers
//   aiming backwards.
//
// Resolution is untouched either way: no scale is applied at blit time, and the cell lands 1:1 on
// the same device pixels the procedural art occupied.
// Stamp a whole-figure cell. Called from drawEnemyEnt inside the entity's own transform, so (0, 0)
// here is the entity's feet. SPRITE_blit draws nothing else and moves nothing else, which is what
// keeps the overlays (hit ring, hp pip) in the caller's coordinate system. `time` is the same clock
// the frame passed to the procedural renderer, and is only used for the bat's hover (see
// SPRITE_BAT_BOB).

function SPRITE_blit(g, cell, e, time) {
  if (!cell || !cell.cv) return false;
  const flip = cell.mirror !== false && e.face < 0;
  // Mirroring has to reproduce what the art does natively: g.scale(face, 1) about the entity origin
  // (feet), after which the bitmap's left edge sits at +ax. So px = +ax when flipped, and the feet —
  // at sprite x = ax, rendered at (dx + w - ax) under the flip — land at ax + w - ax = w... which is
  // why the anchor is carried instead of derived: under the flip the feet column is w - ax from the
  // bitmap's left edge, and putting *that* on the origin is the whole job.
  const px = flip ? -cell.axMirror : -cell.ax;
  g.save();
  if (flip) g.scale(-1, 1);
  // The cell's ax/ay are the coordinates, *inside the sprite*, of the entity origin — the feet, at
  // x = 0. So the top-left has to go at (-ax, -ay): that places sprite point (ax, ay) exactly on the
  // entity origin, which is where the procedural art's (0,0) sits.
  //
  // Drawing the top-left at (ax, ay - h) instead is an old anchor convention misapplied (it assumes
  // ax/ay name the sprite's own top-left, which they don't), and it shifts the sprite by roughly
  // (+ax, +ay - h) — below and to the right of its contact shadow, which is drawn at the true
  // entity position and so stays put. It is visible on every kind, and the offset is not constant
  // across them, because ax/ay come from each art's own body box: brute +46,-14; ironjaw +51,-9;
  // fenris +47,-13; spider +27,-53; bat +17,-28.
  //
  // The bat's hover, applied here rather than baked: the cell holds the body with the hover pinned
  // to 0, so this offset is exactly what the original renderer would have translated by at this
  // instant. Live time, live wob, same formula as drawBat. Keyed off the entity, not the cell —
  // the cell has no `kind` field, and a guard that silently never fires is how the hover ended up
  // frozen into the sprite and jumping with the wing frames.
  const bobY = e.kind === "bat" ? SPRITE_batBob(e, time) : 0;
  g.drawImage(cell.cv, px, -cell.ay + bobY, cell.w, cell.h);
  g.restore();
  return true;
}

// Stamp a cached archer: body, then the bow arm under the live aim transform, then the head. Same
// order drawBruteArcher uses, and the arm transform here is the same one drawBruteArcherBowArm
// applies — translate to the shoulder, rotate by the heading, flip vertically for left-facing — with
// the only difference being that the rotation is applied to a sprite recorded at aim 0 instead of to
// vector art drawn at that heading. `parts` is what archerSpriteFor returned; null draws nothing and
// leaves the caller to the procedural path.
//
// The pieces are all in the entity's own transform (feet at the origin), and each cell carries its
// own anchor, so each stamp lands where its function drew it. Called inside drawEnemyEnt's entity
// transform, like SPRITE_blit.
function SPRITE_blitArcherParts(g, parts, e, time) {
  if (!parts || !parts.body || !parts.arm || !parts.head) return false;
  // Body. The body bake is per facing and its own function already applies the horizontal flip the
  // art uses, so the stamp is unmirrored — cell.mirror is false for archer parts (see below).
  if (!SPRITE_blit(g, parts.body, e, time)) return false;
  // Bow arm: the one live rotation. Re-create the drawing function's transform, then stamp the
  // aim-0 sprite into it, so the recorded arm turns about the shoulder exactly as the vector did.
  const arm = parts.arm;
  g.save();
  g.translate(SPRITE_ARCHER_ARM_ANCHOR[0], SPRITE_ARCHER_ARM_ANCHOR[1]);
  g.rotate(e.aimAng || 0);
  if (e.face < 0) g.scale(1, -1);
  // The sprite was recorded with its shoulder pivot at armX/armY inside the bitmap, so put that
  // point on this transform's origin (which is the shoulder).
  g.drawImage(arm.cv, -arm.armX, -arm.armY, arm.w, arm.h);
  g.restore();
  // Head.
  if (!SPRITE_blit(g, parts.head, e, time)) return false;
  return true;
}
