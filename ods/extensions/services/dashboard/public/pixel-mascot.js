/* Portal's soft-square character. The PixelMascot API stays compatible with saved integrations. */
"use strict";

(() => {
  const STATES = Object.freeze(["idle", "working", "waiting", "blocked", "thinking", "done", "sleeping"]);
  const TAU = Math.PI * 2;
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const wave = (time, period) => Math.sin(time * TAU / period);
  const GESTURES = Object.freeze({hello:1.8, 'hop-left':1.45, 'hop-right':1.45, squish:1.65, wink:1.7, twirl:2.1, stretch:2.2, perk:1.1, nod:1.2, starstruck:2.5});
  const PLAY_SEQUENCE = ['hop-left', 'hop-right', 'squish', 'wink', 'twirl'];

  // Gestures decorate a pose, never replace an actual task state.
  function sampleGesture(name, seconds = 0, direction = 1) {
    const duration = GESTURES[name] || 0;
    const g = {x:0,y:0,rotate:0,sx:1,sy:1,lookX:0,lookY:0,eyeOpen:1,leftOpen:1,rightOpen:1,happy:0,blush:0,sparkle:0};
    if (!duration || !Number.isFinite(seconds) || seconds <= 0 || seconds >= duration) return g;
    const u = seconds / duration;
    const e = Math.sin(Math.PI * u) ** 2;
    const bounce = Math.sin(Math.PI * clamp((u - .12) / .7, 0, 1));
    if (name === 'hello') {
      g.rotate = 12 * e * Math.sin(u * TAU * 1.5); g.y = -5 * e;
      g.eyeOpen = 1 + .18 * e; g.happy = .4 * e;
    } else if (name.startsWith('hop-')) {
      const side = name === 'hop-left' ? -1 : 1;
      g.x = side * 9 * e; g.y = -15 * bounce;
      g.rotate = side * 13 * e; g.sx = 1 - .075 * bounce; g.sy = 1 + .09 * bounce;
      g.blush = .25 * e;
    } else if (name === 'squish') {
      g.sx = 1 + .17 * e; g.sy = 1 - .18 * e; g.y = 6 * e;
      g.eyeOpen = 1 - .5 * e; g.happy = .8 * e; g.blush = .35 * e;
    } else if (name === 'wink') {
      g.rotate = -9 * e; g.leftOpen = 1 - .96 * Math.sin(Math.PI * u) ** 6;
      g.lookX = 2 * e; g.blush = .3 * e;
    } else if (name === 'twirl') {
      // A tiny side-to-side dance, not a full screen-spinning rotation.
      g.rotate = direction * 24 * e * Math.sin(u * TAU * 1.5);
      g.x = direction * 7 * e * Math.sin(u * TAU); g.y = -6 * e;
      g.sx = 1 - .06 * e; g.sy = 1 + .06 * e; g.happy = .8 * e;
    } else if (name === 'stretch') {
      g.sy = 1 + .16 * e; g.sx = 1 - .09 * e; g.y = -5 * e;
      g.eyeOpen = 1 - .7 * Math.sin(Math.PI * clamp(u / .65, 0, 1)) ** 2;
      g.rotate = -8 * e;
    } else if (name === 'perk') {
      g.y = -3 * e; g.rotate = -5 * e; g.eyeOpen = 1 + .18 * e;
    } else if (name === 'nod') {
      g.y = 3 * e * Math.sin(u * TAU); g.rotate = 5 * e;
    } else if (name === 'starstruck') {
      g.y = -12 * e; g.rotate = 12 * e * Math.sin(u * TAU * 2);
      g.happy = e; g.blush = .5 * e; g.sparkle = e;
      g.sx = 1 + .06 * e; g.sy = 1 - .04 * e;
    }
    return g;
  }
  const PALETTE = Object.freeze({
    idle: [229, 229, 228], thinking: [226, 220, 240], working: [205, 217, 238],
    waiting: [236, 219, 193], blocked: [230, 204, 193], done: [236, 229, 221], sleeping: [216, 218, 221],
  });

  // Pure, deterministic poses make motion inspectable without a running agent.
  // Time is elapsed within a state, never a source of invented execution state.
  function samplePose(state = "idle", seconds = 0, { reduced = false, settled = false } = {}) {
    if (!STATES.includes(state)) state = "idle";
    const t = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
    const still = reduced || settled;
    const [red, green, blue] = PALETTE[state];
    const p = { x: 0, y: 0, rotate: -3, sx: 1, sy: 1, round: 19, lookX: 0, lookY: 0, eyeOpen: 1, leftOpen:1, rightOpen:1, eyeTilt: 0, happy: 0, red, green, blue, blush: 0, sparkle:0, question:0, lens:0, propX:0, propY:0, propRotate:0, gear:0, gearRotate:0, hourglass:0, waitRotate:0, grainY:0, attention:0, sleepX:0, sleepY:0, sleepOpacity:.7 };
    if (state === "sleeping") {
      const breath = still ? 0 : wave(t, 4.6);
      p.eyeOpen = .08; p.rotate = 12 + 1.8 * breath; p.y = 4 - 1.2 * breath;
      p.sx = 1 + .025 * breath; p.sy = .94 - .03 * breath;
      p.sleepX = still ? 0 : 2.5 * wave(t, 5.4);
      p.sleepY = still ? 0 : -3 - 3 * wave(t, 4.6);
      p.sleepOpacity = still ? .7 : .65 + .22 * wave(t, 4.6);
    } else if (state === "idle") {
      p.y = still ? 0 : -1.2 * wave(t, 4.8);
      p.sx = 1 + (still ? 0 : .012 * wave(t, 4.8));
      p.sy = 1 - (still ? 0 : .014 * wave(t, 4.8));
      p.lookX = still ? 0 : 1.7 * wave(t, 9.6);
      const curiosity = still ? 0 : Math.sin(Math.PI * clamp(((t + 7) % 23 - 18) / 3, 0, 1)) ** 2;
      p.rotate -= 6 * curiosity; p.lookY = -2 * curiosity; p.eyeOpen += .1 * curiosity;
    } else if (state === "thinking") {
      p.rotate = -10 + (still ? 0 : 8 * wave(t, 3.2));
      p.y = -2 + (still ? 0 : 3.5 * wave(t, 3.2));
      p.x = still ? 0 : 1.6 * wave(t, 8.8);
      p.sx = .97 + (still ? 0 : .015 * wave(t, 4.4)); p.sy = 1.035 - (still ? 0 : .02 * wave(t, 4.4)); p.round = 22;
      p.lookX = 3 + (still ? 0 : 2.5 * wave(t, 6.8));
      p.lookY = -5; p.eyeOpen = .92;
      // Visual thinking metaphors, not claims of web/search tool execution.
      const phase = t % 8;
      p.question = still ? 1 : Math.sin(Math.PI * clamp((phase + .2) / 3.4, 0, 1)) ** 2;
      p.lens = still ? 0 : Math.sin(Math.PI * clamp((phase - 3.4) / 4.4, 0, 1)) ** 2;
      p.propX = -13 * (1 - p.lens);
      p.propY = still ? 0 : -3 * wave(t, 2.2);
      p.propRotate = still ? 0 : -18 + 12 * wave(t, 2.6);
      p.lookX += 3 * p.lens;
    } else if (state === "working") {
      const beat = still ? 0 : wave(t, .92);
      p.x = still ? 0 : 1.2 * wave(t, 1.84);
      p.y = -1.5 - 3 * beat;
      p.rotate = 7 + (still ? 0 : 3 * wave(t, 1.84));
      p.sx = 1 - .035 * beat; p.sy = 1 + .045 * beat;
      p.round = 17; p.lookX = still ? 3 : 4 * wave(t, 2.8); p.lookY = 2; p.eyeOpen = .84; p.eyeTilt = -5;
      p.gear = 1; p.gearRotate = still ? 0 : t * 80;
    } else if (state === "waiting") {
      const patience = still ? 0 : clamp((t - 12) / 35, 0, 1);
      const sway = still ? 0 : wave(t, 3.6);
      const fidget = still ? 0 : wave(t, 1.2) * (.5 + patience);
      p.x = 2.1 * sway;
      p.y = 1.5 + (still ? 0 : 2 * wave(t, 3.6)) + .6 * fidget;
      p.rotate = 2 + 6 * sway;
      p.sx = 1.015 + .023 * sway; p.sy = .98 - .028 * sway; p.round = 21;
      p.lookX = still ? -3 : -5 * wave(t, 3.6);
      p.lookY = 1 + .6 * fidget; p.eyeOpen = .88;
      p.green -= 12 * patience; p.blue -= 15 * patience;
      p.hourglass = 1;
      const flip = clamp((t % 5 - 4) / 1, 0, 1);
      p.waitRotate = still ? 0 : Math.floor(t / 5) * 180 + 180 * flip * flip * (3 - 2 * flip);
      p.grainY = still ? 0 : 3 * wave(t, 1.2);
    } else if (state === "blocked") {
      // A brief head shake followed by a quiet, concerned pose. No alarm loop.
      p.x = still ? 0 : 1.8 * Math.exp(-2.8 * t) * Math.sin(12 * t);
      p.rotate = -7 + (still ? 0 : 3 * Math.exp(-2.8 * t) * Math.sin(12 * t));
      p.y = 2; p.sx = .985; p.sy = .99; p.round = 22;
      p.lookX = -1; p.lookY = 1; p.eyeOpen = .9; p.leftOpen = .84;
      p.attention = 1;
    } else if (state === "done") {
      // One springy acknowledgement, then smiling eyes at rest. Never loop success.
      const bounce = still ? 0 : Math.exp(-3.4 * t) * Math.sin(8 * t);
      p.y = -12 * bounce; p.rotate = -3 + 9 * bounce;
      p.sx = 1 - .09 * bounce; p.sy = 1 + .11 * bounce;
      p.round = 21; p.eyeOpen = 1; p.happy = 1;
    }
    if (!still && !["done", "blocked", "sleeping"].includes(state)) {
      // Smooth periodic blink: no hard reset at a loop boundary.
      const phase = t % 12.6;
      const blink = Math.max(Math.exp(-(((phase - 3.1) / .085) ** 2)), Math.exp(-(((phase - 9.1) / .085) ** 2)), .85 * Math.exp(-(((phase - 9.38) / .07) ** 2)));
      p.eyeOpen *= 1 - .93 * blink;
    }
    return p;
  }

  const records = new Map();
  const keyed = new Map();
  const doc = globalThis.document;
  const clock = () => globalThis.performance.now();
  let frame = null;
  let previousFrame = 0;
  let installed = false;
  let intersection;
  let mutations;
  let reducedMotion;
  let pruneQueued = false;
  const svgNS = "http://www.w3.org/2000/svg";
  const rounded = (n) => Number(n.toFixed(4));

  function svg(tag, attributes = {}) {
    const node = doc.createElementNS(svgNS, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function draw(record, p) {
    record.sleepMark.setAttribute('opacity', record.state === 'sleeping' ? rounded(p.sleepOpacity) : '0');
    record.sleepMark.setAttribute('transform', `translate(${rounded(p.sleepX)} ${rounded(p.sleepY)})`);
    record.sparkles.setAttribute('opacity', rounded(p.sparkle));
    record.sparkles.setAttribute('transform', `translate(0 ${rounded(-4 * p.sparkle)})`);
    record.question.setAttribute('opacity', rounded(p.question));
    record.question.setAttribute('transform', `translate(0 ${rounded(-3 * p.question)}) rotate(${rounded(8 * p.question)} 84 18)`);
    record.lens.setAttribute('opacity', rounded(p.lens));
    record.lens.setAttribute('transform', `translate(${rounded(87 + p.propX)} ${rounded(43 + p.propY)}) rotate(${rounded(p.propRotate)})`);
    record.gear.setAttribute('opacity', rounded(p.gear));
    record.gear.setAttribute('transform', `translate(87 65) rotate(${rounded(p.gearRotate)})`);
    record.hourglass.setAttribute('opacity', rounded(p.hourglass));
    record.hourglass.setAttribute('transform', `translate(88 36) rotate(${rounded(p.waitRotate)})`);
    record.grain.setAttribute('cy', rounded(p.grainY));
    record.attention.setAttribute('opacity', rounded(p.attention));
    const r = p.round;
    // A rounded pixel, not an oval or a framed robot badge. Slight asymmetry keeps it soft.
    record.body.setAttribute("d", `M ${19 + r} 20 H ${81 - r} Q 81 20 81 ${20 + r} V ${80 - r} Q 81 80 ${81 - r} 80 H ${19 + r} Q 19 80 19 ${80 - r} V ${20 + r} Q 19 20 ${19 + r} 20 Z`);
    record.body.setAttribute("fill", `rgb(${Math.round(p.red)}, ${Math.round(p.green)}, ${Math.round(p.blue)})`);
    record.cheeks.forEach((cheek) => cheek.setAttribute("opacity", rounded(clamp(p.blush, 0, .65))));
    record.motion.setAttribute("transform", `translate(${rounded(50 + p.x)} ${rounded(51 + p.y)}) rotate(${rounded(p.rotate)}) scale(${rounded(p.sx)} ${rounded(p.sy)}) translate(-50 -50)`);
    record.face.setAttribute("transform", `translate(${rounded(50 + p.lookX)} ${rounded(49 + p.lookY)})`);
    record.eyes.forEach((eye, index) => {
      const x = index === 0 ? -10 : 10;
      const happy = p.happy;
      // Wider rounded eyes; shorter centerline preserves the overall height.
      const startY = -7.25 * (1 - happy) + happy;
      const endY = 7.25 * (1 - happy) + happy;
      const asleep = record.state === 'sleeping';
      eye.style.setProperty('--portal-eye-stroke', asleep ? '3.5' : '9');
      eye.setAttribute('stroke-width', asleep ? '3.5' : '9');
      eye.setAttribute("d", asleep ? `M ${x-5} 0 Q ${x} 4 ${x+5} 0` : `M ${x - happy * 4} ${rounded(startY)} Q ${x} ${rounded(-7 * happy)} ${x + happy * 4} ${rounded(endY)}`);
      eye.setAttribute("transform", `translate(${x} 0) rotate(${rounded((index ? -1 : 1) * p.eyeTilt)}) scale(1 ${asleep ? 1 : rounded(Math.max(.04, p.eyeOpen * (index ? p.rightOpen : p.leftOpen)))}) translate(${-x} 0)`);
    });
  }

  function target(record, now) {
    const expired = ["done", "blocked"].includes(record.state) && now - record.started >= 4000;
    const p = samplePose(record.state, (now - record.started) / 1000, {
      reduced: Boolean(reducedMotion?.matches) || record.static,
      settled: record.settled || expired,
    });
    if (record.brand) {
      // A stationary logo with open eyes, even while a completed chat is selected.
      Object.assign(p, { x: 0, y: 0, rotate: -3, sx: 1, sy: 1, round: 19, eyeOpen: 1, happy: 0 });
      p.lookX = reducedMotion?.matches ? 0 : record.gazeX;
      p.lookY = reducedMotion?.matches ? 0 : record.gazeY;
    }
    if (record.interactive && record.state === 'idle' && !reducedMotion?.matches && now - record.lastGaze < 2400) {
      p.lookX = record.gazeX; p.lookY = record.gazeY;
    }
    if (record.gesture && !reducedMotion?.matches && !record.static && !record.settled) {
      const g = sampleGesture(record.gesture, (now - record.gestureStarted) / 1000, record.playDirection);
      for (const key of ['x','y','rotate','lookX','lookY']) p[key] += g[key];
      for (const key of ['sx','sy','eyeOpen','leftOpen','rightOpen']) p[key] *= g[key];
      for (const key of ['happy','blush','sparkle']) p[key] = Math.max(p[key], g[key]);
    }
    if (record.hovered && (record.interactive || record.brand) && record.state === 'idle' && !record.static && !reducedMotion?.matches) {
      p.blush = Math.max(p.blush, .16); // Warm attention, not permanently smiling/closed eyes.
    }
    return p;
  }

  function animates(record, now) {
    if (record.static || reducedMotion?.matches || !record.visible || !record.element.isConnected) return false;
    if (now < record.interactionUntil) return true;
    if (record.brand || record.settled) return false;
    // Blocked and done have a finite acknowledgement; all other states breathe.
    return !["done", "blocked"].includes(record.state) || now - record.started < 4000;
  }

  function settleExpired(record, now = clock()) {
    if (!["done", "blocked"].includes(record.state) || now - record.started < 4000) return;
    record.pose = target(record, now);
    record.velocity = Object.fromEntries(Object.keys(record.pose).map((name) => [name, 0]));
    draw(record, record.pose);
  }

  function settleInteraction(record, now = clock()) {
    if (record.gesture && now >= record.gestureStarted + GESTURES[record.gesture] * 1000) {
      record.gesture = null;
      delete record.element.dataset.portalGesture;
    }
    if (!record.interactionUntil || now < record.interactionUntil) return;
    record.interactionUntil = 0;
    record.pose = target(record, now);
    record.velocity = Object.fromEntries(Object.keys(record.pose).map((name) => [name, 0]));
    draw(record, record.pose);
  }

  function requestFrame() {
    if (!doc || doc.hidden || frame !== null || reducedMotion?.matches) return;
    if ([...records.values()].some((record) => animates(record, clock()))) frame = globalThis.requestAnimationFrame(tick);
  }

  function tick(now) {
    frame = null;
    if (doc.hidden) return;
    const dt = clamp((now - previousFrame) / 1000 || 1 / 60, 1 / 240, 1 / 30);
    previousFrame = now;
    for (const record of records.values()) {
      if (!record.element.isConnected || !record.visible) continue;
      settleInteraction(record, now);
      if (record.static || (record.settled && now >= record.interactionUntil)) continue;
      if (!record.brand && now >= record.interactionUntil && ["done", "blocked"].includes(record.state) && now - record.started >= 4000) {
        settleExpired(record, now);
        continue;
      }
      const next = target(record, now);
      for (const name of Object.keys(next)) {
        record.velocity[name] = (record.velocity[name] + (next[name] - record.pose[name]) * 200 * dt) * Math.exp(-22 * dt);
        record.pose[name] += record.velocity[name] * dt;
      }
      draw(record, record.pose);
    }
    prune();
    requestFrame();
  }

  function destroy(element) {
    const record = records.get(element);
    if (!record) return;
    intersection?.unobserve(element);
    records.delete(element);
    element.removeEventListener("pointerenter", record.onEnter);
    element.removeEventListener("pointerleave", record.onLeave);
    if (record.key && keyed.get(record.key) === element) keyed.delete(record.key);
    if (!records.size && frame !== null) {
      globalThis.cancelAnimationFrame(frame);
      frame = null;
      previousFrame = 0;
    }
  }

  function prune() {
    for (const element of records.keys()) if (!element.isConnected) destroy(element);
  }

  function install() {
    if (installed || !doc) return;
    installed = true;
    reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
    reducedMotion?.addEventListener("change", () => {
      if (frame !== null) globalThis.cancelAnimationFrame(frame);
      frame = null;
      for (const record of records.values()) {
        record.pose = target(record, clock());
        record.velocity = Object.fromEntries(Object.keys(record.pose).map((key) => [key, 0]));
        draw(record, record.pose);
      }
      requestFrame();
    });
    if (globalThis.IntersectionObserver) {
      intersection = new globalThis.IntersectionObserver((entries) => {
        for (const entry of entries) {
          const record = records.get(entry.target);
          if (record) {
            record.visible = entry.isIntersecting;
            if (record.visible) { settleExpired(record); settleInteraction(record); }
          }
        }
        requestFrame();
      });
    }
    if (globalThis.MutationObserver) {
      mutations = new globalThis.MutationObserver(() => {
        if (pruneQueued) return;
        pruneQueued = true;
        // renderChat removes and reattaches keyed nodes synchronously every poll.
        globalThis.queueMicrotask(() => { pruneQueued = false; prune(); requestFrame(); });
      });
      mutations.observe(doc.documentElement, { childList: true, subtree: true });
    }
    doc.addEventListener("visibilitychange", () => {
      if (frame !== null) globalThis.cancelAnimationFrame(frame);
      frame = null;
      previousFrame = 0;
      if (!doc.hidden) for (const record of records.values()) if (record.visible) { settleExpired(record); settleInteraction(record); }
      requestFrame();
    });
    doc.addEventListener("pointermove", (event) => {
      if (event.pointerType === "touch" || doc.hidden || reducedMotion?.matches) return;
      for (const record of records.values()) {
        if ((!record.brand && !record.interactive) || record.static || record.settled || !record.visible || !record.element.isConnected || record.state !== 'idle') continue;
        const box = record.element.getBoundingClientRect();
        if (!box.width || !box.height) continue;
        const dx = event.clientX - box.left - box.width / 2;
        const dy = event.clientY - box.top - box.height / 2;
        const distance = Math.max(90, Math.hypot(dx, dy));
        record.gazeX = clamp(dx / distance * 6, -6, 6);
        record.gazeY = clamp(dy / distance * 5, -5, 5);
        record.lastGaze = clock();
        record.interactionUntil = Math.max(record.interactionUntil, clock() + 1200);
      }
      requestFrame();
    }, { passive: true });
    doc.addEventListener("pointerleave", () => {
      for (const record of records.values()) if (record.brand || record.interactive) {
        record.gazeX = 0; record.gazeY = 0; record.hovered = false;
        record.interactionUntil = Math.max(record.interactionUntil, clock() + 1200);
      }
      requestFrame();
    });
  }

  function setState(element, state = "idle", { settled = false } = {}) {
    const record = records.get(element);
    if (!record) return mount(element, { state, settled });
    const next = STATES.includes(state) ? state : "idle";
    if (record.state === next && record.settled === settled) return element;
    const previous = record.state;
    record.gesture = null;
    delete element.dataset.portalGesture;
    record.state = next;
    record.settled = settled;
    record.started = clock();
    record.interactionUntil = clock() + 1200;
    if (record.interactive && next === 'idle' && previous === 'sleeping') gesture(record, 'stretch');
    else if (record.interactive && next === 'idle' && ['thinking','working'].includes(previous)) gesture(record, 'nod');
    element.dataset.mascotState = next;
    element.title = `${element.dataset.pixelName || 'Portal'} · ${next}`;
    if (record.static || settled || reducedMotion?.matches) {
      record.pose = target(record, clock());
      draw(record, record.pose);
    }
    requestFrame();
    return element;
  }

  function mount(element, { state = "idle", key = "", static: isStatic = false, settled = false } = {}) {
    install();
    if (records.has(element)) return setState(element, state, { settled });
    const next = STATES.includes(state) ? state : "idle";
    const canvas = svg("svg", { viewBox: "0 0 100 100", "aria-hidden": "true", focusable: "false" });
    const motion = svg("g");
    const body = svg("path", { class: "pixel-mascot-body" });
    const face = svg("g");
    const cheeks = [-18, 18].map((x) => svg("ellipse", { cx: x, cy: 9, rx: 5, ry: 2.8, fill: "#dc8f83", opacity: 0 }));
    const eyes = [0,1].map(() => svg('path', {class:'pixel-mascot-eye', fill:'none',stroke:'#18191b','stroke-width':9,'stroke-linecap':'round','stroke-linejoin':'round'}));
    const sparkles = svg('g', {class:'portal-sparkles',opacity:0, fill:'none',stroke:'#d7e3e8','stroke-width':1.8,'stroke-linecap':'round'});
    [[13,24,3],[85,19,4],[85,65,2.5]].forEach(([x,y,r]) => sparkles.append(svg('path',{d:`M ${x} ${y-r} Q ${x} ${y} ${x+r} ${y} Q ${x} ${y} ${x} ${y+r} Q ${x} ${y} ${x-r} ${y} Q ${x} ${y} ${x} ${y-r} Z`})));
    const sleepMark = svg('text', {x:73, y:18, fill:'currentColor', 'font-size':16, opacity:0});
    sleepMark.textContent = 'zzz';
    const question = svg('text', {class:'portal-thinking-question',x:82,y:24,fill:'#d7e3e8','font-size':30,'font-weight':600,'font-family':'system-ui, sans-serif',opacity:0});
    question.textContent = '?';
    const lens = svg('g',{class:'portal-thinking-lens',opacity:0,stroke:'#c0d0dc','stroke-width':3.5,'stroke-linecap':'round',fill:'none'});
    lens.append(svg('path',{d:'M 6 6 L 15 15'}),svg('circle',{cx:0,cy:0,r:10,fill:'#151c22','fill-opacity':.8}),svg('path',{d:'M -5 -1 Q -5 -5 -1 -5','stroke-width':1.5,opacity:.65}));
    const gear = svg('g',{class:'portal-working-gear',opacity:0,stroke:'#c0d0dc','stroke-width':3,'stroke-linecap':'round',fill:'none'});
    gear.append(svg('circle',{r:8,fill:'#151c22','fill-opacity':.85}),svg('circle',{r:2.5,'stroke-width':2}));
    for (let tooth=0;tooth<8;tooth++) gear.append(svg('path',{d:'M 0 -8 L 0 -12',transform:`rotate(${tooth*45})`}));
    const hourglass = svg('g',{class:'portal-waiting-hourglass',opacity:0,stroke:'#dbcbad','stroke-width':2.5,'stroke-linecap':'round','stroke-linejoin':'round',fill:'none'});
    const grain=svg('circle',{r:1.2,cy:0,fill:'#dbcbad',stroke:'none'});
    hourglass.append(svg('path',{d:'M -8 -12 H 8 M -8 12 H 8 M -6 -12 V -7 L 4 5 V 12 M 6 -12 V -7 L -4 5 V 12',fill:'#1d1b18','fill-opacity':.65}),svg('path',{d:'M -4 -8 H 4 L 0 -3 Z M -3 9 H 3 L 0 5 Z',fill:'#dbcbad',stroke:'none'}),grain);
    const attention=svg('g',{class:'portal-attention-mark',opacity:0,transform:'translate(86 23)',stroke:'#d7bcae','stroke-width':2.3,'stroke-linecap':'round',fill:'none'});
    attention.append(svg('circle',{r:10,fill:'#28231f','fill-opacity':.9,'stroke-width':1.5}),svg('path',{d:'M 0 -5 V 1'}),svg('circle',{cy:5,r:1.1,fill:'#d7bcae',stroke:'none'}));
    face.append(...cheeks, ...eyes); motion.append(body, face); canvas.append(motion, sleepMark, sparkles, question, lens, gear, hourglass, attention);
    element.replaceChildren(canvas);
    element.classList.add("pixel-mascot");
    element.setAttribute("aria-hidden", "true");
    element.dataset.mascotState = next;
    element.title = `${element.dataset.pixelName || 'Portal'} · ${next}`;
    const record = { element, motion, body, face, eyes, cheeks, sleepMark, sparkles, question, lens, gear, hourglass, grain, attention, key, state: next, static: isStatic, settled,
      interactive: element.hasAttribute('data-pixel-interactive'), gesture:null, gestureStarted:0, playDirection:1, playCount:0, clickTimes:[], lastPlay:-Infinity, lastGaze:-Infinity,
      brand: element.hasAttribute("data-pixel-brand"), gazeX: 0, gazeY: 0,
      hovered: false, hoverStarted: 0, interactionUntil: 0, started: clock(), visible: !intersection };
    record.onEnter = (event) => {
      if (event.pointerType === "touch" || record.static || record.settled || (!record.interactive && !record.brand) || reducedMotion?.matches) return;
      record.hovered = true; record.hoverStarted = clock(); record.interactionUntil = Math.max(record.interactionUntil, clock() + 1800);
      if (record.interactive && record.state === 'idle' && !record.gesture) gesture(record, 'perk');
      requestFrame();
    };
    record.onLeave = () => {
      if (record.static || record.settled || (!record.interactive && !record.brand)) return;
      record.hovered = false; record.interactionUntil = Math.max(record.interactionUntil, clock() + 1200);
      requestFrame();
    };
    element.addEventListener("pointerenter", record.onEnter);
    element.addEventListener("pointerleave", record.onLeave);
    record.pose = target(record, clock());
    record.velocity = Object.fromEntries(Object.keys(record.pose).map((name) => [name, 0]));
    records.set(element, record);
    if (record.interactive && next === 'idle' && !settled) gesture(record, 'hello');
    if (key) keyed.set(key, element);
    draw(record, record.pose);
    intersection?.observe(element);
    requestFrame();
    return element;
  }

  function create({ className = "", key = "", ...options } = {}) {
    if (key && keyed.has(key)) {
      const existing = keyed.get(key);
      existing.className = `pixel-mascot ${className}`.trim();
      const record = records.get(existing);
      const isStatic = Boolean(options.static);
      if (record.static !== isStatic) {
        record.static = isStatic;
        record.pose = target(record, clock());
        record.velocity = Object.fromEntries(Object.keys(record.pose).map((name) => [name, 0]));
        draw(record, record.pose);
        requestFrame();
      }
      return setState(existing, options.state, { settled: Boolean(options.settled) });
    }
    const element = doc.createElement("span");
    element.className = className;
    return mount(element, { ...options, key });
  }

  const descriptions = {
    idle: "At ease. A small breath and a curious glance.",
    working: "A focused little rhythm, in soft blue, while verified work is running.",
    waiting: "A curious sway and a little fidget as the wait grows, in warm sand.",
    blocked: "A brief head shake and a soft coral tint. Something needs attention.",
    thinking: "An upward glance and a gentle sway while the agent thinks.",
    done: "One small bounce, smiling eyes, then back to rest.",
    sleeping: "A quiet nap after the configured idle time. Activity wakes Portal with a stretch.",
  };

  function init(root = doc) {
    if (!root) return;
    root.querySelectorAll("[data-pixel-mascot]").forEach((element) => {
      if (!records.has(element)) mount(element, { state: element.dataset.pixelMascot });
    });
    const demo = root.querySelector("#pixel-motion-demo");
    if (!demo || demo.dataset.initialized) return;
    demo.dataset.initialized = "true";
    const character = demo.querySelector("[data-pixel-motion-character]");
    const description = demo.querySelector("[data-pixel-motion-description]");
    mount(character);
    demo.querySelectorAll("[data-pixel-motion-state]").forEach((button) => {
      button.addEventListener("click", () => {
        const state = button.dataset.pixelMotionState;
        // Manual demo replay is isolated from the real task/brand state.
        if (records.get(character)?.state === state) records.get(character).started = clock();
        setState(character, state);
        requestFrame();
        demo.querySelectorAll("[data-pixel-motion-state]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
        description.textContent = descriptions[state];
      });
    });
  }

  function gesture(record, name) {
    if (reducedMotion?.matches || record.static || record.settled || !GESTURES[name]) return;
    record.gesture = name; record.gestureStarted = clock();
    record.element.dataset.portalGesture = name;
    record.interactionUntil = Math.max(record.interactionUntil, clock() + GESTURES[name] * 1000 + 500);
    requestFrame();
  }

  function play(element) {
    const record = records.get(element);
    if (!record || record.static || record.settled || !record.interactive || reducedMotion?.matches || clock()-record.lastPlay<100) return;
    record.lastPlay = clock();
    if (record.state === 'sleeping') {setState(element, 'idle');return;}
    if (record.state !== 'idle') {gesture(record,'nod');return;}
    record.playDirection *= -1;
    record.clickTimes = [...record.clickTimes.filter(time => clock()-time<900),clock()];
    if (record.clickTimes.length >= 3) {record.clickTimes=[];gesture(record,'starstruck');}
    else gesture(record, PLAY_SEQUENCE[record.playCount++ % PLAY_SEQUENCE.length]);
  }
  globalThis.PixelMascot = Object.freeze({ STATES, GESTURES, samplePose, sampleGesture, create, mount, setState, destroy, play, init });
  if (doc) init();
})();
