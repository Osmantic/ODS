"use strict";

const $ = (selector) => document.querySelector(selector);
const form = $("#onboarding-form");
const toast = $("#toast");
const reviewToken = (() => {
  const match = /^#review=([A-Za-z0-9_-]{43})$/.exec(window.location.hash);
  if (!match) return "";
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  return match[1];
})();
let accessSessionVerified = false;
let onboardingRevision = "";
let operatorActionAvailability = {};
let reviewViewAvailability = {};
let latestFrontierStatus = null;
let workAuthoring = null;
let workAuthoringRevision = null;
let workDraftReviews = null;
let milestoneSequence = 0;
let workHandoffTaskHandle = null;
let chatProjection = null;
let deepWorkProjection = null;
let deepWorkSemanticProjection = null;
let approvalProjection = null;
let permissionProjection = null;
let workspaceInspectorReturnFocus = null;
let activeConversationHandle = null;
let chatDraftingNew = false;
let chatRunning = false;
let chatPollTimer = null;
let chatPollFailures = 0;
const profileLimbs = {
  minimal: { email: false, calendar: false, social: false, web: false, operations: false, frontier: false },
  "chief-of-staff": { email: true, calendar: true, social: false, web: true, operations: false, frontier: false },
  research: { email: false, calendar: false, social: false, web: true, operations: false, frontier: false },
  "engineering-operator": { email: true, calendar: true, social: false, web: true, operations: true, frontier: false },
};

async function api(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new Error(response.redirected ? "Additional authentication is required before Pixel can continue." : "The Pixel control response was not authenticated JSON.");
  }
  const value = await response.json().catch(() => ({ message: "The local control response was unreadable." }));
  if (!response.ok) throw new Error(value.message || "The local control request was rejected.");
  return value;
}

function privateWorkspaceAuthorized() {
  return Boolean(reviewToken || accessSessionVerified);
}

function privateHeaders() {
  return reviewToken ? { "X-Pixel-Review-Token": reviewToken } : {};
}

function approvalRoute(remotePath, localPath) {
  return reviewToken ? localPath : remotePath;
}

function ensureApprovalSession() {
  if (reviewToken) return Promise.resolve();
  if (!accessSessionVerified) return Promise.reject(new Error("Pixel's authenticated workspace session is not verified."));
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  const challenge = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  const channel = new BroadcastChannel(`pixel-approval-${challenge}`);
  const popup = window.open("", `pixel-approval-${challenge}`, "popup=yes,width=520,height=620");
  if (!popup) {
    channel.close();
    return Promise.reject(new Error("Pixel could not open the approval verification window. Allow this site to open the approval popup, then try again."));
  }
  popup.opener = null;
  popup.location.replace(`/approve/session?challenge=${challenge}`);
  return new Promise((resolve, reject) => {
    let settled = false;
    let closedTimer;
    let expiryTimer;
    const cleanup = () => {
      channel.close();
      window.clearInterval(closedTimer);
      window.clearTimeout(expiryTimer);
    };
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback(value);
    };
    channel.onmessage = (event) => {
      if (event.data?.schemaVersion !== 1 || event.data?.type !== "pixel-approval-ready" || event.data?.challenge !== challenge) return;
      finish(resolve);
    };
    closedTimer = window.setInterval(() => {
      if (popup.closed) finish(reject, new Error("Approval verification was closed. The exact proposal is still pending and nothing ran."));
    }, 400);
    expiryTimer = window.setTimeout(() => {
      try { popup.close(); } catch {}
      finish(reject, new Error("Approval verification timed out. The exact proposal is still pending and nothing ran."));
    }, 120000);
  });
}

function notify(message, tone = "success") {
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.hidden = false;
  window.clearTimeout(notify.timer);
  notify.timer = window.setTimeout(() => { toast.hidden = true; }, 5000);
}

function chatRequestId() {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return `chatreq-${[...bytes].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function showInspector(show = true) {
  const inspector = $("#workspace-inspector");
  inspector.hidden = !show;
  $("#agent-workspace").classList.toggle("inspector-hidden", !show);
  $("#toggle-inspector").setAttribute("aria-expanded", show ? "true" : "false");
}

function restoreDeepWorkAuthoring() {
  const panel = $("#deep-work-authoring-panel");
  const home = $("#deep-work-authoring-home");
  if (panel && home && panel.parentElement !== home) home.append(panel);
}

function resetDeepWorkDraftForm() {
  const form = $("#deep-work-draft-form");
  form.reset();
  $("#deep-work-milestone-list").replaceChildren();
  milestoneSequence = 0;
  if (workAuthoring?.state === "ready") addMilestone();
  setText("#deep-work-draft-message", "");
}

function closeInlineDeepWorkPlanner({ clearLink = false, resetDraft = false } = {}) {
  restoreDeepWorkAuthoring();
  $("#chat-inline-workbench").hidden = true;
  if (clearLink) {
    workHandoffTaskHandle = null;
    $("#deep-work-handoff-note").hidden = true;
  }
  if (resetDraft) resetDeepWorkDraftForm();
}

function openInlineDeepWorkPlanner(taskHandle, source) {
  if (workHandoffTaskHandle && workHandoffTaskHandle !== taskHandle) {
    throw new Error("A different chat task is already linked to this draft. Clear that link before switching tasks.");
  }
  workHandoffTaskHandle = taskHandle;
  $("#deep-work-handoff-note").hidden = false;
  const form = $("#deep-work-draft-form");
  form.elements.goalObjective.value = source.userText.slice(0, 4000);
  const firstMilestone = $("#deep-work-milestone-list .milestone-objective");
  if (firstMilestone && !firstMilestone.value.trim()) firstMilestone.value = source.userText.slice(0, 4000);
  setText("#deep-work-draft-message", "Review the milestone boundaries and independent completion checks. The fixed permission path can create only an inert exact draft; it will not start work.");
  $("#chat-inline-workbench-content").append($("#deep-work-authoring-panel"));
  const workbench = $("#chat-inline-workbench");
  workbench.hidden = false;
  form.elements.goalObjective.focus({ preventScroll: true });
  workbench.scrollIntoView({ block: "end", behavior: "smooth" });
}

function showControlCenter(show) {
  if (show) closeInlineDeepWorkPlanner();
  $("#agent-workspace").hidden = show;
  $("#control-center").hidden = !show;
  if (show) $("#control-main").focus({ preventScroll: true });
  else $("#chat-message").focus({ preventScroll: true });
}

function setWorkspaceRunState(label, tone = "neutral") {
  const state = $("#workspace-run-state");
  state.className = `run-chip ${tone}`;
  const dot = document.createElement("span");
  dot.setAttribute("aria-hidden", "true");
  state.replaceChildren(dot, document.createTextNode(` ${label}`));
  const inspector = $("#inspector-run-state");
  inspector.className = `service-state ${tone === "running" ? "good" : tone === "attention" ? "attention" : "neutral"}`;
  inspector.textContent = label;
}

function emptyChatElement() {
  const container = document.createElement("div");
  container.className = "workspace-welcome";
  container.id = "chat-empty-state";
  const mark = document.createElement("span");
  mark.className = "welcome-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = "P";
  const title = document.createElement("h2");
  title.textContent = "Start with the outcome you want.";
  const copy = document.createElement("p");
  copy.textContent = "Pixel will use its configured local agent, keep durable task state visible, and ask inline before a consequential boundary. Settings and technical evidence stay available without becoming a second workflow.";
  const starters = document.createElement("div");
  starters.className = "starter-grid";
  [
    ["Check in", "What needs attention right now?", "Review what needs my attention and distinguish current evidence from remembered context."],
    ["Plan deep work", "Turn a large outcome into durable milestones.", "Build a durable plan for this goal, then show the milestones and exact completion criteria before work begins."],
    ["Research", "Use current sources and visible provenance.", "Research this question using current sources, inline citations, and explicit evidence-versus-inference labels."],
  ].forEach(([heading, detail, prompt]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.starter = prompt;
    const strong = document.createElement("strong");
    strong.textContent = heading;
    const span = document.createElement("span");
    span.textContent = detail;
    button.append(strong, span);
    starters.append(button);
  });
  container.append(mark, title, copy, starters);
  return container;
}

function chatMessage(role, text, meta = "") {
  const article = document.createElement("article");
  article.className = `chat-message ${role}`;
  if (role === "assistant") {
    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "P";
    article.append(avatar);
  }
  const wrapper = document.createElement("div");
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  wrapper.append(body);
  if (meta) {
    const detail = document.createElement("div");
    detail.className = "message-meta";
    detail.textContent = meta;
    wrapper.append(detail);
  }
  article.append(wrapper);
  return article;
}

function chatActivityLabel(code) {
  return {
    accepted: "Accepted into durable private custody",
    "agent-started": "Configured local agent started",
    "response-verified": "Agent response verified and retained",
    "execution-failed": "Agent execution failed without an answer claim",
    "recovery-required": "Interrupted task retained for recovery review",
  }[code] || "Unrecognized task event";
}

function chatCapabilityLabel(capability) {
  if (!capability) return "";
  if (capability.state === "complete-local" && capability.autonomousWithinPolicy) return "ran automatically within local policy";
  if (capability.state === "broker-correlated" && capability.externalEffectOccurred && capability.autonomousWithinPolicy) return "bounded external action completed within policy";
  if (capability.state === "broker-correlated" && capability.externalEffectOccurred === null) return "broker accepted the request; outcome is still pending";
  if (capability.state === "broker-correlated" && capability.brokerReceiptSatisfied) return "broker outcome verified";
  if (capability.state === "broker-approval-required") return "exact broker approval is required";
  if (capability.state === "broker-ambiguous") return "broker outcome is ambiguous—review before retry";
  if (capability.state === "broker-correlation-required") return "consequential broker evidence required";
  if (capability.state === "unclassified") return "capability evidence incomplete";
  if (capability.state === "legacy-unavailable") return "legacy capability evidence unavailable";
  return "capability state unavailable";
}

function chatRunEvent(turn) {
  const event = document.createElement("details");
  event.className = `run-event${["failed", "interrupted"].includes(turn.state) ? " failed" : turn.state === "succeeded" ? " settled" : ""}`;
  event.open = turn.state === "running" || ["failed", "interrupted"].includes(turn.state);
  const summary = document.createElement("summary");
  const pulse = document.createElement("span");
  pulse.className = "event-pulse";
  pulse.setAttribute("aria-hidden", "true");
  const text = document.createElement("span");
  text.textContent = {
    submitting: "Submitting task to durable private custody…",
    queued: "Task accepted",
    executing: "Pixel is working through the configured local agent…",
    completed: "Task completed with verified response custody",
    failed: "Task failed without claiming an answer",
    interrupted: "Task interrupted; recovery review required",
  }[turn.phase] || "Task state unavailable";
  const identity = document.createElement("code");
  identity.textContent = typeof turn.taskHandle === "string" ? turn.taskHandle.slice(-8) : "pending";
  summary.append(pulse, text, identity);
  const activity = document.createElement("ol");
  activity.className = "task-activity";
  (turn.activity || []).forEach((item) => {
    const row = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = chatActivityLabel(item.code);
    const at = document.createElement("time");
    at.dateTime = item.at;
    at.textContent = new Date(item.at).toLocaleTimeString();
    row.append(label, at);
    activity.append(row);
  });
  event.append(summary, activity);
  return event;
}

function chatHandoffControl(turn) {
  const article = document.createElement("article");
  if (turn.handoff) {
    article.className = "chat-handoff-card sealed";
    const heading = document.createElement("div");
    heading.className = "chat-handoff-heading";
    const title = document.createElement("strong");
    title.textContent = "Deep Work handoff sealed";
    const state = document.createElement("span");
    state.textContent = turn.handoff.targetState === "retained" ? "Draft retained" :
      turn.handoff.targetState === "configuration-changed" ? "Configuration changed" : "Review unavailable";
    heading.append(title, state);
    const summary = document.createElement("p");
    summary.textContent = `${formatCount(turn.handoff.milestoneCount)} exact milestone${turn.handoff.milestoneCount === 1 ? "" : "s"} · ${turn.handoff.profiles.map(deepWorkLabel).join(", ")} · ${deepWorkLabel(turn.handoff.dataClassification)}.`;
    const boundary = document.createElement("p");
    boundary.className = "work-card-boundary";
    boundary.textContent = "The immutable receipt binds this settled task record to the exact inert goal declaration. It did not start, schedule, activate, or complete work.";
    article.append(heading, summary, boundary);
    if (turn.handoff.targetState === "retained" && turn.handoff.draftHandle) {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "button quiet compact-button";
      open.dataset.openHandoffDraft = turn.handoff.draftHandle;
      open.textContent = "Review exact goal";
      article.append(open);
    }
    return article;
  }
  if (turn.state === "running" || workAuthoring?.state !== "ready") return null;
  article.className = "chat-handoff-card";
  const copy = document.createElement("span");
  copy.textContent = "Needs sustained, independently verified work?";
  const start = document.createElement("button");
  start.type = "button";
  start.className = "button quiet compact-button";
  start.dataset.planDeepWork = turn.taskHandle;
  start.textContent = "Plan as Deep Work";
  article.append(copy, start);
  return article;
}

function approvalCard(preview) {
  const article = document.createElement("article");
  article.className = "chat-approval-card";
  article.dataset.approvalId = preview.actionId;
  const heading = document.createElement("div");
  heading.className = "approval-card-heading";
  const title = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "approval-eyebrow";
  eyebrow.textContent = "Approval required";
  const name = document.createElement("h3");
  name.textContent = preview.label;
  title.append(eyebrow, name);
  const state = document.createElement("span");
  state.className = "approval-state";
  state.textContent = "Waiting";
  heading.append(title, state);
  const effect = document.createElement("p");
  effect.className = "approval-effect";
  effect.textContent = preview.effect;
  const receipt = document.createElement("dl");
  receipt.className = "approval-receipt";
  [["Action", actionLabel(preview.kind)], ["Expires", new Date(preview.expiresAt).toLocaleTimeString()], ["Exact hash", preview.actionHash]].forEach(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    if (label === "Exact hash") {
      const code = document.createElement("code");
      code.textContent = value;
      detail.append(code);
    } else detail.textContent = value;
    row.append(term, detail);
    receipt.append(row);
  });
  const note = document.createElement("p");
  note.className = "approval-boundary";
  note.textContent = "Approve once runs only this exact hash. Deny removes the pending proposal. Remote approval requires the separate MFA-protected approval session.";
  const message = document.createElement("p");
  message.className = "approval-message";
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  const actions = document.createElement("div");
  actions.className = "approval-card-actions";
  const deny = document.createElement("button");
  deny.type = "button";
  deny.className = "button quiet";
  deny.dataset.denyApproval = preview.actionId;
  deny.textContent = "Deny";
  const approve = document.createElement("button");
  approve.type = "button";
  approve.className = "button primary";
  approve.dataset.approveApproval = preview.actionId;
  approve.textContent = "Approve once";
  actions.append(deny, approve);
  article.append(heading, effect, receipt, note, actions, message);
  return article;
}

function appendApprovalCards(thread) {
  const approvals = approvalProjection?.state === "ready" ? approvalProjection.approvals : [];
  approvals.forEach((preview) => thread.append(approvalCard(preview)));
}

function chatWorkStateLabel(state) {
  return {
    ready: "Ready", running: "Working", "waiting-authority": "Review needed", paused: "Paused",
    completed: "Completed", failed: "Failed", cancelled: "Cancelled",
    "recovery-inconclusive": "Recovery needed", "budget-exhausted": "Budget reached",
    "no-progress": "No progress",
  }[state] || "Unavailable";
}

function fixedActionPermission(kind) {
  return permissionProjection?.state === "ready" && Array.isArray(permissionProjection.settings)
    ? permissionProjection.settings.find((setting) => setting.kind === kind) || null
    : null;
}

function appendChatLifecycleControls(article, goal, stale) {
  const configurations = {
    "deep-work-pause": {
      policy: "deepWorkPause", label: "Pause future work", className: "button quiet warning-action",
      description: "A bounded step already running may finish; no later milestone will be scheduled.",
    },
    "deep-work-resume": {
      policy: "deepWorkResume", label: "Resume reviewed goal", className: "button quiet",
      description: "Starts nothing immediately and preserves the exact paused scope and custody.",
    },
    "deep-work-cancel": {
      policy: "deepWorkCancel", label: "Cancel when safely settled", className: "button quiet danger-action",
      description: "Requires provable pre-launch revocation or terminal cleanup; it cannot hide a live worker.",
    },
  };
  const relevant = goal.state === "paused"
    ? ["deep-work-resume", "deep-work-cancel"]
    : ["ready", "running", "waiting-authority"].includes(goal.state)
      ? ["deep-work-pause", "deep-work-cancel"] : [];
  if (!relevant.length) return;
  const controls = document.createElement("section");
  controls.className = "work-card-controls";
  controls.setAttribute("aria-label", "Deep Work lifecycle controls");
  const heading = document.createElement("div");
  heading.className = "work-card-control-heading";
  const title = document.createElement("strong");
  title.textContent = "Goal controls";
  const note = document.createElement("span");
  note.textContent = "Exact current-state review";
  heading.append(title, note);
  const actions = document.createElement("div");
  actions.className = "work-card-control-actions";
  relevant.forEach((kind) => {
    const config = configurations[kind];
    const setting = fixedActionPermission(kind);
    const available = !stale && operatorActionAvailability[config.policy] === true;
    const permitted = setting?.privatePolicyEnabled === true && setting.mode !== "never-allow";
    const button = document.createElement("button");
    button.type = "button";
    button.className = config.className;
    button.dataset.chatLifecycleAction = kind;
    button.disabled = !available || !permitted;
    const label = document.createElement("strong");
    label.textContent = config.label;
    const detail = document.createElement("small");
    const permission = !setting ? "Permission state unavailable"
      : !setting.privatePolicyEnabled ? "Locked off by private deployment policy"
        : setting.mode === "never-allow" ? "Set to Never allow"
          : permissionModeLabel(setting.mode);
    detail.textContent = `${config.description} ${permission}.`;
    button.append(label, detail);
    actions.append(button);
  });
  const boundary = document.createElement("p");
  boundary.className = "work-card-control-boundary";
  boundary.textContent = stale
    ? "Controls are unavailable until Pixel has a fresh, coherent controller observation."
    : "Any required confirmation appears here in the conversation. The server rechecks exact goal state before acting.";
  controls.append(heading, actions, boundary);
  article.append(controls);
}

function appendChatRecoveryGuidance(article, goal) {
  const guidance = {
    "waiting-authority": "The controller is waiting at a declared boundary. Review the boundary and any exact proposal shown inline; no progress should be inferred while authority is absent.",
    paused: "Future scheduling is durably paused. Resume requires a fresh exact review and cannot broaden the goal.",
    "recovery-inconclusive": "Recovery evidence is inconclusive. Preserve custody, do not retry or resume, and inspect the private recovery evidence on the trusted host.",
    failed: "The goal failed. Retained evidence remains reviewable, but failure is not completion and Pixel will not silently retry it.",
    "budget-exhausted": "A declared budget was exhausted. Pixel will not expand it or continue automatically.",
    "no-progress": "The no-progress threshold was reached. Pixel stopped looping; revise the plan only through a separately reviewed action.",
  }[goal.state];
  if (!guidance) return;
  const note = document.createElement("p");
  note.className = "work-card-recovery-guidance";
  note.textContent = guidance;
  article.append(note);
}

function chatDeepWorkCard() {
  const value = deepWorkProjection;
  if (!value?.goal) return null;
  const goal = value.goal;
  const stale = value.state === "offline";
  const degraded = value.state === "degraded";
  const current = (value.sessions || []).find((session) => session.current) || value.sessions?.[0] || null;
  const article = document.createElement("article");
  article.className = `chat-work-card${stale || degraded || ["failed", "recovery-inconclusive", "budget-exhausted", "no-progress"].includes(goal.state) ? " attention" : ""}`;
  const heading = document.createElement("div");
  heading.className = "chat-work-heading";
  const title = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "work-card-eyebrow";
  eyebrow.textContent = stale ? "Last retained goal snapshot" : "Current durable goal";
  const name = document.createElement("h3");
  name.textContent = "Deep Work controller";
  title.append(eyebrow, name);
  const state = document.createElement("span");
  state.className = "work-card-state";
  state.textContent = stale ? "Status stale" : degraded ? "Service concern" : chatWorkStateLabel(goal.state);
  heading.append(title, state);

  const progress = document.createElement("p");
  progress.className = "work-card-summary";
  progress.textContent = `${stale ? "Last verified snapshot: " : ""}${formatCount(goal.progress.milestonesCompleted)} of ${formatCount(goal.progress.milestonesTotal)} milestones independently verified · ${formatCount(value.summary.artifactCount)} retained artifact${value.summary.artifactCount === 1 ? "" : "s"}.`;
  const facts = document.createElement("dl");
  facts.className = "work-card-facts";
  [
    ["Continuity", goal.continuity.restartSafe ? `Checkpoint ${formatCount(goal.continuity.checkpointSequence)} · restart safe` : "Not verified"],
    ["Controller", deepWorkLabel(value.state)],
    ["Observed", `${new Date(value.generatedAt).toLocaleString()}${stale ? " · refresh required" : ""}`],
    ["Next event", deepWorkLabel(goal.nextAction)],
    ["Current work", current ? `${deepWorkLabel(current.mode)} · ${deepWorkLabel(current.state)}` : "No active milestone"],
    ["Verification", current ? deepWorkLabel(current.verification) : "Goal-level evidence retained"],
  ].forEach(([label, detail]) => reviewField(facts, label, detail));
  article.append(heading, progress, facts);

  if (current?.artifacts?.kinds?.length) {
    const kinds = document.createElement("p");
    kinds.className = "work-card-artifacts";
    kinds.textContent = current.artifacts.kinds.map((item) => `${deepWorkLabel(item.kind)} (${formatCount(item.count)})`).join(" · ");
    article.append(kinds);
  }

  if (deepWorkSemanticProjection) {
    const review = deepWorkSemanticProjection;
    const provenance = document.createElement("details");
    provenance.className = "work-card-provenance";
    const summary = document.createElement("summary");
    summary.textContent = `${review.content.artifacts.length} pathless artifact item${review.content.artifacts.length === 1 ? "" : "s"} · ${review.verification.checkedItems} evidence check${review.verification.checkedItems === 1 ? "" : "s"}`;
    const exact = document.createElement("p");
    exact.textContent = `${review.content.title} · ${deepWorkLabel(review.verification.method)} · review ${review.reviewSha256.slice(0, 12)}…`;
    const inventory = document.createElement("ul");
    inventory.className = "work-card-inventory";
    review.content.artifacts.forEach((artifact, index) => {
      const item = document.createElement("li");
      item.textContent = `Artifact ${index + 1} · ${deepWorkLabel(artifact.kind)} · ${artifact.format.toUpperCase()} · ${formatWorkBytes(artifact.bytes)} · ${artifact.purpose}`;
      inventory.append(item);
    });
    if (!review.content.artifacts.length) {
      const item = document.createElement("li");
      item.textContent = `${review.content.findings.length} retained finding${review.content.findings.length === 1 ? "" : "s"}; this work type produced evidence rather than a derived file.`;
      inventory.append(item);
    }
    const inspect = document.createElement("button");
    inspect.type = "button";
    inspect.className = "button quiet compact-button";
    inspect.textContent = "Inspect full evidence";
    inspect.addEventListener("click", openWorkspaceEvidenceInspector);
    provenance.append(summary, exact, inventory, inspect);
    article.append(provenance);
  } else if (!stale && reviewViewAvailability.deepWorkSemanticReviews === true && (goal.state === "waiting-authority" || value.summary.artifactCount > 0)) {
    const load = document.createElement("button");
    load.type = "button";
    load.className = "button quiet compact-button work-card-load";
    load.textContent = "Load exact evidence and artifacts";
    load.addEventListener("click", async () => {
      load.disabled = true;
      load.textContent = "Rechecking retained evidence…";
      try {
        await loadDeepWorkSemanticReview();
        openWorkspaceEvidenceInspector();
        notify("Exact local evidence loaded into this conversation.");
      } catch (error) {
        load.disabled = false;
        load.textContent = "Retry exact evidence load";
        notify(error.message, "error");
      }
    });
    article.append(load);
  }

  appendChatRecoveryGuidance(article, goal);
  appendChatLifecycleControls(article, goal, stale);
  const boundary = document.createElement("p");
  boundary.className = "work-card-boundary";
  boundary.textContent = "This is the appliance's current controller goal. Pixel does not claim that the selected chat created it unless an explicit handoff receipt is shown. Loading evidence is read-only and grants no completion or external-effect authority.";
  article.append(boundary);
  return article;
}

function renderConversationList(conversations, selected) {
  const list = $("#chat-conversation-list");
  list.replaceChildren();
  if (!conversations.length) {
    const empty = document.createElement("span");
    empty.className = "rail-empty";
    empty.textContent = "No conversations yet";
    list.append(empty);
    return;
  }
  conversations.forEach((conversation) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation-link${conversation.handle === selected ? " active" : ""}`;
    button.dataset.conversationHandle = conversation.handle;
    const title = document.createElement("strong");
    title.textContent = conversation.title;
    const detail = document.createElement("small");
    detail.textContent = `${conversation.turns.length} turn${conversation.turns.length === 1 ? "" : "s"} · ${new Date(conversation.updatedAt).toLocaleString()}`;
    button.append(title, detail);
    list.append(button);
  });
}

function renderChat(value) {
  chatProjection = value;
  const conversations = Array.isArray(value?.conversations) ? value.conversations : [];
  if (value?.activeHandle) {
    activeConversationHandle = value.activeHandle;
    chatDraftingNew = false;
  }
  let active = conversations.find((item) => item.handle === activeConversationHandle);
  if (!active && conversations.length && !chatDraftingNew) active = conversations[0];
  if (active) activeConversationHandle = active.handle;
  renderConversationList(conversations, activeConversationHandle);
  const thread = $("#chat-thread");
  thread.replaceChildren();
  if (!active) {
    activeConversationHandle = null;
    $("#workspace-title").textContent = "How can Pixel help?";
    thread.append(emptyChatElement());
  } else {
    $("#workspace-title").textContent = active.title;
    active.turns.forEach((turn) => {
      thread.append(chatMessage("user", turn.userText));
      thread.append(chatRunEvent(turn));
      if (turn.state === "succeeded") {
        const capabilityMeta = chatCapabilityLabel(turn.capability);
        const toolMeta = Number.isInteger(turn.toolCalls) ? `${turn.toolCalls} bounded tool call${turn.toolCalls === 1 ? "" : "s"}${turn.toolFailures ? ` · ${turn.toolFailures} failed` : ""}` : "";
        thread.append(chatMessage("assistant", turn.assistantText, [toolMeta, capabilityMeta].filter(Boolean).join(" · ")));
      }
      const handoff = chatHandoffControl(turn);
      if (handoff) thread.append(handoff);
    });
  }
  const workCard = chatDeepWorkCard();
  if (workCard) thread.append(workCard);
  appendApprovalCards(thread);
  thread.scrollTop = thread.scrollHeight;
  const ready = value?.state === "ready" && privateWorkspaceAuthorized();
  const runningTurn = conversations.flatMap((conversation) => conversation.turns || []).find((turn) => turn.state === "running");
  chatRunning = Boolean(runningTurn);
  $("#send-message").disabled = !ready || chatRunning;
  $("#chat-message").disabled = !ready;
  setText("#chat-message-status", !privateWorkspaceAuthorized() ? "Open the exact private URL printed by ./pixel ui or sign in through the authenticated portal to use owner-private chat." :
    runningTurn ? `Task ${runningTurn.taskHandle.slice(-8)} is running. Progress is durable; this page can reconnect while the local agent continues.` :
    value?.state === "ready" ? "Connected to the configured Pixel agent. Consequential boundaries still require their exact policy path." :
    value?.state === "unavailable" ? "Conversation custody could not be verified safely. No partial history is shown." :
    "Pixel chat is not connected to an exact configured local agent yet.");
  if (chatRunning) {
    setWorkspaceRunState("Working", "running");
    scheduleChatPoll();
  } else {
    if (chatPollTimer !== null) window.clearTimeout(chatPollTimer);
    chatPollTimer = null;
    chatPollFailures = 0;
    const last = active?.turns.at(-1);
    setWorkspaceRunState(last && ["failed", "interrupted"].includes(last.state) ? "Needs review" : "Ready", last && ["failed", "interrupted"].includes(last.state) ? "attention" : "neutral");
  }
}

function scheduleChatPoll() {
  if (chatPollTimer !== null || !privateWorkspaceAuthorized()) return;
  chatPollTimer = window.setTimeout(async () => {
    chatPollTimer = null;
    try {
      const value = await api("/api/v1/chat", { headers: privateHeaders() });
      chatPollFailures = 0;
      renderChat(value);
    } catch {
      chatPollFailures += 1;
      if (chatRunning && chatPollFailures < 10) {
        setText("#chat-message-status", "Pixel is still working, but this page temporarily lost its progress connection. Reconnecting…");
        scheduleChatPoll();
      } else if (chatRunning) {
        setWorkspaceRunState("Connection lost", "attention");
        setText("#chat-message-status", "Progress polling stopped after repeated connection failures. Reload to reconnect to durable task custody; the local task was not cancelled.");
      }
    }
  }, chatPollFailures ? 2000 : 750);
}

function renderWorkspaceSummary(value, deepWork, approvalInbox) {
  updateApprovalSummary(approvalInbox);
  const active = deepWork?.summary?.activeSessions || 0;
  const attention = deepWork?.summary?.attentionSessions || 0;
  setText("#inspector-deep-work", active ? `${active} active · ${attention} attention` : attention ? `${attention} need attention` : deepWork?.state === "ready" ? "Ready" : "Not active");
  setText("#inspector-artifacts", `${deepWork?.summary?.artifactCount || 0} retained`);
  const frontier = value?.frontier;
  setText("#workspace-privacy-route", frontier?.state === "active" ? "Local-first · bounded remote review available" : "Local-first · no active remote route");
}

function updateApprovalSummary(approvalInbox) {
  const approvalCount = approvalInbox?.state === "ready" && Array.isArray(approvalInbox.approvals) ? approvalInbox.approvals.length : 0;
  setText("#workspace-approval-count", String(approvalCount));
  setText("#rail-approval-count", String(approvalCount));
  setText("#workspace-approval-summary", approvalCount ? `${approvalCount} exact proposal${approvalCount === 1 ? " is" : "s are"} waiting inline in this conversation workspace.` : "No exact action is waiting for approval.");
}

function renderApprovals(value) {
  approvalProjection = value;
  if (chatProjection) renderChat(chatProjection);
}

function permissionModeLabel(mode) {
  return {
    "always-ask": "Always ask",
    "auto-within-policy": "Auto within policy",
    "never-allow": "Never allow",
  }[mode] || "Unavailable";
}

function populatePermissionSettings(list, settings, dataAttribute) {
  list.replaceChildren();
  settings.forEach((setting) => {
    const row = document.createElement("label");
    row.className = "permission-setting-row";
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = setting.label;
    const detail = document.createElement("small");
    detail.textContent = setting.privatePolicyEnabled ?
      (setting.autoEligible ? "Fixed local action · bounded auto is available" : "Consequential lifecycle action · fresh approval required") :
      "Locked off by the private deployment policy";
    copy.append(name, detail);
    const select = document.createElement("select");
    select.dataset[dataAttribute] = setting.kind;
    select.setAttribute("aria-label", `${setting.label} permission`);
    setting.availableModes.forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = permissionModeLabel(mode);
      option.selected = mode === setting.mode;
      select.append(option);
    });
    select.disabled = setting.availableModes.length === 1;
    row.append(copy, select);
    list.append(row);
  });
}

function renderPermissions(value) {
  permissionProjection = value;
  const settings = value?.state === "ready" && Array.isArray(value.settings) ? value.settings : [];
  const counts = { "always-ask": 0, "auto-within-policy": 0, "never-allow": 0 };
  settings.forEach((setting) => { if (Object.hasOwn(counts, setting.mode)) counts[setting.mode] += 1; });
  setText("#permission-ask-count", String(counts["always-ask"]));
  setText("#permission-auto-count", String(counts["auto-within-policy"]));
  setText("#permission-never-count", String(counts["never-allow"]));
  setText("#open-permission-settings", counts["auto-within-policy"] ? `Permissions · ${counts["auto-within-policy"]} auto` : `Permissions · ${counts["always-ask"]} ask`);
  const state = $("#permission-settings-state");
  state.className = `status-chip ${settings.length ? "good" : "neutral"}`;
  state.textContent = settings.length ? "Protected" : "Unavailable";
  populatePermissionSettings($("#permission-settings-list"), settings, "permissionKind");
  populatePermissionSettings($("#workspace-permission-list"), settings, "workspacePermissionKind");
  const save = $("#save-permissions");
  const workspaceSave = $("#save-workspace-permissions");
  save.disabled = !settings.length || !privateWorkspaceAuthorized();
  workspaceSave.disabled = save.disabled;
  let guidance;
  if (value?.source === "policy-changed") {
    guidance = "The private deployment policy changed. Pixel reset user defaults to Always ask or Never allow until you save a fresh exact selection.";
  } else if (!settings.length) {
    guidance = "Permission defaults could not be loaded safely.";
  } else {
    guidance = "Saving requires the protected approval identity and invalidates pending proposals.";
  }
  setText("#permission-settings-message", guidance);
  setText("#workspace-permission-message", guidance);
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value;
}

function actionLabel(kind) {
  return {
    configure: "Configuration", plan: "Review plan", verify: "Health check",
    "update-check": "Update check", "backup-create": "Encrypted backup",
    "operations-pause": "Operations emergency pause", "frontier-pause": "Frontier emergency pause",
    "deep-work-pause": "Deep Work scheduling pause",
    "deep-work-resume": "Deep Work scheduling resume",
    "deep-work-cancel": "Deep Work safe cancellation",
    "deep-work-draft": "Inert Deep Work goal draft",
    "deep-work-prepare": "Inactive Deep Work launch package",
    "deep-work-stage": "Dormant Deep Work goal custody",
    "deep-work-service-render": "Inactive Deep Work supervisor bundle",
  }[kind] || kind;
}

function diagnosticLabel(category) {
  return {
    deployment: "Deployment preparation", health: "Deployment health", update: "Update check",
    backup: "Encrypted backup", "operations-containment": "Operations containment",
    "frontier-containment": "Frontier containment", "deep-work-containment": "Deep Work containment",
    "deep-work-recovery": "Deep Work recovery",
    "deep-work-authoring": "Deep Work authoring",
  }[category] || "Local control";
}

function diagnosticGuidance(code) {
  return {
    none: "A later successful check resolved this incident.",
    "inspect-private-deployment-log": "Inspect the private deployment log on this host, correct the cause, then repeat the same fixed action.",
    "inspect-private-health-log": "Inspect the private health log on this host, correct the failed check, then run Check health again.",
    "inspect-private-update-log": "Inspect the private update-check log. Do not prepare or activate an update until the cause is understood.",
    "inspect-private-backup-log": "Inspect the private backup log and prove a successful rehearsal before relying on the backup.",
    "keep-operations-paused": "Keep Operations disabled and contain machine execution through the trusted terminal path.",
    "keep-frontier-paused": "Keep Frontier disabled and contain provider egress through the trusted terminal path.",
    "contain-deep-work-from-terminal": "Stop future Deep Work from the trusted terminal and verify the durable paused checkpoint before recovery.",
    "cancel-deep-work-from-terminal": "Inspect the private cancellation log, then reconcile or safely cancel the exact goal from the trusted terminal.",
    "inspect-private-deep-work-resume-log": "Inspect the private resume log and durable paused checkpoint before creating a fresh resume review.",
    "inspect-private-deep-work-draft-log": "Inspect the private draft log and fixed authoring configuration on the trusted host, then create a fresh exact preview.",
    "inspect-private-deep-work-prepare-log": "Inspect the private launch-preparation log, exact retained draft, and fixed environment before creating a fresh preview.",
    "inspect-private-deep-work-stage-log": "Inspect the private staging log, exact launch package, and dormant goal custody before creating a fresh preview.",
    "inspect-private-deep-work-service-render-log": "Inspect the private service-render log, exact staged goal, and fixed supervisor configuration before creating a fresh preview.",
  }[code] || "Inspect private control state from the trusted host.";
}

function doctorGuidance(code) {
  return {
    "host-supported": "This operating-system release is in Pixel's supported host contract.",
    "use-supported-host": "Use Ubuntu 24.04 LTS or Debian 12 for a supported deployment.",
    "confirm-host-release": "Confirm the operating-system release from the trusted terminal.",
    "python-ready": "The local Python runtime meets Pixel's minimum.",
    "install-python-311": "Install Python 3.11 or newer before deployment.",
    "confirm-python-runtime": "Confirm that Python 3.11 or newer is available from the trusted terminal.",
    "memory-ready": "The rounded memory tier meets the conservative starting floor.",
    "reduce-local-model-load": "Start with a compact or remote model route and measure memory before expanding.",
    "confirm-memory": "Confirm available memory locally before selecting a model.",
    "storage-ready": "The rounded free-storage tier has conservative deployment headroom.",
    "free-local-storage": "Free local storage before downloading runtimes, models, or release artifacts.",
    "confirm-storage": "Confirm free storage locally before downloading deployment artifacts.",
    "prepared-profile-selected": "The prepared profile does not require Pixel's reference containers.",
    "container-detected": "A local container socket was detected for the reference profile.",
    "start-container-runtime": "Install and start the supported container runtime before using the reference profile.",
    "confirm-container-runtime": "Confirm the supported container runtime from the trusted terminal.",
    "model-configuration-ready": "A generated model configuration is present. Validate exact model fit locally before relying on it.",
    "model-not-configured": "No generated model configuration is present; choose and prepare a route when needed.",
    "confirm-generated-model": "The generated model configuration could not be read safely. Inspect it from the trusted terminal.",
  }[code] || "Review this check from the trusted host.";
}

function doctorValue(value) {
  return {
    supported: "Supported", unsupported: "Not supported", unavailable: "Not available", configured: "Configured",
    ubuntu: "Ubuntu", debian: "Debian", linux: "Other Linux", windows: "Windows", macos: "macOS", other: "Other",
    "not-detected": "Not detected", nvidia: "NVIDIA detected", amd: "AMD detected", intel: "Intel detected", multiple: "Multiple vendors detected",
    "socket-detected": "Local socket detected",
    "remote-or-compact": "Remote or compact local model", "compact-local": "Compact local model",
    "balanced-local": "Balanced local model", "larger-local": "Larger local model", "large-memory-local": "Large-memory local model",
    "measure-first": "Measure before increasing context", "start-small": "Start with a small context",
    moderate: "Use a moderate context first", "expanded-after-measurement": "Expand only after measuring headroom",
    "cpu-or-remote-first": "Begin with CPU or an existing private model service",
    "verify-memory-before-use": "Verify usable accelerator memory before selecting a model",
    "not-configured": "Not configured", "4k-16k": "4K-16K", "16k-64k": "16K-64K",
    "64k-256k": "64K-256K", "256k-plus": "256K+", "manual-validation-required": "Manual validation required",
  }[value] || "Not available";
}

function doctorCapacity(value, kind) {
  if (value === "unavailable") return "Not available";
  if (kind === "cpu") return `${value === "16-plus" ? "16+" : value} logical CPUs`;
  const labels = {
    "under-8": "Under 8 GiB", "8-15": "8-15 GiB", "16-31": "16-31 GiB", "32-63": "32-63 GiB", "64-plus": "64+ GiB",
    "under-10": "Under 10 GiB", "10-24": "10-24 GiB", "25-49": "25-49 GiB", "50-99": "50-99 GiB", "100-plus": "100+ GiB",
  };
  return labels[value] || "Not available";
}

function renderDoctor(value) {
  const state = $("#doctor-state");
  state.className = "status-chip";
  if (value.summary.state === "ready") {
    state.textContent = "Ready";
    state.classList.add("good");
    setText("#doctor-summary", "This host meets Pixel's conservative local starting checks.");
  } else if (value.summary.state === "attention") {
    state.textContent = "Review";
    state.classList.add("review");
    setText("#doctor-summary", `${formatCount(value.summary.attentionChecks)} local readiness check${value.summary.attentionChecks === 1 ? "" : "s"} need review.`);
  } else {
    state.textContent = "Incomplete";
    state.classList.add("danger");
    setText("#doctor-summary", "Some local readiness facts or generated configuration could not be verified. Confirm them from the trusted terminal.");
  }
  setText("#doctor-host", `${doctorValue(value.host.contract)} · ${doctorValue(value.host.family)}`);
  setText("#doctor-cpu", doctorCapacity(value.host.cpuCapacity, "cpu"));
  setText("#doctor-memory", doctorCapacity(value.host.memoryCapacityGiB, "memory"));
  setText("#doctor-storage", doctorCapacity(value.host.storageFreeCapacityGiB, "storage"));
  setText("#doctor-accelerator", doctorValue(value.host.accelerator));
  setText("#doctor-model", doctorValue(value.recommendation.localModelClass));
  setText("#doctor-context", doctorValue(value.recommendation.contextGuidance));
  setText("#doctor-accelerator-guidance", doctorValue(value.recommendation.acceleratorGuidance));
  setText("#doctor-configured-model", doctorValue(value.model.discoveryState));
  setText("#doctor-configured-context", doctorValue(value.model.contextCapacity));
  setText("#doctor-reasoning", value.model.discoveryState === "not-configured" ? "Not configured" : value.model.reasoningConfigured === null ? "Not available" : value.model.reasoningConfigured ? "On" : "Off");
  setText("#doctor-fit", doctorValue(value.model.fitAssessment));
  const list = $("#doctor-checks");
  list.replaceChildren();
  value.checks.forEach((check) => {
    const item = document.createElement("li");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = check.id.split("-").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
    const detail = document.createElement("small");
    detail.textContent = doctorGuidance(check.guidanceCode);
    copy.append(title, detail);
    const badge = document.createElement("span");
    badge.className = `boundary-state ${check.state === "pass" || check.state === "not-required" ? "enabled" : "disabled"}`;
    badge.textContent = check.state === "pass" ? "Ready" : check.state === "not-required" ? "N/A" : check.state === "review" ? "Review" : "Unknown";
    item.append(copy, badge);
    list.append(item);
  });
}

function updateGuidance(code) {
  return {
    none: "No release-workspace action is currently indicated.",
    "configure-pixel": "Complete local configuration before inspecting installed update state.",
    "rehearse-update-in-terminal": "Verify the exact signed bundle and rehearse it from the trusted terminal.",
    "verify-migration-in-terminal": "Review the exact signed migration and activation preview from the trusted terminal.",
    "review-terminal-update-receipts": "Review the exact activation or rollback receipt from the trusted terminal.",
    "run-terminal-update-recovery": "Keep the workspace intact and run the exact recovery preview from the trusted terminal.",
    "inspect-private-update-state": "Do not continue the update; inspect private release staging from the trusted terminal.",
  }[code] || "Inspect private release staging from the trusted terminal.";
}

function renderUpdateStatus(value) {
  const state = $("#update-status-state");
  const presentation = {
    "not-configured": ["Not configured", "neutral", "Pixel is not configured, so no installed update workspace is being inspected."],
    idle: ["Idle", "good", "No prepared release workspace is present."],
    prepared: ["Prepared", "review", "A private candidate workspace is present; its signature and migration still require terminal verification."],
    rehearsed: ["Rehearsed", "review", "A rehearsal workspace is present; activation and migration approval remain terminal-only."],
    "activation-recorded": ["Receipt review", "review", "An activation result exists; review its exact private terminal receipt before continuing."],
    "rollback-recorded": ["Receipt review", "review", "A rollback result exists; review exact private receipts before cleanup."],
    "recovery-required": ["Recovery", "danger", "An interrupted update step may require exact terminal recovery. Preserve the workspace."],
    cleaned: ["Clean", "good", "Completed update cleanup history is present and no candidate workspace remains."],
    unavailable: ["Unavailable", "danger", "Private release-workspace shape could not be verified safely. Do not continue the update."],
  }[value.state] || ["Unavailable", "danger", "Private release-workspace shape could not be verified safely."];
  state.className = `status-chip ${presentation[1]}`;
  state.textContent = presentation[0];
  setText("#update-status-summary", presentation[2]);
  setText("#update-current-version", `Pixel ${value.currentVersion}`);
  setText("#update-candidate-versions", value.candidateVersions.length ? value.candidateVersions.map((version) => `Pixel ${version}`).join(", ") : "None");
  setText("#update-prepared-count", formatCount(value.counts.prepared));
  setText("#update-rehearsed-count", formatCount(value.counts.rehearsed));
  setText("#update-activation-count", formatCount(value.counts.activations));
  const migration = {
    "not-applicable": "Not required",
    "terminal-verification-required": "Terminal verification required",
    "terminal-receipt-review-required": "Review exact terminal receipt",
    "recovery-required": "Recover before migration review",
    unavailable: "Could not verify safely",
  }[value.migration.state] || "Could not verify safely";
  setText("#update-migration-state", migration);
  setText("#update-next-action", updateGuidance(value.nextActionCode));
}

function backupRecoveryGuidance(code) {
  return {
    "enable-private-backup-policy": "Enable backup creation in the owner-only control policy, including its private destination and public age recipient.",
    "create-encrypted-backup": "Create a new signed encrypted backup, then validate and rehearse it from the trusted terminal.",
    "validate-and-rehearse-backup-in-terminal": "Validate the signature and decryption, then rehearse into a new empty root before considering restore.",
    "inspect-private-backup-log": "Do not rely on the latest backup attempt; inspect its private control log and create a fresh backup after fixing the cause.",
    "inspect-private-control-state": "Do not rely on this guide; inspect private control receipts from the trusted host.",
  }[code] || "Inspect private recovery state from the trusted host.";
}

function resumeGuidance(value) {
  return {
    "not-indicated": "No browser-initiated pause is recorded.",
    "containment-not-confirmed": "The pause attempt failed; re-establish containment from the trusted terminal before recovery.",
    "terminal-state-review-required": "A pause succeeded, but current broker state is not projected. Inspect it and complete incident review before any terminal resume.",
    unavailable: "Private receipts could not be verified; do not resume authority.",
  }[value] || "Do not resume authority until terminal state is verified.";
}

function renderRecoveryGuide(value) {
  const state = $("#recovery-guide-state");
  const presentation = {
    ready: ["Guide ready", "good", "Content-free guidance is available. Terminal backup validation and broker-state checks remain authoritative."],
    attention: ["Review", "review", "A backup follow-up or terminal authority-state review needs attention."],
    containment: ["Contain", "danger", "Containment was not confirmed. Keep authority off and use the trusted terminal recovery path."],
    unavailable: ["Unavailable", "danger", "Private recovery receipts could not be verified safely. Do not restore or resume."],
  }[value.state] || ["Unavailable", "danger", "Private recovery state could not be verified safely."];
  state.className = `status-chip ${presentation[1]}`;
  state.textContent = presentation[0];
  setText("#recovery-guide-summary", presentation[2]);
  setText("#recovery-backup-policy", value.backup.creationEnabled === true ? "Backup creation enabled" : value.backup.creationEnabled === false ? "Backup creation disabled" : "Not available");
  setText("#recovery-backup-last", {
    none: "No retained creation result", succeeded: "Latest creation succeeded",
    failed: "Latest creation failed", unavailable: "Not available",
  }[value.backup.lastCreation] || "Not available");
  setText("#recovery-backup-next", backupRecoveryGuidance(value.backup.nextActionCode));
  setText("#recovery-incident-state", {
    clear: "No open control incidents", attention: "Inspect private evidence",
    containment: "Re-establish containment", unavailable: "Not available",
  }[value.incident.state] || "Not available");
  setText("#recovery-operations-resume", resumeGuidance(value.incident.operationsResume));
  setText("#recovery-frontier-resume", resumeGuidance(value.incident.frontierResume));
}

function renderDiagnostics(value) {
  const summary = value.summary;
  const state = $("#diagnostic-state");
  state.className = "status-chip";
  if (summary.state === "clear") {
    state.textContent = "Clear";
    state.classList.add("good");
    setText("#diagnostic-summary", summary.retainedIncidents ? "Recorded incidents are resolved." : "No local control incidents are recorded.");
  } else if (summary.state === "containment") {
    state.textContent = "Containment";
    state.classList.add("danger");
    setText("#diagnostic-summary", `${formatCount(summary.criticalIncidents)} critical containment incident${summary.criticalIncidents === 1 ? "" : "s"} require attention.`);
  } else if (summary.state === "attention") {
    state.textContent = "Attention";
    state.classList.add("review");
    setText("#diagnostic-summary", `${formatCount(summary.openIncidents)} local incident${summary.openIncidents === 1 ? "" : "s"} require attention.`);
  } else {
    state.textContent = "Unavailable";
    state.classList.add("danger");
    setText("#diagnostic-summary", "Incident receipts could not be verified. Inspect private control state before relying on this view.");
  }
  const list = $("#diagnostic-incidents");
  list.replaceChildren();
  if (!value.incidents.length) {
    const item = document.createElement("li");
    item.textContent = summary.state === "unavailable" ? "No incident detail is projected while receipt integrity is unavailable." : "No incidents recorded.";
    list.append(item);
    return;
  }
  value.incidents.forEach((incident) => {
    const item = document.createElement("li");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = diagnosticLabel(incident.category);
    const detail = document.createElement("small");
    detail.textContent = diagnosticGuidance(incident.nextActionCode);
    const time = document.createElement("small");
    time.textContent = `${incident.status} · ${new Date(incident.detectedAt).toLocaleString()} · private evidence ${incident.privateEvidenceAvailable ? "available" : "not retained"}`;
    copy.append(title, detail, time);
    const severity = document.createElement("span");
    severity.className = `diagnostic-severity ${incident.status === "resolved" ? "resolved" : incident.severity}`;
    severity.textContent = incident.status === "resolved" ? "Resolved" : incident.severity === "critical" ? "Critical" : "Review";
    item.append(copy, severity);
    list.append(item);
  });
}

function formatCount(value) {
  return Number.isInteger(value) ? value.toLocaleString() : "Not available";
}

function formatWorkDuration(seconds) {
  if (!Number.isInteger(seconds) || seconds < 0) return "Not available";
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${formatCount(minutes)} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return `${formatCount(hours)}h ${remaining}m`;
}

function formatWorkBytes(bytes) {
  if (!Number.isInteger(bytes) || bytes < 0) return "Not available";
  if (bytes < 1024) return `${formatCount(bytes)} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${unit}`;
}

function deepWorkLabel(value) {
  return String(value).split("-").map((part) => part ? part[0].toUpperCase() + part.slice(1) : "").join(" ");
}

function deepWorkStateLabel(value) {
  return {
    authorized: "Authorized", running: "Working", verifying: "Verifying", verified: "Verified",
    "waiting-authority": "Waiting for approval", completed: "Completed", failed: "Failed",
    cancelled: "Cancelled", "cleanup-failed": "Cleanup needs attention", "recovery-inconclusive": "Recovery needs review", "budget-exhausted": "Budget reached", "no-progress": "No progress",
  }[value] || deepWorkLabel(value);
}

function deepWorkGoalActionLabel(value) {
  return {
    "dispatch-child": "Start the next ready milestone", "record-completed": "Record verified goal completion",
    "recover-or-continue-child": "Continue or recover the current milestone", "wait-for-child-authority": "Wait for separate approval",
    paused: "Remain safely paused", terminal: "No further work will be scheduled", "fail-closed": "Inspect private goal state",
  }[value] || "Not available";
}

function deepWorkActivityLabel(value) {
  return {
    authorized: "Work authorized", "work-started": "Work started", "local-step-completed": "Local step completed",
    "artifact-recorded": "Artifact recorded", "verification-started": "Verification started",
    "verification-passed": "Verification passed", "verification-failed": "Verification failed",
    "boundary-requested": "Additional authority requested", "checkpoint-recovered": "Checkpoint recovered",
    "session-completed": "Session completed", "session-failed": "Session failed",
    "session-cancelled": "Session cancelled", "cleanup-failed": "Cleanup could not be proven", "recovery-inconclusive": "Recovery evidence is inconclusive", "budget-exhausted": "Budget reached", "no-progress": "No progress detected",
  }[value] || "Local work update";
}

function deepWorkBoundaryLabel(value) {
  const state = {
    "within-authority": "Within approved boundary", "waiting-approval": "Waiting for separate approval",
    blocked: "Blocked at the safety boundary",
  }[value.state] || "Boundary state unavailable";
  if (value.requestedExpansion === "none") return state;
  const expansion = {
    network: "network access", credential: "credential use", "external-effect": "an external effect",
    scope: "broader scope", budget: "more budget",
  }[value.requestedExpansion] || "additional authority";
  return `${state} · requested ${expansion}`;
}

function deepWorkCapabilityLabel(value) {
  if (value.state === "not-configured") return "No extra job-scoped tools";
  if (value.state === "recovery-required") return "Tool recovery required before reuse";
  const count = `${formatCount(value.toolCount)} job-scoped tool${value.toolCount === 1 ? "" : "s"}`;
  return value.state === "authorized" ? `${count} authorized for this attempt` : `${count} configured for this job`;
}

function appendWorkField(list, label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  row.append(term, detail);
  list.append(row);
}

function workControlLabel(text, control, detail = "") {
  const label = document.createElement("label");
  label.append(document.createTextNode(text), control);
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    label.append(small);
  }
  return label;
}

function parseDomainList(value) {
  const domains = String(value || "").split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
  if (new Set(domains).size !== domains.length) throw new Error("Research domain lists cannot contain duplicates.");
  return domains;
}

function renumberMilestones() {
  const cards = [...document.querySelectorAll(".milestone-card")];
  cards.forEach((card, index) => {
    card.querySelector("h4").textContent = `Milestone ${index + 1}`;
    card.querySelector(".remove-milestone").disabled = cards.length === 1;
    const dependencies = card.querySelector(".milestone-dependencies");
    const selected = new Set([...dependencies.querySelectorAll(".milestone-dependency:checked")].map((input) => input.value));
    const initialized = dependencies.dataset.initialized === "true";
    const legend = dependencies.querySelector("legend");
    dependencies.replaceChildren(legend);
    if (index === 0) {
      const empty = document.createElement("small");
      empty.textContent = "This is a starting milestone and can run as soon as the goal is active.";
      dependencies.append(empty);
    } else {
      cards.slice(0, index).forEach((candidate, dependencyIndex) => {
        const option = document.createElement("label");
        option.className = "milestone-input-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "milestone-dependency";
        checkbox.value = candidate.dataset.milestone;
        checkbox.checked = initialized ? selected.has(checkbox.value) : dependencyIndex === index - 1;
        const description = document.createElement("span");
        description.textContent = `Milestone ${dependencyIndex + 1} must be independently verified first`;
        option.append(checkbox, description);
        dependencies.append(option);
      });
    }
    dependencies.dataset.initialized = "true";
  });
  if (workAuthoring?.state === "ready") $("#add-deep-work-milestone").disabled = cards.length >= workAuthoring.limits.maxMilestones;
}

function addMilestone() {
  if (!workAuthoring || workAuthoring.state !== "ready") return;
  const list = $("#deep-work-milestone-list");
  if (list.children.length >= workAuthoring.limits.maxMilestones) {
    notify(`This guided view supports up to ${workAuthoring.limits.maxMilestones} milestones.`, "error");
    return;
  }
  milestoneSequence += 1;
  const card = document.createElement("article");
  card.className = "milestone-card";
  card.dataset.milestone = String(milestoneSequence);
  const heading = document.createElement("div");
  heading.className = "milestone-heading";
  const title = document.createElement("h4");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button quiet remove-milestone";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => { card.remove(); renumberMilestones(); });
  heading.append(title, remove);

  const grid = document.createElement("div");
  grid.className = "field-grid two";
  const kind = document.createElement("select");
  kind.className = "milestone-kind";
  const kindLabels = { inspect: "Inspect and assess", build: "Build or improve", research: "Research the public web", "analyze-data": "Analyze admitted data" };
  workAuthoring.profiles.filter((profile) => profile.enabled).forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.kind;
    option.textContent = kindLabels[profile.kind];
    if (profile.kind === "analyze-data" && !workAuthoring.inputs.some((input) => input.kind === "dataset")) option.disabled = true;
    kind.append(option);
  });
  const effort = document.createElement("select");
  effort.className = "milestone-effort";
  for (const [value, label] of [["quick", "Quick"], ["standard", "Standard"], ["deep", "Deep"]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    if (value === "standard") option.selected = true;
    effort.append(option);
  }
  grid.append(
    workControlLabel("Work type", kind, "Only capabilities enabled by the private host policy appear."),
    workControlLabel("Effort ceiling", effort, "The private policy can clamp this lower."),
  );
  const objective = document.createElement("textarea");
  objective.className = "milestone-objective";
  objective.rows = 3;
  objective.maxLength = 4000;
  objective.required = true;
  objective.placeholder = "What should this milestone accomplish?";
  const done = document.createElement("textarea");
  done.className = "milestone-done";
  done.rows = 3;
  done.maxLength = 4007;
  done.required = true;
  done.placeholder = "One independently checkable completion condition per line";

  const dependencies = document.createElement("fieldset");
  dependencies.className = "milestone-dependencies";
  const dependencyLegend = document.createElement("legend");
  dependencyLegend.textContent = "Wait for these milestones";
  dependencies.append(dependencyLegend);

  const inputs = document.createElement("fieldset");
  inputs.className = "milestone-inputs";
  const legend = document.createElement("legend");
  legend.textContent = "Admitted local inputs";
  inputs.append(legend);
  if (!workAuthoring.inputs.length) {
    const empty = document.createElement("small");
    empty.textContent = "No local input snapshots are admitted. Admission remains a trusted-terminal step.";
    inputs.append(empty);
  }
  workAuthoring.inputs.forEach((input) => {
    const option = document.createElement("label");
    option.className = "milestone-input-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "milestone-input";
    checkbox.value = input.handle;
    const description = document.createElement("span");
    const dataset = input.datasetCount ? ` · ${formatCount(input.datasetCount)} dataset file${input.datasetCount === 1 ? "" : "s"} (${input.datasetFormats.join(", ")})` : "";
    description.textContent = `${input.label} · ${deepWorkLabel(input.classification)} · ${formatWorkBytes(input.bytes)}${dataset}`;
    option.append(checkbox, description);
    inputs.append(option);
  });

  const research = document.createElement("div");
  research.className = "research-fields field-grid two";
  research.hidden = true;
  const source = document.createElement("select");
  source.className = "research-source";
  for (const [value, label] of [["web", "General web"], ["news", "News"], ["academic", "Academic"], ["forum", "Forums"]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    source.append(option);
  }
  const allowed = document.createElement("input");
  allowed.className = "research-allowed";
  allowed.maxLength = 2000;
  allowed.placeholder = "example.org, docs.example.org (optional)";
  const denied = document.createElement("input");
  denied.className = "research-denied";
  denied.maxLength = 2000;
  denied.placeholder = "blocked.example.org (optional)";
  research.append(
    workControlLabel("Source type", source),
    workControlLabel("Only these domains", allowed, "Comma separated; leave empty for any policy-allowed public domain."),
    workControlLabel("Never these domains", denied, "Comma separated."),
  );
  const syncKind = () => {
    const isResearch = kind.value === "research";
    research.hidden = !isResearch;
    research.querySelectorAll("input, select").forEach((control) => { control.disabled = !isResearch; });
    inputs.querySelectorAll("input").forEach((control) => {
      control.disabled = isResearch;
      if (isResearch) control.checked = false;
    });
  };
  kind.addEventListener("change", syncKind);
  card.append(
    heading, grid,
    workControlLabel("Milestone objective", objective),
    workControlLabel("Done when", done, "Each line becomes a separate independent completion check."),
    dependencies, inputs, research,
  );
  list.append(card);
  syncKind();
  renumberMilestones();
}

function renderWorkAuthoring(value) {
  workAuthoring = value;
  const state = $("#deep-work-authoring-state");
  const fields = $("#deep-work-draft-fields");
  const presentation = {
    ready: ["Ready", "good", `${formatCount(value.inputs.length)} admitted local input${value.inputs.length === 1 ? " is" : "s are"} available by opaque handle. ${formatCount(value.limits.maxDrafts - value.limits.draftsUsed)} private draft slot${value.limits.maxDrafts - value.limits.draftsUsed === 1 ? " remains" : "s remain"}.`],
    disabled: ["Disabled", "neutral", privateWorkspaceAuthorized() ? "Enable inert drafting in the owner-only control policy and supply a private fixed authoring configuration." : "Open the exact URL printed by ./pixel ui or sign in through the authenticated portal to unlock this private view."],
    unavailable: ["Unavailable", "danger", "The fixed private policy, input catalog, object store, or draft store could not be verified safely."],
  }[value.state] || ["Unavailable", "danger", "Deep Work authoring could not be verified safely."];
  state.textContent = presentation[0];
  state.className = `service-state ${presentation[1]}`;
  setText("#deep-work-authoring-guidance", presentation[2]);
  fields.disabled = value.state !== "ready";
  if (value.state !== "ready") {
    $("#deep-work-milestone-list").replaceChildren();
    workAuthoringRevision = null;
    return;
  }
  if (workAuthoringRevision !== value.revision) {
    $("#deep-work-milestone-list").replaceChildren();
    workAuthoringRevision = value.revision;
    addMilestone();
  }
  $("#add-deep-work-milestone").disabled = $("#deep-work-milestone-list").children.length >= value.limits.maxMilestones;
}

function renderWorkDraftReviews(value) {
  workDraftReviews = value;
  const state = $("#deep-work-drafts-state");
  const list = $("#deep-work-drafts");
  const presentation = {
    ready: [value.drafts.length ? `${formatCount(value.drafts.length)} retained` : "Ready", "good", value.drafts.length ? `${value.launch.state === "ready" ? "Inactive launch preparation is available for reviewed drafts." : value.launch.state === "full" ? "Launch storage is full; retained drafts remain reviewable." : value.launch.state === "unavailable" ? "Launch preparation failed closed; retained drafts remain reviewable." : "Launch preparation is disabled."} These are exact retained drafts, not chat promises.` : "No retained goal draft exists yet. Create one above, then it will appear here from durable local bytes."],
    disabled: ["Disabled", "neutral", privateWorkspaceAuthorized() ? "Goal draft review becomes available with the separately enabled private authoring configuration." : "Open the exact URL printed by ./pixel ui or sign in through the authenticated portal to unlock private draft review."],
    unavailable: ["Unavailable", "danger", "Pixel could not validate the complete retained draft set. No partial or potentially stale review is shown."],
  }[value.state] || ["Unavailable", "danger", "Retained goal drafts could not be verified safely."];
  state.textContent = presentation[0];
  state.className = `service-state ${presentation[1]}`;
  setText("#deep-work-drafts-message", presentation[2]);
  list.replaceChildren();
  if (value.state !== "ready" || !value.drafts.length) {
    const empty = document.createElement("p");
    empty.className = "work-empty";
    empty.textContent = value.state === "unavailable" ? "Draft details are hidden because retained state did not pass validation." : "No retained goal drafts are available.";
    list.append(empty);
    return;
  }
  value.drafts.forEach((draft, draftIndex) => {
    const card = document.createElement("article");
    card.className = "work-draft-card";
    card.dataset.draftHandle = draft.handle;
    const heading = document.createElement("div");
    heading.className = "work-session-heading";
    const title = document.createElement("h4");
    title.textContent = draft.objective;
    const badge = document.createElement("span");
    const preparationPresentation = {
      "prepared-inactive": ["attention", "Prepared, inactive"],
      "staged-inactive": ["attention", "Staged, dormant"],
      unavailable: ["danger", "Package state unavailable"],
      draft: ["good", "Exact retained draft"],
    }[draft.preparationState] || ["danger", "Unavailable"];
    badge.className = `service-state ${preparationPresentation[0]}`;
    badge.textContent = preparationPresentation[1];
    heading.append(title, badge);
    const facts = document.createElement("dl");
    facts.className = "work-session-facts";
    appendWorkField(facts, "Created", new Date(draft.createdAt).toLocaleString());
    appendWorkField(facts, "Sensitivity", deepWorkLabel(draft.dataClassification));
    appendWorkField(facts, "Plan", `${formatCount(draft.milestoneCount)} milestone${draft.milestoneCount === 1 ? "" : "s"} · local model only`);
    appendWorkField(facts, "Authority", "No execution, scheduling, external effects, or completion authority");
    const details = document.createElement("details");
    if (draftIndex === 0) details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "Review milestones, limits, and verification";
    const milestones = document.createElement("ol");
    milestones.className = "draft-milestone-review";
    draft.milestones.forEach((milestone) => {
      const item = document.createElement("li");
      const milestoneTitle = document.createElement("strong");
      milestoneTitle.textContent = `${deepWorkLabel(milestone.profile)}: ${milestone.objective}`;
      const dependency = document.createElement("p");
      dependency.textContent = milestone.dependsOn.length ? `Starts after milestone${milestone.dependsOn.length === 1 ? "" : "s"} ${milestone.dependsOn.join(", ")} pass independent verification.` : "Starting milestone; eligible when the reviewed goal is activated.";
      const criteriaTitle = document.createElement("span");
      criteriaTitle.className = "draft-review-label";
      criteriaTitle.textContent = "Done only when";
      const criteria = document.createElement("ul");
      milestone.doneWhen.forEach((criterion) => { const row = document.createElement("li"); row.textContent = criterion; criteria.append(row); });
      const envelope = document.createElement("p");
      const services = milestone.capabilities.brokeredServices.length ? milestone.capabilities.brokeredServices.map(deepWorkLabel).join(", ") : "none";
      envelope.textContent = `${deepWorkLabel(milestone.effort)} effort · ${formatWorkDuration(milestone.budgets.maxRuntimeSeconds)} max runtime · ${formatCount(milestone.budgets.maxIterations)} iterations · ${formatCount(milestone.budgets.maxToolCalls)} tool calls · ${formatCount(milestone.budgets.maxModelRequests)} model requests · ${formatCount(milestone.inputCount)} admitted inputs · brokered services: ${services}.`;
      const verifier = document.createElement("p");
      verifier.textContent = `Completion check: ${deepWorkLabel(milestone.verification)}. Worker claims are insufficient; external effects are disabled.`;
      item.append(milestoneTitle, dependency, criteriaTitle, criteria, envelope, verifier);
      milestones.append(item);
    });
    const digest = document.createElement("p");
    digest.className = "draft-review-digest";
    const digestLabel = document.createElement("strong");
    digestLabel.textContent = "Exact review digest: ";
    const digestCode = document.createElement("code");
    digestCode.textContent = draft.reviewSha256;
    digest.append(digestLabel, digestCode);
    details.append(summary, milestones, digest);
    if (draft.launchPackage) {
      const packageFacts = document.createElement("dl");
      packageFacts.className = "work-session-facts launch-package-facts";
      for (const [label, value] of [
        ["Package", draft.launchPackage.state === "staged-inactive" ? "Dormant stage receipt retained" : "Prepared and inactive"],
        ["Compiled children", formatCount(draft.launchPackage.childCount)],
        ["Profiles", draft.launchPackage.profiles.join(", ")],
        ["Earliest lease expiry", formatTime(draft.launchPackage.earliestLeaseExpiry)],
      ]) {
        const row = document.createElement("div"); const term = document.createElement("dt"); const description = document.createElement("dd");
        term.textContent = label; description.textContent = value; row.append(term, description); packageFacts.append(row);
      }
      const manifest = document.createElement("p"); manifest.className = "review-digest";
      const manifestLabel = document.createElement("strong"); manifestLabel.textContent = "Exact launch manifest SHA-256";
      const manifestCode = document.createElement("code"); manifestCode.textContent = draft.launchPackage.manifestSha256;
      manifest.append(manifestLabel, manifestCode); details.append(packageFacts, manifest);
      if (draft.launchPackage.servicePackage) {
        const serviceFacts = document.createElement("dl");
        serviceFacts.className = "work-session-facts launch-package-facts";
        for (const [label, value] of [
          ["Supervisor", "Rendered, inactive, and not installed"],
          ["Execution model", "Durable checkpoint events"],
          ["Watchdog", "Liveness recovery only; not a work cadence"],
        ]) {
          const row = document.createElement("div"); const term = document.createElement("dt"); const description = document.createElement("dd");
          term.textContent = label; description.textContent = value; row.append(term, description); serviceFacts.append(row);
        }
        const serviceManifest = document.createElement("p"); serviceManifest.className = "review-digest";
        const serviceManifestLabel = document.createElement("strong"); serviceManifestLabel.textContent = "Exact inactive supervisor manifest SHA-256";
        const serviceManifestCode = document.createElement("code"); serviceManifestCode.textContent = draft.launchPackage.servicePackage.manifestSha256;
        serviceManifest.append(serviceManifestLabel, serviceManifestCode); details.append(serviceFacts, serviceManifest);
      }
    }
    const actions = document.createElement("div");
    actions.className = "form-actions draft-review-actions";
    const prepare = document.createElement("button");
    prepare.type = "button";
    prepare.className = "button primary";
    prepare.textContent = draft.launchPackage ? "Launch package retained" : draft.preparationState === "unavailable" ? "Package state unavailable" : "Prepare exact inactive goal";
    prepare.disabled = !draft.canPrepare;
    prepare.addEventListener("click", async () => {
      prepare.disabled = true;
      setText("#deep-work-drafts-message", "Rechecking the exact retained draft and fixed private launch environment…");
      try {
        if (!privateWorkspaceAuthorized() || !workDraftReviews?.revision) throw new Error("Use an authenticated private workspace before preparing private work.");
        const response = await requestFixedAction({ schemaVersion: 1, kind: "deep-work-prepare", authoringRevision: workDraftReviews.revision, draftHandle: draft.handle, reviewSha256: draft.reviewSha256 });
        if (response.state === "approval-required") setText("#deep-work-drafts-message", "Exact inactive launch preparation is ready for confirmation. No work has been staged or run.");
      } catch (error) {
        setText("#deep-work-drafts-message", error.message);
        notify(error.message, "error");
        prepare.disabled = !draft.canPrepare;
      }
    });
    actions.append(prepare);
    if (draft.launchPackage) {
      const stage = document.createElement("button");
      stage.type = "button"; stage.className = "button primary";
      stage.textContent = draft.launchPackage.stageReceiptPresent ? "Recheck dormant custody" : "Stage exact dormant goal";
      stage.disabled = !draft.launchPackage.canStage;
      stage.addEventListener("click", async () => {
        stage.disabled = true;
        setText("#deep-work-drafts-message", "Rechecking the exact launch package and private dormant-custody boundary…");
        try {
          if (!privateWorkspaceAuthorized() || !workDraftReviews?.revision) throw new Error("Use an authenticated private workspace before staging private work.");
          const response = await requestFixedAction({
            schemaVersion: 1, kind: "deep-work-stage", authoringRevision: workDraftReviews.revision,
            draftHandle: draft.handle, reviewSha256: draft.reviewSha256,
            packageHandle: draft.launchPackage.handle, manifestSha256: draft.launchPackage.manifestSha256,
          });
          if (response.state === "approval-required") setText("#deep-work-drafts-message", "Exact dormant staging is ready for confirmation. No worker, schedule, service, model, or provider will start.");
        } catch (error) {
          setText("#deep-work-drafts-message", error.message); notify(error.message, "error");
          stage.disabled = !draft.launchPackage.canStage;
        }
      });
      actions.append(stage);
      if (draft.launchPackage.stageReceiptPresent) {
        const renderService = document.createElement("button");
        renderService.type = "button"; renderService.className = "button primary";
        renderService.textContent = draft.launchPackage.servicePackage ? "Inactive supervisor retained" : "Render inactive supervisor";
        renderService.disabled = !draft.launchPackage.canRenderService;
        renderService.addEventListener("click", async () => {
          renderService.disabled = true;
          setText("#deep-work-drafts-message", "Rechecking exact dormant custody and the private supervisor-render boundary...");
          try {
            if (!privateWorkspaceAuthorized() || !workDraftReviews?.revision) throw new Error("Use an authenticated private workspace before rendering private supervision.");
            const response = await requestFixedAction({
              schemaVersion: 1, kind: "deep-work-service-render", authoringRevision: workDraftReviews.revision,
              draftHandle: draft.handle, reviewSha256: draft.reviewSha256,
              packageHandle: draft.launchPackage.handle, manifestSha256: draft.launchPackage.manifestSha256,
            });
            if (response.state === "approval-required") setText("#deep-work-drafts-message", "Exact inactive supervision is ready for confirmation. No unit will be installed or activated, and no work will start.");
          } catch (error) {
            setText("#deep-work-drafts-message", error.message); notify(error.message, "error");
            renderService.disabled = !draft.launchPackage.canRenderService;
          }
        });
        actions.append(renderService);
      }
    }
    card.append(heading, facts, details, actions);
    list.append(card);
  });
}

function deepWorkDraftRequest() {
  if (!workAuthoring || workAuthoring.state !== "ready" || !workAuthoring.revision) throw new Error("Deep Work authoring is not ready.");
  const form = $("#deep-work-draft-form");
  const objective = form.elements.goalObjective.value.trim();
  if (!objective) throw new Error("Describe the overall goal first.");
  const classification = form.elements.goalClassification.value;
  const cards = [...document.querySelectorAll(".milestone-card")];
  const milestoneNumber = new Map(cards.map((card, index) => [card.dataset.milestone, index + 1]));
  const milestones = cards.map((card) => {
    const kind = card.querySelector(".milestone-kind").value;
    const milestoneObjective = card.querySelector(".milestone-objective").value.trim();
    const doneWhen = card.querySelector(".milestone-done").value.split(/\r?\n/u).map((item) => item.trim()).filter(Boolean);
    if (!milestoneObjective) throw new Error("Every milestone needs an objective.");
    if (!doneWhen.length) throw new Error("Every milestone needs at least one independently checkable completion condition.");
    if (doneWhen.length > workAuthoring.limits.maxCriteriaPerMilestone) throw new Error(`Each milestone supports up to ${workAuthoring.limits.maxCriteriaPerMilestone} completion checks.`);
    const inputHandles = [...card.querySelectorAll(".milestone-input:checked")].map((input) => input.value);
    const dependsOn = [...card.querySelectorAll(".milestone-dependency:checked")]
      .map((input) => milestoneNumber.get(input.value))
      .filter((value) => Number.isSafeInteger(value))
      .sort((left, right) => left - right);
    const milestone = { kind, objective: milestoneObjective, doneWhen, dependsOn, inputHandles, effort: card.querySelector(".milestone-effort").value };
    if (kind === "research") {
      if (classification !== "public") throw new Error("Public web research must use a Public goal and cannot receive local inputs.");
      milestone.research = {
        allowedDomains: parseDomainList(card.querySelector(".research-allowed").value),
        deniedDomains: parseDomainList(card.querySelector(".research-denied").value),
        sourceTypes: [card.querySelector(".research-source").value],
      };
    }
    return milestone;
  });
  return {
    schemaVersion: 1, kind: "deep-work-draft", authoringRevision: workAuthoring.revision,
    ...(workHandoffTaskHandle ? { chatTaskHandle: workHandoffTaskHandle } : {}),
    objective, dataClassification: classification, milestones,
  };
}

function renderDeepWork(value) {
  const state = $("#deep-work-state");
  const presentation = {
    ready: ["Ready", "good", "Deep Work is ready. No local session is currently running."],
    busy: ["Working", "good", `${formatCount(value.summary.activeSessions)} local session${value.summary.activeSessions === 1 ? " is" : "s are"} in progress.`],
    degraded: ["Degraded", "review", "Deep Work is operating with a service concern. Inspect private evidence from the trusted host."],
    offline: ["Offline", "danger", "The last content-free update is stale. Do not rely on it as current controller state."],
    disabled: ["Not connected", "neutral", "The Deep Work controller is not connected to this local control surface."],
    unavailable: ["Unavailable", "danger", "Deep Work state could not be verified safely. Private work details remain hidden."],
  }[value.state] || ["Unavailable", "danger", "Deep Work state could not be verified safely."];
  state.className = `status-chip ${presentation[1]}`;
  state.textContent = presentation[0];
  setText("#deep-work-summary", presentation[2]);
  setText("#deep-work-active", formatCount(value.summary.activeSessions));
  setText("#deep-work-attention", formatCount(value.summary.attentionSessions));
  setText("#deep-work-artifacts", formatCount(value.summary.artifactCount));
  setText("#deep-work-service-concerns", formatCount(value.summary.degradedServices));

  const goal = value.goal;
  const goalState = $("#deep-work-goal-state");
  const goalProgress = $("#deep-work-goal-progress");
  if (!goal) {
    goalState.className = "service-state neutral";
    goalState.textContent = "Not connected";
    goalProgress.max = 1;
    goalProgress.value = 0;
    goalProgress.setAttribute("aria-valuetext", "No goal progress is available");
    setText("#deep-work-goal-milestones", "Not available");
    setText("#deep-work-goal-checkpoints", "Not available");
    setText("#deep-work-goal-next", "Not available");
    setText("#deep-work-goal-model", "Not connected");
    setText("#deep-work-goal-watchdog", "Not connected");
    setText("#deep-work-goal-runtime", "Not available");
    setText("#deep-work-goal-requests", "Not available");
    setText("#deep-work-goal-tokens", "Not available");
    setText("#deep-work-goal-failures", "Not available");
    for (const id of ["jobs", "runtime", "requests", "input", "output", "network", "artifacts", "failures"]) setText(`#deep-work-budget-${id}`, "Not available");
    setText("#deep-work-goal-continuity", "Event-driven restart safety and independent verification are not connected.");
  } else {
    const goalAttention = ["waiting-authority", "recovery-inconclusive", "failed", "budget-exhausted", "no-progress"].includes(goal.state);
    goalState.className = `service-state ${["running", "completed"].includes(goal.state) ? "good" : goalAttention ? "attention" : "neutral"}`;
    goalState.textContent = value.state === "offline" ? `Last known: ${deepWorkStateLabel(goal.state)}` : deepWorkStateLabel(goal.state);
    goalProgress.max = goal.progress.milestonesTotal;
    goalProgress.value = goal.progress.milestonesCompleted;
    goalProgress.setAttribute("aria-valuetext", `${formatCount(goal.progress.milestonesCompleted)} of ${formatCount(goal.progress.milestonesTotal)} milestones independently verified`);
    setText("#deep-work-goal-milestones", `${formatCount(goal.progress.milestonesCompleted)} verified of ${formatCount(goal.progress.milestonesTotal)} · ${formatCount(goal.progress.jobsStarted)} started`);
    setText("#deep-work-goal-checkpoints", formatCount(goal.continuity.checkpointSequence + 1));
    setText("#deep-work-goal-next", deepWorkGoalActionLabel(goal.nextAction));
    setText("#deep-work-goal-model", "Advance on durable progress events");
    setText("#deep-work-goal-watchdog", "Recover missed events or interrupted supervision");
    setText("#deep-work-goal-runtime", formatWorkDuration(goal.usage.runtimeSeconds));
    setText("#deep-work-goal-requests", formatCount(goal.usage.modelRequests));
    setText("#deep-work-goal-tokens", `${formatCount(goal.usage.inputTokens)} input · ${formatCount(goal.usage.outputTokens)} output`);
    setText("#deep-work-goal-failures", formatCount(goal.progress.failures));
    setText("#deep-work-budget-jobs", formatBudget(goal.budgets.used.jobs, goal.budgets.limits.jobs, goal.budgets.remaining.jobs));
    setText("#deep-work-budget-runtime", formatDurationBudget(goal.budgets.used.runtimeSeconds, goal.budgets.limits.runtimeSeconds, goal.budgets.remaining.runtimeSeconds));
    setText("#deep-work-budget-requests", formatBudget(goal.budgets.used.modelRequests, goal.budgets.limits.modelRequests, goal.budgets.remaining.modelRequests));
    setText("#deep-work-budget-input", formatBudget(goal.budgets.used.inputTokens, goal.budgets.limits.inputTokens, goal.budgets.remaining.inputTokens));
    setText("#deep-work-budget-output", formatBudget(goal.budgets.used.outputTokens, goal.budgets.limits.outputTokens, goal.budgets.remaining.outputTokens));
    setText("#deep-work-budget-network", formatByteBudget(goal.budgets.used.networkBytes, goal.budgets.limits.networkBytes, goal.budgets.remaining.networkBytes));
    setText("#deep-work-budget-artifacts", formatByteBudget(goal.budgets.used.artifactBytes, goal.budgets.limits.artifactBytes, goal.budgets.remaining.artifactBytes));
    setText("#deep-work-budget-failures", formatBudget(goal.budgets.used.failures, goal.budgets.limits.failures, goal.budgets.remaining.failures));
    setText("#deep-work-goal-continuity", "Each durable checkpoint triggers the next bounded decision. Elapsed time creates no progress; the watchdog only restores liveness. Goal completion still requires independent verification of every milestone.");
  }

  const services = $("#deep-work-services");
  services.replaceChildren();
  if (!value.services.length) {
    const item = document.createElement("li");
    item.textContent = value.state === "disabled" ? "The Deep Work controller is not connected." : "No service health is projected.";
    services.append(item);
  } else {
    value.services.forEach((service) => {
      const item = document.createElement("li");
      const label = document.createElement("strong");
      label.textContent = deepWorkLabel(service.id);
      const serviceState = document.createElement("span");
      serviceState.className = `service-state ${["ready", "busy"].includes(service.state) ? "good" : service.state === "disabled" ? "neutral" : "attention"}`;
      serviceState.textContent = value.state === "offline" ? `Last known: ${deepWorkLabel(service.state)}` : deepWorkLabel(service.state);
      item.append(label, serviceState);
      services.append(item);
    });
  }
  renderPrivateKnowledge(value);

  const sessions = $("#deep-work-sessions");
  sessions.replaceChildren();
  if (!value.sessions.length) {
    const empty = document.createElement("p");
    empty.className = "work-empty";
    empty.textContent = value.state === "unavailable" ? "Session summaries are hidden while state integrity is unavailable." : "No Deep Work sessions are projected.";
    sessions.append(empty);
  } else {
    value.sessions.forEach((session, index) => {
      const card = document.createElement("article");
      card.className = "work-session-card";
      const heading = document.createElement("div");
      heading.className = "work-session-heading";
      const title = document.createElement("h4");
      title.textContent = `${session.current ? "Current " : ""}${deepWorkLabel(session.mode)} session ${index + 1}`;
      const badge = document.createElement("span");
      badge.className = `service-state ${["running", "verifying", "verified", "completed"].includes(session.state) ? "good" : ["authorized", "waiting-authority"].includes(session.state) ? "attention" : "danger"}`;
      badge.textContent = value.state === "offline" ? `Last known: ${deepWorkStateLabel(session.state)}` : deepWorkStateLabel(session.state);
      heading.append(title, badge);
      const progress = document.createElement("progress");
      progress.max = session.progress.criteriaTotal;
      progress.value = session.progress.criteriaPassing;
      progress.setAttribute("aria-label", `Passing criteria for ${deepWorkLabel(session.mode)} session ${index + 1}`);
      progress.setAttribute("aria-valuetext", `${formatCount(session.progress.criteriaPassing)} of ${formatCount(session.progress.criteriaTotal)} criteria passing`);
      const fields = document.createElement("dl");
      fields.className = "work-session-facts";
      appendWorkField(fields, "Criteria", `${formatCount(session.progress.criteriaPassing)} passing · ${formatCount(session.progress.criteriaFailing)} failing`);
      appendWorkField(fields, "Loop", `${formatCount(session.progress.iteration)} of ${formatCount(session.progress.maxIterations)}`);
      if (session.progress.failureStage) appendWorkField(fields, "Failure class", deepWorkLabel(session.progress.failureStage));
      appendWorkField(fields, "Runtime", formatWorkDuration(session.usage.runtimeSeconds));
      appendWorkField(fields, "Model requests", formatCount(session.usage.modelRequests));
      appendWorkField(fields, "Tokens", `${formatCount(session.usage.inputTokens)} input · ${formatCount(session.usage.outputTokens)} output`);
      appendWorkField(fields, "Artifacts", `${formatCount(session.artifacts.count)} · ${formatWorkBytes(session.artifacts.totalBytes)}`);
      appendWorkField(fields, "Verification", deepWorkLabel(session.verification));
      appendWorkField(fields, "Special tools", deepWorkCapabilityLabel(session.capability));
      appendWorkField(fields, "Boundary", deepWorkBoundaryLabel(session.boundaryState));
      const kinds = document.createElement("p");
      kinds.className = "work-artifact-kinds";
      kinds.textContent = session.artifacts.kinds.length ? `Artifact types: ${session.artifacts.kinds.map((item) => `${deepWorkLabel(item.kind)} (${formatCount(item.count)})`).join(", ")}` : "No artifacts recorded.";
      card.append(heading, progress, fields, kinds);
      if (session.activity.length) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = `Recent content-free activity (${formatCount(session.activity.length)})`;
        const activity = document.createElement("ol");
        activity.className = "work-activity-list";
        session.activity.forEach((event) => {
          const item = document.createElement("li");
          const copy = document.createElement("span");
          copy.textContent = deepWorkActivityLabel(event.summaryCode);
          const outcome = document.createElement("small");
          outcome.textContent = `${deepWorkLabel(event.category)} · ${deepWorkLabel(event.outcome)} · ${new Date(event.at).toLocaleString()}`;
          item.append(copy, outcome);
          activity.append(item);
        });
        details.append(summary, activity);
        card.append(details);
      }
      sessions.append(card);
    });
  }
}

function renderPrivateKnowledge(value) {
  const service = value.services.find((item) => item.id === "knowledge-vault");
  const stale = value.state === "offline";
  const presentation = !service ? ["Not connected", "neutral", "The encrypted local knowledge vault is not connected to this controller."] : {
    ready: ["Ready", "good", "Encrypted private knowledge is available to explicitly authorized local jobs."],
    busy: ["Working", "good", "The local knowledge vault is completing a bounded lifecycle operation."],
    disabled: ["Disabled", "neutral", "Private knowledge is installed but disabled by policy."],
    degraded: ["Needs attention", "attention", "Private knowledge has a service concern. Keep retrieval disabled and inspect the trusted host."],
    offline: ["Offline", "danger", "The knowledge vault service is offline. No private context should be injected."],
    unknown: ["Unverified", "danger", "The knowledge vault state cannot be verified safely. No private context should be injected."],
  }[service.state] || ["Unverified", "danger", "The knowledge vault state cannot be verified safely. No private context should be injected."];
  const state = $("#knowledge-state");
  state.className = `service-state ${stale ? "attention" : presentation[1]}`;
  state.textContent = stale ? `Last known: ${presentation[0]}` : presentation[0];
  setText("#knowledge-summary", stale ? "The controller update is stale. Treat the knowledge vault as unavailable until fresh local health returns." : presentation[2]);
}

function formatBudget(used, limit, remaining) {
  if (![used, limit, remaining].every(Number.isInteger)) return "Not available";
  return `${formatCount(used)} used · ${formatCount(remaining)} left · ${formatCount(limit)} limit`;
}

function formatDurationBudget(used, limit, remaining) {
  if (![used, limit, remaining].every(Number.isInteger)) return "Not available";
  return `${formatWorkDuration(used)} used · ${formatWorkDuration(remaining)} left · ${formatWorkDuration(limit)} limit`;
}

function formatByteBudget(used, limit, remaining) {
  if (![used, limit, remaining].every(Number.isInteger)) return "Not available";
  return `${formatWorkBytes(used)} used · ${formatWorkBytes(remaining)} left · ${formatWorkBytes(limit)} limit`;
}

function formatFrontierBudget(used, limit, remaining, state) {
  if (state === "configured" && Number.isInteger(limit)) return `${formatCount(limit)} configured limit · usage starts after activation`;
  return formatBudget(used, limit, remaining);
}

function formatWindow(seconds) {
  if (!Number.isInteger(seconds)) return "No active budget window";
  if (seconds % 86400 === 0) {
    const days = seconds / 86400;
    return `${days} ${days === 1 ? "day" : "days"} window`;
  }
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return `${hours} ${hours === 1 ? "hour" : "hours"} window`;
  }
  return `${formatCount(seconds)} second window`;
}

function dollars(micros) {
  return `$${(micros / 1000000).toFixed(4)}`;
}

function reviewField(list, label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  row.append(term, detail);
  list.append(row);
}

function renderFrontierReviews(value) {
  const list = $("#frontier-review-list");
  list.replaceChildren();
  if (!value.reviews.length) {
    const empty = document.createElement("p");
    empty.className = "review-empty";
    empty.textContent = "No pending Frontier reviews were found.";
    list.append(empty);
    return;
  }
  value.reviews.forEach((review) => {
    const card = document.createElement("article");
    card.className = "review-card";
    const title = document.createElement("h3");
    title.textContent = review.taskClass === "plan_review" ? "Plan review" : "Failure triage";
    const summary = document.createElement("dl");
    summary.className = "review-summary";
    const provider = review.providerAuthMode === "chatgpt" ? "ChatGPT subscription" :
      review.providerAuthMode === "api-key" ? "Usage-based API" : "Synthetic test";
    const cost = review.cost.mode === "metered" ? `${dollars(review.cost.estimatedAmountMicros)} maximum estimate` :
      review.cost.mode === "subscription" ? "Subscription route; no API estimate" : "Not priced locally";
    reviewField(summary, "Job", review.jobId);
    reviewField(summary, "Classification", review.classification);
    reviewField(summary, "Data categories", review.dataCategories.join(", "));
    reviewField(summary, "Provider route", provider);
    reviewField(summary, "Token ceiling", `${formatCount(review.estimatedInputTokens)} estimated input · ${formatCount(review.maxOutputTokens)} maximum output`);
    reviewField(summary, "Placeholders", formatCount(review.placeholderCount));
    reviewField(summary, "Cost", cost);
    reviewField(summary, "Plan hash", review.planHash);
    reviewField(summary, "Capsule hash", review.capsuleHash);
    const capsuleHeading = document.createElement("h4");
    capsuleHeading.textContent = "Exact sanitized capsule";
    const capsule = document.createElement("pre");
    const capsuleCode = document.createElement("code");
    capsuleCode.textContent = JSON.stringify(review.sanitizedCapsule, null, 2);
    capsule.append(capsuleCode);
    const terminalHeading = document.createElement("h4");
    terminalHeading.textContent = "Continue in a trusted terminal";
    const commands = document.createElement("pre");
    const commandCode = document.createElement("code");
    commandCode.textContent = `./pixel frontier-show ${review.jobId}\n./pixel frontier-approve ${review.jobId} ${review.planHash} --confirm`;
    commands.append(commandCode);
    const boundary = document.createElement("p");
    boundary.className = "review-boundary";
    boundary.textContent = "The broker rechecks the exact plan, capsule, policy, expiry, cancellation, and budget before any provider call.";
    card.append(title, summary, capsuleHeading, capsule, terminalHeading, commands, boundary);
    list.append(card);
  });
}

function semanticSection(card, heading, text) {
  if (text === null) return;
  const title = document.createElement("h4");
  title.textContent = heading;
  const copy = document.createElement("p");
  copy.className = "review-boundary";
  copy.textContent = text;
  card.append(title, copy);
}

function renderDeepWorkSemanticReview(value) {
  const list = $("#deep-work-review-content");
  list.replaceChildren();
  const state = $("#deep-work-review-state");
  state.className = "service-state attention";
  state.textContent = "Human judgment needed";

  const card = document.createElement("article");
  card.className = "review-card";
  const title = document.createElement("h3");
  title.textContent = value.content.title;
  const facts = document.createElement("dl");
  facts.className = "review-summary";
  reviewField(facts, "Work type", deepWorkLabel(value.profile));
  reviewField(facts, "Sensitivity", deepWorkLabel(value.dataClassification));
  reviewField(facts, "Verifier", deepWorkLabel(value.verification.method));
  reviewField(facts, "Checks", `${formatCount(value.verification.checkedItems)} passed · ${formatCount(value.verification.failedItems)} failed`);
  reviewField(facts, "Data movement", value.privacy.contentLeavesHost ? "External movement reported" : "Stays on this host");
  reviewField(facts, "Exact review", value.reviewSha256);
  card.append(title, facts);

  semanticSection(card, "Milestone objective", value.objective);
  const criteriaTitle = document.createElement("h4");
  criteriaTitle.textContent = "Accept only if every condition is truly satisfied";
  const criteria = document.createElement("ol");
  criteria.className = "semantic-criteria";
  value.acceptanceCriteria.forEach((criterion) => {
    const item = document.createElement("li");
    item.textContent = criterion;
    criteria.append(item);
  });
  card.append(criteriaTitle, criteria);
  semanticSection(card, "Summary", value.content.overview);
  semanticSection(card, "Method", value.content.methodology);

  const findingsTitle = document.createElement("h4");
  findingsTitle.textContent = "Candidate findings and retained evidence";
  const findings = document.createElement("ol");
  findings.className = "semantic-findings";
  value.content.findings.forEach((finding) => {
    const item = document.createElement("li");
    const statement = document.createElement("strong");
    statement.textContent = finding.statement;
    const evidence = document.createElement("ul");
    evidence.className = "semantic-evidence";
    finding.evidence.forEach((entry) => {
      const evidenceItem = document.createElement("li");
      const reference = document.createElement("code");
      reference.textContent = `${entry.reference} · ${formatWorkBytes(entry.bytes)}`;
      evidenceItem.append(reference);
      if (entry.excerpt !== null) {
        const excerpt = document.createElement("blockquote");
        excerpt.textContent = entry.excerpt;
        evidenceItem.append(excerpt);
      }
      evidence.append(evidenceItem);
    });
    item.append(statement, evidence);
    findings.append(item);
  });
  card.append(findingsTitle, findings);

  if (value.content.artifacts.length) {
    const artifactsTitle = document.createElement("h4");
    artifactsTitle.textContent = "Derived artifacts";
    const artifacts = document.createElement("ul");
    artifacts.className = "semantic-artifacts";
    value.content.artifacts.forEach((artifact) => {
      const item = document.createElement("li");
      item.textContent = `${artifact.path} · ${deepWorkLabel(artifact.kind)} · ${artifact.format.toUpperCase()} · ${formatWorkBytes(artifact.bytes)} · ${artifact.purpose}`;
      artifacts.append(item);
    });
    card.append(artifactsTitle, artifacts);
  }
  semanticSection(card, "Known limitations", value.content.limitations);
  semanticSection(card, "What Pixel actually verified", value.verification.explanation);
  const warning = document.createElement("p");
  warning.className = "review-boundary";
  warning.textContent = "The deterministic verifier did not establish semantic accuracy. Loading this review changed no goal state and granted no completion authority.";
  card.append(warning);
  list.append(card);
  renderWorkspaceEvidenceInspector(value);
}

function renderWorkspaceEvidenceInspector(value) {
  const target = $("#workspace-evidence-content");
  target.replaceChildren();
  const source = $("#deep-work-review-content .review-card");
  if (!source || !value) {
    setText("#workspace-evidence-message", "No exact private review is loaded.");
    return;
  }
  target.append(source.cloneNode(true));
  setText("#workspace-evidence-message", `${deepWorkLabel(value.profile)} review · ${formatCount(value.verification.checkedItems)} checked · ${value.privacy.contentLeavesHost ? "external movement reported" : "stays on this host"}.`);
}

function showWorkspaceInspectorView(view) {
  showControlCenter(false);
  showInspector(true);
  const focused = view !== "summary";
  $("#workspace-run-summary").hidden = focused;
  $("#workspace-approvals").hidden = focused;
  $("#workspace-permission-summary").hidden = view !== "summary";
  $("#workspace-permission-editor").hidden = view !== "permissions";
  $("#workspace-evidence-inspector").hidden = view !== "evidence";
  $("#open-control-center").hidden = focused;
  setText("#workspace-inspector-title", {
    summary: "Task context", permissions: "Permission defaults", evidence: "Evidence review",
  }[view] || "Task context");
}

function rememberWorkspaceInspectorFocus() {
  workspaceInspectorReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

function closeWorkspaceInspectorView() {
  showWorkspaceInspectorView("summary");
  const target = workspaceInspectorReturnFocus?.isConnected ? workspaceInspectorReturnFocus : $("#toggle-inspector");
  workspaceInspectorReturnFocus = null;
  target.focus({ preventScroll: true });
}

function openWorkspaceEvidenceInspector() {
  if (!deepWorkSemanticProjection) {
    notify("Load the exact retained evidence before opening its inspector.", "error");
    return;
  }
  rememberWorkspaceInspectorFocus();
  renderWorkspaceEvidenceInspector(deepWorkSemanticProjection);
  showWorkspaceInspectorView("evidence");
  $("#close-workspace-evidence").focus({ preventScroll: true });
  $("#workspace-evidence-inspector").scrollIntoView({ block: "start" });
}

async function loadDeepWorkSemanticReview() {
  const review = await api("/api/v1/reviews/deep-work", {
    headers: privateHeaders(),
  });
  deepWorkSemanticProjection = review;
  renderDeepWorkSemanticReview(review);
  setText("#deep-work-review-message", "Exact private candidate review loaded. No goal state changed.");
  if (chatProjection) renderChat(chatProjection);
  return review;
}

function clearDeepWorkSemanticReview() {
  deepWorkSemanticProjection = null;
  $("#deep-work-review-content").replaceChildren();
  const state = $("#deep-work-review-state");
  state.className = "service-state neutral";
  state.textContent = "Not loaded";
  $("#workspace-evidence-content").replaceChildren();
  setText("#workspace-evidence-message", "No exact private review is loaded.");
  if (!$("#workspace-evidence-inspector").hidden) showWorkspaceInspectorView("summary");
}

function shortIdentity(value) {
  return typeof value === "string" && /^[a-f0-9]{40,64}$/.test(value) ? `${value.slice(0, 12)}…` : "Not available";
}

function renderAttestation(value) {
  const state = value && typeof value === "object" ? value.state : "unavailable";
  const presentations = {
    verified: ["Verified", "good", "Exact source, installed files, configuration, profiles, connectors, and gateway checks match this fresh receipt."],
    limited: ["Limited", "review", "Integrity matches, but endpoint checks were explicitly skipped. Run the full trusted-host verification before relying on this deployment."],
    stale: ["Stale", "review", "The last exact verification is older than five minutes. Refresh the trusted-host verification before relying on current state."],
    mismatch: ["Mismatch", "danger", "Current files or configuration no longer match the last verification receipt. Treat this deployment as unverified."],
    unavailable: ["Unavailable", "danger", "Pixel cannot establish exact local deployment identity from a valid receipt."],
    "not-installed": ["Not installed", "neutral", "No active local release is available to attest."],
  };
  const presentation = presentations[state] || presentations.unavailable;
  const chip = $("#attestation-state");
  chip.className = `status-chip ${presentation[1]}`;
  chip.textContent = presentation[0];
  const reason = {
    "identity-mismatch": " The receipt or its bound release manifests changed.",
    "installed-drift": " Installed release bytes changed.",
    "source-drift": " Active runtime source changed.",
    "configuration-drift": " Generated or active OpenClaw configuration changed.",
    "receipt-unavailable": " The private receipt is missing or unreadable.",
    "receipt-invalid": " The private receipt is malformed or unsafe.",
    "source-identity-unavailable": " The release was not tied to a clean Git source identity.",
  }[value?.reasonCode] || "";
  setText("#attestation-summary", `${presentation[2]}${reason}`);
  setText("#attestation-observed", value?.verifiedAt ? `${new Date(value.verifiedAt).toLocaleString()} · ${value.ageSeconds ?? "?"}s old` : "No verified receipt");
  setText("#attestation-source", value?.source?.state === "git-clean" ? shortIdentity(value.source.commit) : "Source identity unavailable");
  setText("#attestation-tree", value?.source?.state === "git-clean" ? shortIdentity(value.source.tree) : "Source tree unavailable");
  const qualification = value?.qualification;
  setText("#attestation-qualification", qualification?.recordStatus ?
    `${qualification.recordStatus} · ${qualification.relationship} · ${qualification.qualifiedAt || "date unavailable"}` : "No matching qualified baseline");
  const runtime = value?.runtime;
  setText("#attestation-runtime", runtime?.state === "gateway-verified-model-unproven" ?
    `OpenClaw ${runtime.openclaw} · ${runtime.routeClass} route · endpoints ${runtime.endpointChecks}` : "Runtime proof unavailable");
  setText("#attestation-model", runtime?.modelIdSha256 ?
    `Hashed ID ${shortIdentity(runtime.modelIdSha256)} · capability unproven` : "Model identity unavailable");
  setText("#attestation-config", value?.configuration?.generatedDeploymentSha256 ?
    `Deployment ${shortIdentity(value.configuration.generatedDeploymentSha256)} · OpenClaw ${shortIdentity(value.configuration.activeOpenClawSha256)}` : "Configuration proof unavailable");
  const connectors = Array.isArray(value?.connectors) ? value.connectors : [];
  const verified = connectors.filter((item) => ["enabled-verified", "disabled-verified"].includes(item.state)).length;
  const enabled = connectors.filter((item) => item.state === "enabled-verified").length;
  setText("#attestation-connectors", verified === 6 ? `${enabled} enabled · all 6 states verified` : "Connector proof unavailable");
}

function renderStatus(value, doctor, updateStatus, recoveryGuide, deepWork) {
  const product = value.product;
  const attestation = value.attestation || { state: "unavailable", reasonCode: "receipt-unavailable" };
  renderAttestation(attestation);
  setText("#version-value", `Pixel ${product.version}`);
  setText("#install-value", product.installed ?
    (attestation.state === "verified" ? "Installed and exactly verified" : "Installed; proof needs review") :
    product.configured ? "Configured, not active here" : "Not configured yet");
  setText("#plan-value", product.planned ? "Ready to review" : "Not prepared");
  setText("#plan-hash", value.reviewPlan.sha256 ? `${value.reviewPlan.sha256.slice(0, 12)}…` : "No plan loaded");
  const diagnosticAttention = value.diagnostics.state === "unavailable" ? 1 :
    Number.isInteger(value.diagnostics.openIncidents) ? value.diagnostics.openIncidents : 0;
  const doctorAttention = Number.isInteger(doctor.summary.attentionChecks) && Number.isInteger(doctor.summary.unavailableChecks) ?
    doctor.summary.attentionChecks + doctor.summary.unavailableChecks : 1;
  const frontierAttention = ["configured", "unavailable"].includes(value.frontier.state) ? 1 : 0;
  const updateAttention = ["idle", "cleaned", "not-configured"].includes(updateStatus.state) ? 0 : 1;
  const recoveryAttention = value.diagnostics.state === "clear" && recoveryGuide.state === "attention" ? 1 : 0;
  const deepWorkAttention = ["unavailable", "offline"].includes(deepWork.state) ? 1 :
    deepWork.summary.attentionSessions + deepWork.summary.degradedServices;
  const attestationAttention = product.configured && attestation.state !== "verified" ? 1 : 0;
  const attention = Object.values(value.pending).reduce((sum, count) => sum + count, 0) + diagnosticAttention + doctorAttention + frontierAttention + updateAttention + recoveryAttention + deepWorkAttention + attestationAttention;
  setText("#attention-value", String(attention));
  setText("#savings-value", value.frontier.avoidedProviderCalls ?? "—");
  setText("#pending-source", value.pending.sourceChanges);
  setText("#pending-ops", value.pending.operationsApprovals);
  setText("#pending-frontier", value.pending.frontierApprovals);
  const frontier = value.frontier;
  latestFrontierStatus = frontier;
  const providerLabel = frontier.authMode === "chatgpt" ? "ChatGPT plan access" :
    frontier.authMode === "api-key" ? "OpenAI API" : frontier.authMode === "mock" ? "Synthetic test" : "Not available";
  const stateLabel = {
    disabled: "Off — no provider route is active",
    configured: "Prepared — external provider setup is still required",
    active: "Active — broker counters are available",
    unavailable: "Unavailable — verify private Frontier state",
  }[frontier.state] || "Unavailable — verify private Frontier state";
  const setupLabel = {
    disabled: "Not required", "external-required": "Complete setup in a trusted terminal",
    "broker-prepared": "Broker prepared; access is rechecked before calls", unavailable: "Could not verify",
  }[frontier.providerSetup] || "Could not verify";
  const billingLabel = {
    none: "None", "chatgpt-plan": "Eligible ChatGPT plan usage — not API billing",
    "platform-api": "Separately billed through the API Platform", synthetic: "Synthetic test only",
    unavailable: "Could not verify",
  }[frontier.billingBoundary] || "Could not verify";
  const budgetSourceLabel = {
    none: "No active budget", "generated-policy": "Generated policy limits; no usage counters yet",
    "active-broker": "Live broker aggregate", unavailable: "Could not verify safely",
  }[frontier.budgetSource] || "Could not verify safely";
  setText("#frontier-readiness", stateLabel);
  setText("#frontier-provider", providerLabel);
  setText("#frontier-setup", setupLabel);
  setText("#frontier-billing", billingLabel);
  setText("#frontier-budget-source", budgetSourceLabel);
  setText("#frontier-window", formatWindow(frontier.windowSeconds));
  setText("#frontier-job-budget", formatFrontierBudget(frontier.used.jobs, frontier.limits.jobs, frontier.remaining.jobs, frontier.state));
  setText("#frontier-input-budget", formatFrontierBudget(frontier.used.inputTokens, frontier.limits.inputTokens, frontier.remaining.inputTokens, frontier.state));
  setText("#frontier-output-budget", formatFrontierBudget(frontier.used.outputTokens, frontier.limits.outputTokens, frontier.remaining.outputTokens, frontier.state));
  setText("#frontier-failure-budget", formatFrontierBudget(frontier.used.failures, frontier.limits.failures, frontier.remaining.failures, frontier.state));
  let costLabel = "Not priced locally";
  if (frontier.costMode === "subscription") costLabel = "ChatGPT plan route; not an API Platform estimate";
  if (frontier.authMode === "api-key" && frontier.costMode === "unavailable") costLabel = "API billing; no verified dollar estimate";
  if (frontier.costMode === "metered" && Number.isInteger(frontier.used.estimatedCostMicros)) {
    costLabel = `${dollars(frontier.used.estimatedCostMicros)} estimated`;
    if (Number.isInteger(frontier.limits.estimatedCostMicros) && Number.isInteger(frontier.remaining.estimatedCostMicros)) {
      costLabel = `${dollars(frontier.used.estimatedCostMicros)} used · ${dollars(frontier.remaining.estimatedCostMicros)} left · ${dollars(frontier.limits.estimatedCostMicros)} limit`;
    }
  }
  if (frontier.state === "unavailable") costLabel = "Could not verify safely";
  setText("#frontier-cost-budget", costLabel);
  operatorActionAvailability = value.operatorActions || {};
  document.querySelectorAll("[data-policy]").forEach((button) => {
    button.disabled = operatorActionAvailability[button.dataset.policy] !== true;
  });
  const pauseAvailable = operatorActionAvailability.operationsPause === true || operatorActionAvailability.frontierPause === true;
  $("#pause-reason").disabled = !pauseAvailable;
  reviewViewAvailability = value.reviewViews || {};
  const reviewButton = $("#load-frontier-reviews");
  reviewButton.disabled = reviewViewAvailability.frontierReviews !== true || !privateWorkspaceAuthorized();
  if (reviewButton.disabled) {
    $("#frontier-review-list").replaceChildren();
    setText("#frontier-review-message", reviewViewAvailability.frontierReviews === true ?
      "Open the exact URL printed by ./pixel ui to unlock this process-lifetime view." :
      "Disabled in the private control policy.");
  } else if (!$("#frontier-review-list").children.length) {
    setText("#frontier-review-message", "Available on demand; capsule content has not been loaded.");
  }
  const workReviewButton = $("#load-deep-work-review");
  workReviewButton.disabled = reviewViewAvailability.deepWorkSemanticReviews !== true || !privateWorkspaceAuthorized();
  if (workReviewButton.disabled) {
    $("#deep-work-review-content").replaceChildren();
    const state = $("#deep-work-review-state");
    state.className = "service-state neutral";
    state.textContent = "Not loaded";
    setText("#deep-work-review-message", reviewViewAvailability.deepWorkSemanticReviews === true ?
      "Open the exact URL printed by ./pixel ui to unlock this process-lifetime private view." :
      "Disabled in the private control policy.");
  } else if (!$("#deep-work-review-content").children.length) {
    setText("#deep-work-review-message", "Available on demand when a safe candidate is waiting for semantic judgment.");
  }

  const overall = $("#overall-state");
  overall.className = "status-chip";
  if (product.installed && attestation.state === "verified") {
    overall.textContent = "Active";
    overall.classList.add("good");
  } else if (product.installed) {
    overall.textContent = "Review proof";
    overall.classList.add(["mismatch", "unavailable"].includes(attestation.state) ? "danger" : "review");
  } else if (product.planned) {
    overall.textContent = "Plan ready";
    overall.classList.add("review");
  } else if (product.configured) {
    overall.textContent = "In progress";
    overall.classList.add("review");
  } else {
    overall.textContent = "Start setup";
    overall.classList.add("neutral");
  }
  const steps = [...document.querySelectorAll("#readiness-steps li")];
  steps.forEach((step) => step.classList.remove("done", "current"));
  if (product.configured) steps[0].classList.add("done"); else steps[0].classList.add("current");
  if (product.configured) steps[1].classList.add(product.planned ? "done" : "current");
  if (product.planned) steps[2].classList.add(product.installed ? "done" : "current");
  if (product.installed) steps[3].classList.add("done");

  const limbList = $("#limb-status");
  limbList.replaceChildren();
  value.limbs.forEach((limb) => {
    const item = document.createElement("li");
    const state = document.createElement("span");
    state.className = `boundary-state ${limb.enabled ? "enabled" : "disabled"}`;
    state.textContent = limb.enabled ? "On" : "Off";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = limb.label;
    const detail = document.createElement("small");
    detail.textContent = limb.boundary;
    copy.append(title, detail);
    item.append(copy, state);
    limbList.append(item);
  });

  const activity = $("#recent-actions");
  activity.replaceChildren();
  if (!value.recentActions.length) {
    const item = document.createElement("li");
    item.textContent = "No control actions recorded yet.";
    activity.append(item);
  } else {
    value.recentActions.forEach((entry) => {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = actionLabel(entry.kind);
      const detail = document.createElement("small");
      const finished = entry.finishedAt ? new Date(entry.finishedAt).toLocaleString() : "Interrupted";
      detail.textContent = `${entry.status} · ${finished}`;
      item.append(title, detail);
      activity.append(item);
    });
  }
}

function fillForm(value) {
  const settings = value.settings;
  onboardingRevision = value.revision;
  for (const [key, child] of Object.entries(settings)) {
    if (["schemaVersion", "limbs", "calendarDirectEnabled", "modelPrivateHosts"].includes(key)) continue;
    if (form.elements.namedItem(key)) form.elements.namedItem(key).value = child;
  }
  form.elements.modelReasoning.checked = settings.modelReasoning;
  form.elements.modelPrivateHosts.value = settings.modelPrivateHosts.join(", ");
  Object.entries(settings.limbs).forEach(([name, enabled]) => {
    form.elements.namedItem(`limb-${name}`).checked = enabled;
  });
  form.elements.calendarDirectEnabled.checked = settings.calendarDirectEnabled;
  syncCalendarControl();
  syncFrontierControls();
  syncFrontierBudgetEditor(true);
  const state = $("#save-state");
  state.textContent = value.advancedSettingsPreserved ? "Saved locally" : "New setup";
  state.className = `status-chip ${value.advancedSettingsPreserved ? "good" : "neutral"}`;
}

function settingsFromForm() {
  const data = new FormData(form);
  const limb = (name) => form.elements.namedItem(`limb-${name}`).checked;
  const contextWindow = Number(data.get("modelContextWindow"));
  const maximumTokens = Number(data.get("modelMaxTokens"));
  if (maximumTokens > contextWindow) throw new Error("Maximum answer tokens cannot exceed the context window.");
  const privateHosts = String(data.get("modelPrivateHosts") || "").split(",").map((item) => item.trim()).filter(Boolean);
  if (new Set(privateHosts.map((host) => host.toLowerCase())).size !== privateHosts.length) {
    throw new Error("Additional private model hosts cannot contain duplicates.");
  }
  return {
    schemaVersion: 1,
    deploymentProfile: data.get("deploymentProfile"),
    capabilityProfile: data.get("capabilityProfile"),
    ownerName: data.get("ownerName"),
    organization: data.get("organization"),
    deploymentName: data.get("deploymentName"),
    timeZone: data.get("timeZone"),
    agentName: data.get("agentName"),
    modelProvider: data.get("modelProvider"),
    modelId: data.get("modelId"),
    modelName: data.get("modelName"),
    modelBaseUrl: data.get("modelBaseUrl"),
    modelPrivateHosts: privateHosts,
    modelReasoning: form.elements.modelReasoning.checked,
    modelContextWindow: contextWindow,
    modelMaxTokens: maximumTokens,
    searxngBaseUrl: data.get("searxngBaseUrl"),
    embeddingModel: data.get("embeddingModel"),
    googleAccount: data.get("googleAccount"),
    calendarId: data.get("calendarId"),
    gatewayPort: Number(data.get("gatewayPort")),
    limbs: { email: limb("email"), calendar: limb("calendar"), social: limb("social"), web: limb("web"), operations: limb("operations"), frontier: limb("frontier") },
    calendarDirectEnabled: form.elements.calendarDirectEnabled.checked,
    frontierAuthMode: form.elements.frontierAuthMode.value,
    frontierBudgetProfile: form.elements.frontierBudgetProfile.value,
  };
}

function syncCalendarControl() {
  const enabled = form.elements.namedItem("limb-calendar").checked;
  form.elements.calendarDirectEnabled.disabled = !enabled;
  if (!enabled) form.elements.calendarDirectEnabled.checked = false;
  $("#calendar-direct-row").classList.toggle("disabled", !enabled);
}

function setFrontierGuidance(title, detail) {
  const guidance = $("#frontier-auth-guidance");
  const strong = document.createElement("strong");
  strong.textContent = title;
  guidance.replaceChildren(strong, document.createTextNode(` ${detail}`));
}

function setBudgetInput(name, value) {
  const input = $("#frontier-budget-form").elements[name];
  if (Number.isInteger(value)) input.value = String(value);
}

function syncFrontierBudgetEditor(prefill = false) {
  const enabled = form.elements.namedItem("limb-frontier").checked &&
    form.elements.frontierBudgetProfile.value === "custom";
  const authMode = form.elements.frontierAuthMode.value;
  $("#frontier-budget-proposal").hidden = true;
  setText("#budget-editor-message", "");
  $("#frontier-budget-fields").disabled = !enabled;
  $("#frontier-cost-limit-row").hidden = authMode !== "api-key";
  $("#frontier-budget-form").elements.maxEstimatedCostUsd.required = enabled && authMode === "api-key";
  const state = $("#budget-editor-state");
  state.className = `status-chip ${enabled ? "review" : "neutral"}`;
  state.textContent = enabled ? "Draft available" : "Unavailable";
  setText("#budget-editor-guidance", enabled ?
    "Choose exact limits. Creating a proposal does not apply, configure, activate, or spend anything." :
    "Enable Frontier with a private custom policy before drafting limits here.");
  if (!enabled) {
    $("#frontier-budget-proposal").hidden = true;
    setText("#budget-editor-message", "");
    return;
  }
  if (prefill && latestFrontierStatus && ["configured", "active"].includes(latestFrontierStatus.state)) {
    setBudgetInput("windowMinutes", Number.isInteger(latestFrontierStatus.windowSeconds) && latestFrontierStatus.windowSeconds % 60 === 0 ? latestFrontierStatus.windowSeconds / 60 : null);
    setBudgetInput("maxJobs", latestFrontierStatus.limits.jobs);
    setBudgetInput("maxInputTokens", latestFrontierStatus.limits.inputTokens);
    setBudgetInput("maxOutputTokens", latestFrontierStatus.limits.outputTokens);
    setBudgetInput("maxFailures", latestFrontierStatus.limits.failures);
    if (authMode === "api-key" && Number.isInteger(latestFrontierStatus.limits.estimatedCostMicros)) {
      $("#frontier-budget-form").elements.maxEstimatedCostUsd.value = (latestFrontierStatus.limits.estimatedCostMicros / 1000000).toFixed(6).replace(/\.?0+$/, "");
    }
  }
}

function syncFrontierControls() {
  const enabled = form.elements.namedItem("limb-frontier").checked;
  const mode = form.elements.frontierAuthMode.value;
  const budget = form.elements.frontierBudgetProfile.value;
  for (const name of ["frontierAuthMode", "frontierBudgetProfile"]) {
    form.elements[name].disabled = !enabled;
  }
  $("#frontier-setup-row").classList.toggle("disabled", !enabled);
  if (!enabled) {
    setFrontierGuidance("Frontier is off.", "No cloud-review authentication or budget is active.");
  } else if (budget === "custom") {
    setFrontierGuidance("Custom limits are terminal managed.", "Pixel will preserve the private policy and reject a managed preset override. Provider access is still completed outside this page.");
  } else if (mode === "chatgpt") {
    setFrontierGuidance("No credential field by design.", "ChatGPT sign-in is completed outside this page and uses eligible plan access. Pixel's own request and token limits still apply.");
  } else {
    setFrontierGuidance("Separately billed API usage.", "The API key is provisioned outside this page. Pixel limits requests and tokens, but cannot claim a dollar cap without a verified private pricing policy.");
  }
  syncFrontierBudgetEditor();
}

async function refresh() {
  clearDeepWorkSemanticReview();
  const chatRequest = privateWorkspaceAuthorized() ? api("/api/v1/chat", { headers: privateHeaders() }) : Promise.resolve({
    schemaVersion: 1, state: "disabled", activeHandle: null, conversations: [],
  });
  const authoringRequest = privateWorkspaceAuthorized() ? api("/api/v1/deep-work/authoring", { headers: privateHeaders() }) : Promise.resolve({
    schemaVersion: 1, state: "disabled", revision: null, profiles: [], inputs: [],
    limits: { maxMilestones: 16, maxCriteriaPerMilestone: 8, draftsUsed: 0, maxDrafts: 1 },
  });
  const draftReviewsRequest = privateWorkspaceAuthorized() ? api("/api/v1/deep-work/drafts", { headers: privateHeaders() }) : Promise.resolve({
    schemaVersion: 1, state: "disabled", revision: null, drafts: [], launch: { state: "disabled", preparedUsed: 0, maxPrepared: 0 },
    service: { state: "disabled", renderedUsed: 0, maxRendered: 0 },
  });
  const approvalsRequest = privateWorkspaceAuthorized() ? api("/api/v1/approvals", { headers: privateHeaders() }) : Promise.resolve({
    schemaVersion: 1, state: "disabled", approvals: [],
  });
  const permissionsRequest = privateWorkspaceAuthorized() ? api("/api/v1/permissions", { headers: privateHeaders() }) : Promise.resolve({
    schemaVersion: 1, state: "disabled", settings: [],
  });
  const [status, onboarding, doctor, diagnostics, updateStatus, recoveryGuide, deepWork, authoring, draftReviews, chat, approvals, permissions] = await Promise.all([
    api("/api/v1/status"), api("/api/v1/onboarding"), api("/api/v1/doctor"), api("/api/v1/diagnostics"), api("/api/v1/update-status"), api("/api/v1/recovery-guide"), api("/api/v1/deep-work"),
    authoringRequest, draftReviewsRequest, chatRequest, approvalsRequest, permissionsRequest,
  ]);
  renderStatus({ ...status, diagnostics: diagnostics.summary }, doctor, updateStatus, recoveryGuide, deepWork);
  renderDoctor(doctor);
  renderDiagnostics(diagnostics);
  renderUpdateStatus(updateStatus);
  renderRecoveryGuide(recoveryGuide);
  deepWorkProjection = deepWork;
  renderDeepWork(deepWork);
  renderWorkAuthoring(authoring);
  renderWorkDraftReviews(draftReviews);
  renderApprovals(approvals);
  renderPermissions(permissions);
  renderChat(chat);
  renderWorkspaceSummary(status, deepWork, approvals);
  fillForm(onboarding);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("#form-message");
  message.textContent = "Saving privately…";
  try {
    await ensureApprovalSession();
    const saved = await api(approvalRoute("/approve/onboarding", "/api/v1/onboarding"), {
      method: "POST",
      body: JSON.stringify({ schemaVersion: 1, revision: onboardingRevision, settings: settingsFromForm() }),
    });
    fillForm(saved);
    message.textContent = "Saved on this computer.";
    notify("Local onboarding settings saved.");
  } catch (error) {
    message.textContent = error.message;
    notify(error.message, "error");
  }
});

form.elements.namedItem("limb-calendar").addEventListener("change", syncCalendarControl);
form.elements.namedItem("limb-frontier").addEventListener("change", syncFrontierControls);
form.elements.frontierAuthMode.addEventListener("change", syncFrontierControls);
form.elements.frontierBudgetProfile.addEventListener("change", syncFrontierControls);
form.elements.capabilityProfile.addEventListener("change", () => {
  const defaults = profileLimbs[form.elements.capabilityProfile.value];
  if (!defaults) return;
  Object.entries(defaults).forEach(([name, enabled]) => {
    form.elements.namedItem(`limb-${name}`).checked = enabled;
  });
  syncCalendarControl();
  syncFrontierControls();
  notify("Capability starting point applied. Review each capability before saving.");
});

function exactInteger(value, label, minimum, maximum) {
  if (!/^(?:0|[1-9][0-9]*)$/.test(value)) throw new Error(`${label} must be a whole number.`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${label} is outside the safe editor range.`);
  }
  return parsed;
}

function usdToMicros(value) {
  if (!/^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,6})?$/.test(value)) {
    throw new Error("Maximum estimated API cost must be USD from 0 through 1000 with no more than six decimals.");
  }
  const [whole, fraction = ""] = value.split(".");
  const micros = Number(BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, "0")));
  if (!Number.isSafeInteger(micros) || micros > 1000000000) {
    throw new Error("Maximum estimated API cost cannot exceed $1000.");
  }
  return micros;
}

function budgetDisplay(field, value) {
  if (field === "windowSeconds") return `${formatCount(value / 60)} minutes`;
  if (field === "maxEstimatedCostMicros") return value === null ? "Not applicable" : dollars(value);
  return formatCount(value);
}

function renderBudgetProposal(value) {
  const labels = {
    windowSeconds: "Window", maxJobs: "Reviews", maxInputTokens: "Input tokens",
    maxOutputTokens: "Output tokens", maxFailures: "Failed reviews",
    maxEstimatedCostMicros: "Estimated API cost",
  };
  const changes = $("#budget-proposal-changes");
  changes.replaceChildren();
  Object.entries(labels).forEach(([field, label]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = `${budgetDisplay(field, value.currentBudgets[field])} → ${budgetDisplay(field, value.proposedBudgets[field])} (${value.directions[field]})`;
    row.append(term, description);
    changes.append(row);
  });
  setText("#budget-proposal-summary", `${value.increasesPotentialSpend ? "This change can increase potential usage or spend." : "This change only narrows or redistributes the current limits."} It expires ${new Date(value.expiresAt).toLocaleString()}.`);
  setText("#budget-proposal-command", `./pixel frontier-budget apply --proposal-id ${value.proposalId} --proposal-hash ${value.proposalHash} --confirm`);
  $("#frontier-budget-proposal").hidden = false;
  const state = $("#budget-editor-state");
  state.textContent = "Proposal ready";
  state.className = "status-chip review";
}

$("#frontier-budget-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const editor = event.currentTarget;
  const message = $("#budget-editor-message");
  const button = $("#create-budget-proposal");
  button.disabled = true;
  message.textContent = "Creating a private exact proposal…";
  try {
    const windowMinutes = exactInteger(editor.elements.windowMinutes.value, "Budget window", 1, 44640);
    const request = {
      schemaVersion: 1,
      windowSeconds: windowMinutes * 60,
      maxJobs: exactInteger(editor.elements.maxJobs.value, "Maximum reviews", 1, 1000),
      maxInputTokens: exactInteger(editor.elements.maxInputTokens.value, "Maximum input tokens", 128, 10000000),
      maxOutputTokens: exactInteger(editor.elements.maxOutputTokens.value, "Maximum output tokens", 64, 2000000),
      maxFailures: exactInteger(editor.elements.maxFailures.value, "Maximum failed reviews", 1, 100),
      maxEstimatedCostMicros: form.elements.frontierAuthMode.value === "api-key" ?
        usdToMicros(editor.elements.maxEstimatedCostUsd.value) : null,
    };
    if (request.maxOutputTokens > request.maxInputTokens) throw new Error("Maximum output tokens cannot exceed maximum input tokens.");
    if (request.maxFailures > request.maxJobs) throw new Error("Maximum failed reviews cannot exceed maximum reviews.");
    const proposal = await api("/api/v1/frontier-budget/preview", {
      method: "POST", body: JSON.stringify(request),
    });
    renderBudgetProposal(proposal);
    message.textContent = "Proposal stored privately. Nothing was applied or sent.";
    notify("Exact Frontier budget proposal created; terminal confirmation is still required.");
  } catch (error) {
    $("#frontier-budget-proposal").hidden = true;
    message.textContent = error.message;
    notify(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

$("#frontier-budget-form").addEventListener("input", () => {
  if (!$("#frontier-budget-proposal").hidden) {
    $("#frontier-budget-proposal").hidden = true;
    setText("#budget-editor-message", "Limits changed; create a new exact proposal before using a terminal command.");
    const state = $("#budget-editor-state");
    state.textContent = "Draft available";
    state.className = "status-chip review";
  }
});

$("#toggle-inspector").addEventListener("click", () => showInspector($("#workspace-inspector").hidden));
$("#close-inspector").addEventListener("click", () => showInspector(false));
$("#open-control-center").addEventListener("click", () => showControlCenter(true));
$("#open-settings").addEventListener("click", () => showControlCenter(true));
$("#close-control-center").addEventListener("click", () => showControlCenter(false));
function openPermissionSettings() {
  rememberWorkspaceInspectorFocus();
  showWorkspaceInspectorView("permissions");
  ($("#workspace-permission-list select:not(:disabled)") || $("#close-workspace-permissions")).focus({ preventScroll: true });
  $("#workspace-permission-editor").scrollIntoView({ block: "start" });
}
$("#open-permission-settings").addEventListener("click", openPermissionSettings);
$("#edit-permissions").addEventListener("click", openPermissionSettings);
$("#close-workspace-permissions").addEventListener("click", closeWorkspaceInspectorView);
$("#close-workspace-evidence").addEventListener("click", closeWorkspaceInspectorView);
$("#close-chat-inline-workbench").addEventListener("click", () => {
  closeInlineDeepWorkPlanner();
  $("#chat-message").focus({ preventScroll: true });
});
$("#open-approvals").addEventListener("click", () => {
  showControlCenter(false);
  const first = $(".chat-approval-card");
  if (first) first.scrollIntoView({ block: "center" });
  else {
    showInspector(true);
    $("#workspace-approvals").scrollIntoView({ block: "start" });
  }
});

async function savePermissionDefaults(form, dataAttribute, button, messageSelector) {
  button.disabled = true;
  setText(messageSelector, reviewToken ? "Saving exact local permission defaults…" : "Verify the protected approval identity to save these defaults…");
  try {
    if (permissionProjection?.state !== "ready" || !Array.isArray(permissionProjection.settings)) throw new Error("Permission defaults are not available for an exact save.");
    const modes = {};
    permissionProjection.settings.forEach((setting) => {
      const select = form.querySelector(`[data-${dataAttribute}="${setting.kind}"]`);
      if (!select || !setting.availableModes.includes(select.value)) throw new Error("A permission selection is no longer valid. Reload Pixel and try again.");
      modes[setting.kind] = select.value;
    });
    await ensureApprovalSession();
    await api(approvalRoute("/approve/permissions", "/api/v1/permissions"), {
      method: "POST",
      headers: privateHeaders(),
      body: JSON.stringify({ schemaVersion: 1, revision: permissionProjection.revision, modes }),
    });
    await refresh();
    setText(messageSelector, "Protected permission defaults saved. Every older pending proposal was denied.");
    notify("Permission defaults saved; stale proposals were denied.");
  } catch (error) {
    setText(messageSelector, error.message);
    notify(error.message, "error");
  } finally {
    button.disabled = permissionProjection?.state !== "ready" || !privateWorkspaceAuthorized();
  }
}

$("#permission-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await savePermissionDefaults(event.currentTarget, "permission-kind", $("#save-permissions"), "#permission-settings-message");
});

$("#workspace-permission-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await savePermissionDefaults(event.currentTarget, "workspace-permission-kind", $("#save-workspace-permissions"), "#workspace-permission-message");
});

const narrowWorkspace = window.matchMedia("(max-width: 1180px)");
if (narrowWorkspace.matches) showInspector(false);
narrowWorkspace.addEventListener("change", (event) => {
  if (event.matches) showInspector(false);
});

$("#new-conversation").addEventListener("click", () => {
  closeInlineDeepWorkPlanner({ clearLink: true, resetDraft: true });
  activeConversationHandle = null;
  chatDraftingNew = true;
  if (chatProjection) renderChat({ ...chatProjection, activeHandle: null });
  $("#chat-message").focus();
});

$("#chat-conversation-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-conversation-handle]");
  if (!button || !chatProjection) return;
  closeInlineDeepWorkPlanner({ clearLink: true, resetDraft: true });
  activeConversationHandle = button.dataset.conversationHandle;
  chatDraftingNew = false;
  renderChat({ ...chatProjection, activeHandle: null });
});

$("#chat-thread").addEventListener("click", async (event) => {
  const starter = event.target.closest("[data-starter]");
  if (starter) {
    $("#chat-message").value = starter.dataset.starter;
    $("#chat-message").focus();
    return;
  }
  const planner = event.target.closest("[data-plan-deep-work]");
  if (planner) {
    const taskHandle = planner.dataset.planDeepWork;
    const source = (chatProjection?.conversations || []).flatMap((conversation) => conversation.turns || [])
      .find((turn) => turn.taskHandle === taskHandle);
    if (!source || source.state === "running" || workAuthoring?.state !== "ready") {
      notify("That chat task or Deep Work authoring state changed. Refresh Pixel before continuing.", "error");
      return;
    }
    try {
      openInlineDeepWorkPlanner(taskHandle, source);
    } catch (error) {
      notify(error.message, "error");
    }
    return;
  }
  const exactDraft = event.target.closest("[data-open-handoff-draft]");
  if (exactDraft) {
    const draftHandle = exactDraft.dataset.openHandoffDraft;
    if (!/^workdraftview-[a-f0-9]{24}$/u.test(draftHandle)) {
      notify("The exact Deep Work draft selection is invalid. Refresh Pixel before continuing.", "error");
      return;
    }
    showControlCenter(true);
    window.requestAnimationFrame(() => {
      const card = document.querySelector(`[data-draft-handle="${draftHandle}"]`);
      if (card) card.scrollIntoView({ block: "center" });
      else {
        $("#deep-work-drafts").scrollIntoView({ block: "start" });
        notify("The provenance receipt is retained, but its exact draft is not in the current review projection.", "error");
      }
    });
    return;
  }
  const lifecycle = event.target.closest("[data-chat-lifecycle-action]");
  if (lifecycle) {
    const kind = lifecycle.dataset.chatLifecycleAction;
    if (!["deep-work-pause", "deep-work-resume", "deep-work-cancel"].includes(kind)) {
      notify("That lifecycle action is invalid. Refresh Pixel before continuing.", "error");
      return;
    }
    lifecycle.disabled = true;
    setText("#chat-message-status", "Pixel is rechecking the exact current goal state and permission boundary…");
    try {
      const response = await requestFixedAction({ schemaVersion: 1, kind });
      if (response.state === "approval-required") setText("#chat-message-status", "The exact lifecycle proposal is waiting inline. No goal state changed.");
    } catch (error) {
      lifecycle.disabled = false;
      setText("#chat-message-status", error.message);
      notify(error.message, "error");
    }
    return;
  }
  const approve = event.target.closest("[data-approve-approval]");
  const deny = event.target.closest("[data-deny-approval]");
  const actionId = approve?.dataset.approveApproval || deny?.dataset.denyApproval;
  if (!actionId) return;
  const preview = approvalProjection?.approvals?.find((candidate) => candidate.actionId === actionId);
  if (!preview) {
    notify("This exact approval is no longer pending. Refresh Pixel before continuing.", "error");
    return;
  }
  const card = event.target.closest(".chat-approval-card");
  const message = card?.querySelector(".approval-message");
  const buttons = card?.querySelectorAll("button") || [];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    if (approve) {
      if (message) message.textContent = reviewToken ? "Running the exact approved local action…" : "Verify the separate approval session to continue…";
      await ensureApprovalSession();
      if (message) message.textContent = "Running the exact approved local action…";
      const result = await api(approvalRoute("/approve/execute", "/api/v1/actions/execute"), {
        method: "POST",
        body: JSON.stringify({ schemaVersion: 1, actionId: preview.actionId, actionHash: preview.actionHash }),
      });
      recordActionCompletion(preview.kind, result);
      notify(result.message, result.status === "succeeded" ? "success" : "error");
    } else {
      if (message) message.textContent = "Denying this exact proposal…";
      await api("/api/v1/actions/cancel", {
        method: "POST", headers: privateHeaders(),
        body: JSON.stringify({ schemaVersion: 1, actionId: preview.actionId, actionHash: preview.actionHash }),
      });
      notify("Exact proposal denied. Nothing ran.");
    }
    await refresh();
  } catch (error) {
    if (message) message.textContent = error.message;
    buttons.forEach((button) => { button.disabled = false; });
    notify(error.message, "error");
  }
});

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (chatRunning) return;
  const message = $("#chat-message").value.trim();
  if (!message) return;
  if (!privateWorkspaceAuthorized() || chatProjection?.state !== "ready") {
    notify("Pixel chat is not connected through an authenticated private workspace.", "error");
    return;
  }
  chatRunning = true;
  $("#send-message").disabled = true;
  setWorkspaceRunState("Working", "running");
  setText("#chat-message-status", "Pixel is working. You can continue typing while this turn runs.");
  const thread = $("#chat-thread");
  if (!activeConversationHandle) {
    thread.replaceChildren();
    $("#workspace-title").textContent = message.split("\n", 1)[0];
  }
  thread.append(chatMessage("user", message), chatRunEvent({
    state: "running", phase: "submitting", taskHandle: null, activity: [],
  }));
  thread.scrollTop = thread.scrollHeight;
  try {
    const result = await api("/api/v1/chat/tasks", {
      method: "POST",
      headers: privateHeaders(),
      body: JSON.stringify({
        schemaVersion: 1, requestId: chatRequestId(),
        conversationHandle: activeConversationHandle, message,
      }),
    });
    $("#chat-message").value = "";
    activeConversationHandle = result.activeHandle;
    renderChat(result);
  } catch (error) {
    notify(error.message, "error");
    await refresh().catch(() => {});
  } finally {
    if (chatProjection) renderChat(chatProjection);
    $("#chat-message").focus();
  }
});

function showActionConfirmation(preview) {
  const existing = approvalProjection?.state === "ready" && Array.isArray(approvalProjection.approvals) ? approvalProjection.approvals : [];
  approvalProjection = {
    ...(approvalProjection || { schemaVersion: 1, state: "ready" }),
    state: "ready",
    approvals: [...existing.filter((candidate) => candidate.actionId !== preview.actionId), preview],
  };
  updateApprovalSummary(approvalProjection);
  if (chatProjection) renderChat(chatProjection);
  showControlCenter(false);
  window.requestAnimationFrame(() => {
    const card = document.querySelector(`[data-approval-id="${preview.actionId}"]`);
    if (card) card.scrollIntoView({ block: "center" });
  });
  notify("Exact action is waiting inline. Review its effect, then approve once or deny.");
}

async function requestFixedAction(request) {
  const response = await api("/api/v1/actions/request", {
    method: "POST",
    headers: privateHeaders(),
    body: JSON.stringify(request),
  });
  if (response?.schemaVersion !== 1) throw new Error("Pixel returned an invalid fixed-action decision.");
  if (response.state === "approval-required" && response.preview) {
    showActionConfirmation(response.preview);
    return response;
  }
  if (response.state === "completed" && response.result) {
    recordActionCompletion(request.kind, response.result);
    notify(response.result.message, response.result.status === "succeeded" ? "success" : "error");
    await refresh();
    return response;
  }
  throw new Error("Pixel returned an invalid fixed-action decision.");
}

function recordActionCompletion(kind, result) {
  if (kind === "deep-work-draft" && result.status === "succeeded") {
    setText("#deep-work-draft-message", workHandoffTaskHandle
      ? "Private inert draft created and bound to the selected chat task by an immutable receipt. Nothing was compiled, staged, scheduled, or run."
      : "Private inert draft created. Its exact retained work graph is now visible below; nothing was compiled, staged, scheduled, or run.");
    workHandoffTaskHandle = null;
    $("#deep-work-handoff-note").hidden = true;
  } else if (kind === "deep-work-prepare" && result.status === "succeeded") {
    setText("#deep-work-drafts-message", "Exact launch package prepared with expiring single-use leases. It remains inactive: no ready goal state, schedule, service, worker, or provider call exists.");
  } else if (kind === "deep-work-stage" && result.status === "succeeded") {
    setText("#deep-work-drafts-message", "Exact dormant goal custody is present and revalidated. No worker, schedule, service, model request, provider call, external effect, or completion action occurred.");
  } else if (kind === "deep-work-service-render" && result.status === "succeeded") {
    setText("#deep-work-drafts-message", "Exact event-driven supervision was rendered and retained inactive. It is not installed, scheduled, or activated; no worker, model, or provider was called.");
  }
}

$("#add-deep-work-milestone").addEventListener("click", addMilestone);

$("#clear-deep-work-handoff").addEventListener("click", () => {
  workHandoffTaskHandle = null;
  $("#deep-work-handoff-note").hidden = true;
  setText("#deep-work-draft-message", "Chat provenance link cleared. The draft form remains unchanged.");
});

$("#deep-work-draft-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#create-deep-work-draft");
  button.disabled = true;
  setText("#deep-work-draft-message", "Validating the bounded local draft…");
  try {
    if (!privateWorkspaceAuthorized()) throw new Error("Use an authenticated private workspace before using private authoring.");
    const response = await requestFixedAction(deepWorkDraftRequest());
    if (response.state === "approval-required") setText("#deep-work-draft-message", "Exact inert draft creation is waiting inline for confirmation. No work has run.");
    closeInlineDeepWorkPlanner({ clearLink: true, resetDraft: true });
  } catch (error) {
    setText("#deep-work-draft-message", error.message);
    notify(error.message, "error");
  } finally {
    button.disabled = workAuthoring?.state !== "ready";
  }
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const request = { schemaVersion: 1, kind: button.dataset.action };
      if (["operations-pause", "frontier-pause"].includes(button.dataset.action)) {
        request.reason = $("#pause-reason").value.trim();
        if (!request.reason) throw new Error("Enter a short emergency pause reason first.");
      }
      await requestFixedAction(request);
    } catch (error) {
      notify(error.message, "error");
    } finally {
      button.disabled = button.dataset.policy ? operatorActionAvailability[button.dataset.policy] !== true : false;
    }
  });
});

$("#refresh-status").addEventListener("click", async () => {
  try {
    await refresh();
    notify("Local status refreshed.");
  } catch (error) {
    notify(error.message, "error");
  }
});

$("#load-frontier-reviews").addEventListener("click", async () => {
  const button = $("#load-frontier-reviews");
  button.disabled = true;
  setText("#frontier-review-message", "Loading exact sanitized capsules…");
  try {
    const reviews = await api("/api/v1/reviews/frontier", {
      headers: privateHeaders(),
    });
    renderFrontierReviews(reviews);
    setText("#frontier-review-message", `${formatCount(reviews.reviews.length)} pending review${reviews.reviews.length === 1 ? "" : "s"} loaded locally.`);
  } catch (error) {
    $("#frontier-review-list").replaceChildren();
    setText("#frontier-review-message", error.message);
    notify(error.message, "error");
  } finally {
    button.disabled = reviewViewAvailability.frontierReviews !== true || !privateWorkspaceAuthorized();
  }
});

$("#load-deep-work-review").addEventListener("click", async () => {
  const button = $("#load-deep-work-review");
  button.disabled = true;
  setText("#deep-work-review-message", "Rechecking the exact retained candidate and verifier evidence locally…");
  try {
    await loadDeepWorkSemanticReview();
  } catch (error) {
    clearDeepWorkSemanticReview();
    $("#deep-work-review-state").textContent = "Not available";
    if (chatProjection) renderChat(chatProjection);
    setText("#deep-work-review-message", error.message);
    notify(error.message, "error");
  } finally {
    button.disabled = reviewViewAvailability.deepWorkSemanticReviews !== true || !privateWorkspaceAuthorized();
  }
});

async function initializeWorkspace() {
  if (!reviewToken) {
    try {
      const session = await api("/api/v1/access-session");
      accessSessionVerified = session?.schemaVersion === 1 && session.state === "verified" && session.credentialsExposed === false;
    } catch {
      accessSessionVerified = false;
    }
  }
  await refresh();
}

initializeWorkspace().catch((error) => notify(error.message, "error"));
