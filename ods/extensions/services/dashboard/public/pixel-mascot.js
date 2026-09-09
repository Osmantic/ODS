/* Pixel's own soft-square character. Local SVG, no image service or animation dependency. */
"use strict";

(() => {
  const STATES = Object.freeze(["idle", "working", "waiting", "blocked", "thinking", "done"]);
  const TAU = Math.PI * 2;
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const wave = (time, period) => Math.sin(time * TAU / period);
  const PALETTE = Object.freeze({
    idle: [229, 229, 228], thinking: [226, 220, 240], working: [205, 217, 238],
    waiting: [236, 219, 193], blocked: [237, 184, 174], done: [236, 229, 221],
  });

  // Pure, deterministic poses make motion inspectable without a running agent.
  // Time is elapsed within a state, never a source of invented execution state.
  function samplePose(state = "idle", seconds = 0, { reduced = false, settled = false } = {}) {
    if (!STATES.includes(state)) state = "idle";
    const t = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
    const still = reduced || settled;
    const [red, green, blue] = PALETTE[state];
    const p = { x: 0, y: 0, rotate: -3, sx: 1, sy: 1, round: 19, lookX: 0, lookY: 0, eyeOpen: 1, eyeTilt: 0, happy: 0, red, green, blue, blush: 0 };
    if (state === "idle") {
      p.y = still ? 0 : -1.2 * wave(t, 4.8);
      p.sx = 1 + (still ? 0 : .012 * wave(t, 4.8));
      p.sy = 1 - (still ? 0 : .014 * wave(t, 4.8));
      p.lookX = still ? 0 : 1.7 * wave(t, 9.6);
    } else if (state === "thinking") {
      p.rotate = -10 + (still ? 0 : 3 * wave(t, 4.4));
      p.y = -2 + (still ? 0 : 2.2 * wave(t, 4.4));
      p.x = still ? 0 : 1.6 * wave(t, 8.8);
      p.sx = .97 + (still ? 0 : .015 * wave(t, 4.4)); p.sy = 1.035 - (still ? 0 : .02 * wave(t, 4.4)); p.round = 22;
      p.lookX = 3 + (still ? 0 : 1.5 * wave(t, 4.4));
      p.lookY = -5; p.eyeOpen = .92;
    } else if (state === "working") {
      const beat = still ? 0 : wave(t, .92);
      p.x = still ? 0 : 1.2 * wave(t, 1.84);
      p.y = -1.5 - 3 * beat;
      p.rotate = 7 + (still ? 0 : 3 * wave(t, 1.84));
      p.sx = 1 - .035 * beat; p.sy = 1 + .045 * beat;
      p.round = 17; p.lookX = 4; p.lookY = 2; p.eyeOpen = .7; p.eyeTilt = -5;
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
    } else if (state === "blocked") {
      // A brief head shake followed by a quiet, concerned pose. No alarm loop.
      p.x = still ? 0 : 3.2 * Math.exp(-2.8 * t) * Math.sin(18 * t);
      p.rotate = -5 + (still ? 0 : 5 * Math.exp(-2.8 * t) * Math.sin(18 * t));
      p.y = 3; p.sx = 1.045; p.sy = .925; p.round = 17;
      p.lookY = -1; p.eyeOpen = .56; p.eyeTilt = 18;
    } else if (state === "done") {
      // One springy acknowledgement, then smiling eyes at rest. Never loop success.
      const bounce = still ? 0 : Math.exp(-3.4 * t) * Math.sin(8 * t);
      p.y = -12 * bounce; p.rotate = -3 + 9 * bounce;
      p.sx = 1 - .09 * bounce; p.sy = 1 + .11 * bounce;
      p.round = 21; p.eyeOpen = 1; p.happy = 1;
    }
    if (!still && state !== "done" && state !== "blocked") {
      // Smooth periodic blink: no hard reset at a loop boundary.
      const blink = Math.pow(Math.max(0, Math.cos((t - 3.1) * TAU / 5.8)), 90);
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
      const startY = -5.5 * (1 - happy) + happy;
      const endY = 5.5 * (1 - happy) + happy;
      eye.setAttribute("d", `M ${x - happy * 4} ${rounded(startY)} Q ${x} ${rounded(-7 * happy)} ${x + happy * 4} ${rounded(endY)}`);
      eye.setAttribute("transform", `translate(${x} 0) rotate(${rounded((index ? -1 : 1) * p.eyeTilt)}) scale(1 ${rounded(Math.max(.04, p.eyeOpen))}) translate(${-x} 0)`);
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
    if (record.hovered && !record.static && !reducedMotion?.matches) {
      const elapsed = (now - record.hoverStarted) / 1000;
      const delight = Math.exp(-3 * elapsed) * Math.sin(10 * elapsed);
      p.blush = .5;
      p.eyeOpen = record.brand ? 1.12 : 1;
      if (!record.brand) {
        p.y -= 7 * delight;
        p.rotate += 5 * delight;
        p.happy = .8;
      }
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
        queueMicrotask(() => { pruneQueued = false; prune(); requestFrame(); });
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
        if (!record.brand || !record.visible || !record.element.isConnected) continue;
        const box = record.element.getBoundingClientRect();
        if (!box.width || !box.height) continue;
        const dx = event.clientX - box.left - box.width / 2;
        const dy = event.clientY - box.top - box.height / 2;
        const distance = Math.max(90, Math.hypot(dx, dy));
        record.gazeX = clamp(dx / distance * 6, -6, 6);
        record.gazeY = clamp(dy / distance * 5, -5, 5);
        record.interactionUntil = clock() + 1200;
      }
      requestFrame();
    }, { passive: true });
    doc.addEventListener("pointerleave", () => {
      for (const record of records.values()) if (record.brand) {
        record.gazeX = 0; record.gazeY = 0; record.hovered = false;
        record.interactionUntil = clock() + 1200;
      }
      requestFrame();
    });
  }

  function setState(element, state = "idle", { settled = false } = {}) {
    const record = records.get(element);
    if (!record) return mount(element, { state, settled });
    const next = STATES.includes(state) ? state : "idle";
    if (record.state === next && record.settled === settled) return element;
    record.state = next;
    record.settled = settled;
    record.started = clock();
    record.interactionUntil = clock() + 1200;
    element.dataset.mascotState = next;
    element.title = `Pixel · ${next}`;
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
    const eyes = [svg("path", { class: "pixel-mascot-eye" }), svg("path", { class: "pixel-mascot-eye" })];
    face.append(...cheeks, ...eyes); motion.append(body, face); canvas.append(motion);
    element.replaceChildren(canvas);
    element.classList.add("pixel-mascot");
    element.setAttribute("aria-hidden", "true");
    element.dataset.mascotState = next;
    element.title = `Pixel · ${next}`;
    const record = { element, motion, body, face, eyes, cheeks, key, state: next, static: isStatic, settled,
      brand: element.hasAttribute("data-pixel-brand"), gazeX: 0, gazeY: 0,
      hovered: false, hoverStarted: 0, interactionUntil: 0, started: clock(), visible: !intersection };
    record.onEnter = (event) => {
      if (event.pointerType === "touch" || record.static || reducedMotion?.matches) return;
      record.hovered = true; record.hoverStarted = clock(); record.interactionUntil = clock() + 1800;
      requestFrame();
    };
    record.onLeave = () => {
      record.hovered = false; record.interactionUntil = clock() + 1200;
      requestFrame();
    };
    element.addEventListener("pointerenter", record.onEnter);
    element.addEventListener("pointerleave", record.onLeave);
    record.pose = target(record, clock());
    record.velocity = Object.fromEntries(Object.keys(record.pose).map((name) => [name, 0]));
    records.set(element, record);
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

  globalThis.PixelMascot = Object.freeze({ STATES, samplePose, create, mount, setState, destroy, init });
  if (doc) init();
})();
