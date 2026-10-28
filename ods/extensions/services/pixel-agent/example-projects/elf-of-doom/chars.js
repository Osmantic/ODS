// Elf of Doom — characters (cartoon canvas art)
"use strict";

// ---------- hero: Gabriel with elven bow ----------
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
function drawBruteArcher(g, e, time) {
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
  // bow arm + bow, aimed at aimAng (unflipped, so it aims at the hero)
  const drawP = e.atkT > 0 ? Math.sin((1 - e.atkT / e.atkDur) * Math.PI) : 0;
  g.save(); g.translate(3, -25); g.rotate(e.aimAng || 0);
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
function drawBat(g, e, time) {
  const flap = Math.sin(time * .03 + e.wob) * .8;
  g.save(); g.translate(0, -26 + Math.sin(time * .008 + e.wob) * 4); g.scale(e.face, 1);
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
