from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "control" / "ui" / "index.html"
JS_PATH = ROOT / "control" / "ui" / "app.js"
CSS_PATH = ROOT / "control" / "ui" / "styles.css"


class SurfaceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.controls = []
        self.links = []
        self.scripts = []
        self.inline_handlers = []
        self.ids = set()
        self.h1_count = 0
        self.html_language = None
        self.has_nav = False
        self.title = None
        self._in_title = False
        self.buttons = []
        self._open_buttons = []

    def handle_data(self, data):
        if self._in_title:
            self.title = (self.title or "") + data
        if self._open_buttons:
            self._open_buttons[-1]["text"].append(data)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "html":
            self.html_language = values.get("lang")
        if tag == "h1":
            self.h1_count += 1
        if tag == "nav" or values.get("role") == "navigation":
            self.has_nav = True
        if tag == "title":
            self._in_title = True
        if tag == "button" or values.get("role") == "button":
            record = {"attrs": values, "text": []}
            self.buttons.append(record)
            self._open_buttons.append(record)
        if "id" in values:
            self.ids.add(values["id"])
        for name, value in attrs:
            if name.lower().startswith("on") or name.lower() == "style":
                self.inline_handlers.append((tag, name, value))
        if tag in {"input", "select", "textarea"}:
            self.controls.append((tag, values, "label" in self.stack))
        if tag == "a":
            self.links.append(values.get("href", ""))
        if tag == "script":
            self.scripts.append(values)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "button" and self._open_buttons:
            self._open_buttons.pop()
        if tag in self.stack:
            while self.stack:
                child = self.stack.pop()
                if child == tag:
                    break


class ControlUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.javascript = JS_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.parser = SurfaceParser()
        cls.parser.feed(cls.html)

    def test_document_has_accessible_structure_and_labels(self):
        self.assertEqual(self.parser.html_language, "en")
        self.assertEqual(self.parser.h1_count, 1)
        self.assertIn("main", self.parser.ids)
        self.assertIn('class="skip-link" href="#main"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        for tag, attributes, nested_in_label in self.parser.controls:
            self.assertTrue(
                nested_in_label or attributes.get("aria-label") or attributes.get("aria-labelledby"),
                f"unlabelled {tag} control: {attributes.get('name')}",
            )

    def test_buttons_have_accessible_names_and_page_has_title_and_nav(self):
        # Regression-protect page-orientation fundamentals the UI already ships.
        self.assertTrue((self.parser.title or "").strip(), "document is missing a non-empty <title>")
        self.assertTrue(self.parser.has_nav, "document is missing a <nav> landmark")
        # Every button must expose an accessible name: an aria-label / aria-labelledby / title, or
        # real alphanumeric text. An icon-only button (a "x" glyph or an SVG) with no name is
        # invisible to screen readers. The input/select/textarea label check never covered buttons,
        # so this closes that gap and locks the UI's labelled icon-buttons against regression.
        self.assertTrue(self.parser.buttons, "no buttons parsed - parser regression")
        for button in self.parser.buttons:
            attributes = button["attrs"]
            text = "".join(button["text"])
            named = bool(attributes.get("aria-label") or attributes.get("aria-labelledby") or attributes.get("title"))
            has_text_name = bool(re.search(r"[0-9A-Za-z]", text))
            self.assertTrue(named or has_text_name, f"button without an accessible name: {attributes}")

    def test_page_is_self_contained_and_has_no_inline_code(self):
        self.assertEqual(self.parser.inline_handlers, [])
        self.assertEqual(self.parser.scripts, [{"src": "/app.js", "defer": None}])
        for target in self.parser.links:
            self.assertTrue(target.startswith("/") or target.startswith("#"), target)
        self.assertNotRegex(self.html, r"https?://")
        self.assertIn('href="/styles.css"', self.html)

    def test_ui_assets_are_valid_utf8_without_common_mojibake(self):
        for label, payload in (
            ("HTML", self.html), ("JavaScript", self.javascript), ("CSS", self.css),
        ):
            for marker in ("\u00c2", "\u00c3", "\u00e2\u20ac"):
                self.assertNotIn(marker, payload, f"{label} contains mojibake")

    def test_browser_contract_has_no_credential_or_generic_command_field(self):
        names = [attributes.get("name", "").lower() for _tag, attributes, _labelled in self.parser.controls]
        dangerous = re.compile(r"api[-_]?key|access[-_]?token|password|secret|credential|private[-_]?key|command|shell|file[-_]?path")
        self.assertFalse(any(dangerous.search(name) for name in names), names)
        self.assertEqual(set(re.findall(r'data-action="([a-z-]+)"', self.html)), {
            "configure", "plan", "verify", "update-check", "backup-create", "operations-pause", "frontier-pause", "deep-work-pause", "deep-work-resume", "deep-work-cancel",
        })
        self.assertNotRegex(self.html, r'data-action="(?:.*restore|apply|approve)"')
        for forbidden in ("innerHTML", "outerHTML", "eval(", "document.write", "localStorage", "sessionStorage", "WebSocket", "EventSource"):
            self.assertNotIn(forbidden, self.javascript)
        self.assertNotRegex(self.javascript, r"https?://")
        for identifier in ("frontier-readiness", "frontier-provider", "frontier-setup", "frontier-billing", "frontier-budget-source", "frontier-job-budget", "frontier-input-budget", "frontier-output-budget", "frontier-failure-budget", "frontier-cost-budget", "frontier-setup-row", "frontier-auth-guidance"):
            self.assertIn(identifier, self.parser.ids)
        for identifier in (
            "frontier-budget-form", "frontier-budget-fields", "budget-editor-state",
            "budget-editor-guidance", "create-budget-proposal", "frontier-budget-proposal",
            "budget-proposal-summary", "budget-proposal-changes", "budget-proposal-command",
            "budget-editor-message",
        ):
            self.assertIn(identifier, self.parser.ids)
        for identifier in ("diagnostic-state", "diagnostic-summary", "diagnostic-incidents"):
            self.assertIn(identifier, self.parser.ids)
        for identifier in (
            "attestation-state", "attestation-summary", "attestation-observed", "attestation-source",
            "attestation-tree", "attestation-qualification", "attestation-runtime", "attestation-model",
            "attestation-config", "attestation-connectors",
        ):
            self.assertIn(identifier, self.parser.ids)
        for identifier in (
            "update-status-state", "update-status-summary", "update-current-version",
            "update-candidate-versions", "update-prepared-count", "update-rehearsed-count",
            "update-activation-count", "update-migration-state", "update-next-action",
        ):
            self.assertIn(identifier, self.parser.ids)
        for identifier in (
            "recovery-guide-state", "recovery-guide-summary", "recovery-backup-policy",
            "recovery-backup-last", "recovery-backup-next", "recovery-incident-state",
            "recovery-operations-resume", "recovery-frontier-resume",
        ):
            self.assertIn(identifier, self.parser.ids)
        for identifier in (
            "deep-work-state", "deep-work-summary", "deep-work-active", "deep-work-attention",
            "deep-work-artifacts", "deep-work-service-concerns", "deep-work-services",
            "deep-work-sessions", "deep-work-boundary", "deep-work-goal-heading", "deep-work-goal-state",
            "deep-work-goal-progress", "deep-work-goal-milestones", "deep-work-goal-checkpoints",
            "deep-work-goal-next", "deep-work-goal-model", "deep-work-goal-watchdog", "deep-work-goal-runtime", "deep-work-goal-requests",
            "deep-work-goal-tokens", "deep-work-goal-failures", "deep-work-goal-continuity",
            "deep-work-budget-jobs", "deep-work-budget-runtime", "deep-work-budget-requests",
            "deep-work-budget-input", "deep-work-budget-output", "deep-work-budget-network",
            "deep-work-budget-artifacts", "deep-work-budget-failures",
            "deep-work-authoring-title", "deep-work-authoring-state", "deep-work-authoring-guidance",
            "deep-work-draft-form", "deep-work-draft-fields", "deep-work-milestone-list",
            "add-deep-work-milestone", "create-deep-work-draft", "deep-work-draft-message",
            "deep-work-drafts-title", "deep-work-drafts-state", "deep-work-drafts-message", "deep-work-drafts",
            "knowledge-title", "knowledge-state", "knowledge-summary", "knowledge-storage",
            "knowledge-key", "knowledge-consent", "knowledge-deletion", "knowledge-backup", "knowledge-boundary",
        ):
            self.assertIn(identifier, self.parser.ids)
        for identifier in (
            "doctor-state", "doctor-summary", "doctor-host", "doctor-cpu", "doctor-memory",
            "doctor-storage", "doctor-accelerator", "doctor-model", "doctor-context", "doctor-checks",
            "doctor-accelerator-guidance",
            "doctor-configured-model", "doctor-configured-context", "doctor-reasoning", "doctor-fit",
        ):
            self.assertIn(identifier, self.parser.ids)
        self.assertIn('frontier.authMode === "chatgpt"', self.javascript)
        self.assertIn('frontier.billingBoundary', self.javascript)
        self.assertIn('form.elements.frontierAuthMode.value', self.javascript)
        self.assertIn("ChatGPT plan (recommended)", self.html)
        self.assertIn("OpenAI API (separately billed)", self.html)
        self.assertIn("Custom private policy — terminal managed", self.html)
        self.assertIn('budget === "custom"', self.javascript)
        self.assertIn("do not replace ChatGPT workspace limits or API Platform billing controls", self.html)
        self.assertIn('api("/api/v1/reviews/frontier", {', self.javascript)
        self.assertIn('"X-Pixel-Review-Token": reviewToken', self.javascript)
        self.assertIn("window.history.replaceState", self.javascript)
        self.assertIn("Exact sanitized capsule", self.javascript)
        self.assertIn("frontier-approve", self.javascript)
        self.assertIn('api("/api/v1/diagnostics")', self.javascript)
        self.assertIn('api("/api/v1/doctor")', self.javascript)
        self.assertIn('api("/api/v1/update-status")', self.javascript)
        self.assertIn("function renderUpdateStatus(value)", self.javascript)
        self.assertIn('updateStatus.state) ? 0 : 1', self.javascript)
        self.assertIn('api("/api/v1/recovery-guide")', self.javascript)
        self.assertIn('api("/api/v1/deep-work")', self.javascript)
        self.assertIn('api("/api/v1/frontier-budget/preview", {', self.javascript)
        self.assertIn("browser writes a short-lived, hash-bound proposal but cannot apply it", self.html)
        self.assertIn("frontier-budget apply --proposal-id", self.javascript)
        self.assertIn("Nothing was applied or sent", self.javascript)
        self.assertIn("function renderRecoveryGuide(value)", self.javascript)
        self.assertIn("function renderDeepWork(value)", self.javascript)
        self.assertIn("function renderAttestation(value)", self.javascript)
        self.assertIn("capability unproven", self.javascript)
        self.assertIn("does not claim that the selected model passed Pixel’s real-work capability qualification", self.html)
        self.assertIn('attestation.state === "verified"', self.javascript)
        self.assertIn("function deepWorkGoalActionLabel(value)", self.javascript)
        self.assertIn("milestones independently verified", self.javascript)
        self.assertIn("Each durable checkpoint triggers the next bounded decision", self.javascript)
        self.assertIn("Elapsed time creates no progress", self.javascript)
        self.assertIn("function deepWorkCapabilityLabel(value)", self.javascript)
        self.assertIn('appendWorkField(fields, "Special tools"', self.javascript)
        self.assertIn('appendWorkField(fields, "Failure class"', self.javascript)
        self.assertIn("Remaining goal capacity", self.html)
        self.assertIn("settled milestones plus the latest observed usage", self.html)
        self.assertIn("goal.budgets.remaining.inputTokens", self.javascript)
        self.assertIn("session.usage.inputTokens", self.javascript)
        self.assertIn('session.current ? "Current "', self.javascript)
        self.assertIn('deepWork.summary.attentionSessions + deepWork.summary.degradedServices', self.javascript)
        self.assertIn('value.state === "offline" ? `Last known:', self.javascript)
        self.assertIn('criteria passing`)', self.javascript)
        self.assertIn("Content-free with checkpoint-bound lifecycle controls", self.html)
        self.assertIn("require an exact fresh review and confirmation", self.html)
        self.assertIn("cannot stop or hide a live worker", self.html)
        self.assertIn('data-action="deep-work-pause" data-policy="deepWorkPause"', self.html)
        self.assertIn('data-action="deep-work-resume" data-policy="deepWorkResume"', self.html)
        self.assertIn('data-action="deep-work-cancel" data-policy="deepWorkCancel"', self.html)
        self.assertIn('"deep-work-pause": "Deep Work scheduling pause"', self.javascript)
        self.assertIn('"deep-work-resume": "Deep Work scheduling resume"', self.javascript)
        self.assertIn('"deep-work-cancel": "Deep Work safe cancellation"', self.javascript)
        self.assertIn('"deep-work-draft": "Inert Deep Work goal draft"', self.javascript)
        self.assertIn('"deep-work-prepare": "Inactive Deep Work launch package"', self.javascript)
        self.assertIn('"deep-work-stage": "Dormant Deep Work goal custody"', self.javascript)
        self.assertIn('"deep-work-service-render": "Inactive Deep Work supervisor bundle"', self.javascript)
        self.assertIn('api("/api/v1/deep-work/authoring", { headers: privateHeaders() })', self.javascript)
        self.assertIn('api("/api/v1/deep-work/drafts", { headers: privateHeaders() })', self.javascript)
        self.assertIn('api("/api/v1/access-session")', self.javascript)
        self.assertIn("function privateWorkspaceAuthorized()", self.javascript)
        self.assertIn("function renderWorkDraftReviews(value)", self.javascript)
        self.assertIn("exact retained drafts, not chat promises", self.javascript)
        self.assertIn('kind: "deep-work-prepare"', self.javascript)
        self.assertIn('kind: "deep-work-stage"', self.javascript)
        self.assertIn('kind: "deep-work-service-render"', self.javascript)
        self.assertIn("Prepare exact inactive goal", self.javascript)
        self.assertIn("Stage exact dormant goal", self.javascript)
        self.assertIn("Render inactive supervisor", self.javascript)
        self.assertIn("No work has been staged or run", self.javascript)
        self.assertIn("cannot install or activate a service", self.html)
        self.assertIn("No worker, schedule, service, model, or provider will start", self.javascript)
        self.assertIn("Evidence before narration", self.html)
        self.assertIn("cannot start work", self.html)
        self.assertIn('kind: "deep-work-draft"', self.javascript)
        self.assertIn("function deepWorkDraftRequest()", self.javascript)
        self.assertIn("function addMilestone()", self.javascript)
        self.assertIn("Goals can branch and converge through an acyclic dependency graph", self.html)
        self.assertIn('className = "milestone-dependencies"', self.javascript)
        self.assertIn("milestoneNumber.get(input.value)", self.javascript)
        self.assertIn("cannot choose host paths, admit files, compile or stage jobs", self.html)
        self.assertIn("No local input snapshots are admitted", self.javascript)
        self.assertIn("Candidate and verifier review", self.html)
        self.assertIn('id="load-deep-work-review"', self.html)
        self.assertIn('api("/api/v1/reviews/deep-work", {', self.javascript)
        self.assertIn('"X-Pixel-Review-Token": reviewToken', self.javascript)
        self.assertIn("function renderDeepWorkSemanticReview(value)", self.javascript)
        self.assertIn("The deterministic verifier did not establish semantic accuracy", self.javascript)
        self.assertIn("Acceptance remains an exact trusted-terminal action", self.html)
        self.assertIn("function renderPrivateKnowledge(value)", self.javascript)
        self.assertIn('item.id === "knowledge-vault"', self.javascript)
        self.assertIn("./pixel work-knowledge-guide add", self.html)
        self.assertIn("plain-language trusted-terminal guide", self.html)
        self.assertIn("This page never receives source text, titles, file paths, vault keys, or deletion authority", self.html)
        self.assertIn("contentLeavesHost", self.javascript)
        self.assertIn("recoveryGuide.state === \"attention\"", self.javascript)
        self.assertIn('doctorCapacity(value.host.cpuCapacity, "cpu")', self.javascript)
        self.assertIn('configured: "Configured"', self.javascript)
        self.assertIn("network probe, or provider call", self.html)
        self.assertIn("No hostname, serial, device name, model identifier, URL", self.html)
        self.assertIn('value.diagnostics.state === "unavailable" ? 1', self.javascript)
        self.assertIn("diagnostics: diagnostics.summary", self.javascript)
        self.assertIn("private evidence", self.javascript)
        self.assertNotIn("privateLogSha256", self.javascript)
        self.assertNotIn("stagingPath", self.javascript)
        self.assertNotIn("artifactPath", self.javascript)
        self.assertNotIn("pauseReason", self.javascript)
        for forbidden in ("sessionHandle", "eventId", "projectionId", "privateObjective", "objectiveText", "promptText", "toolArguments", "artifactNames", "providerContent"):
            self.assertNotIn(forbidden, self.javascript)
        self.assertNotRegex(self.html, r'data-action="(?:.*restore|apply|approve)"')

    def test_profile_changes_apply_reviewable_release_defaults(self):
        expected = {
            "minimal": "email: false, calendar: false, social: false, web: false, operations: false, frontier: false",
            "chief-of-staff": "email: true, calendar: true, social: false, web: true, operations: false, frontier: false",
            "research": "email: false, calendar: false, social: false, web: true, operations: false, frontier: false",
            "engineering-operator": "email: true, calendar: true, social: false, web: true, operations: true, frontier: false",
        }
        for name, limbs in expected.items():
            key = name if name == "minimal" or name == "research" else f'"{name}"'
            self.assertIn(f"{key}: {{ {limbs} }}", self.javascript)
        self.assertIn('form.elements.capabilityProfile.addEventListener("change"', self.javascript)

    def test_primary_workspace_keeps_chat_progress_and_safety_in_one_flow(self):
        for identifier in (
            "agent-workspace", "workspace-nav", "new-conversation", "chat-conversation-list",
            "workspace-title", "workspace-run-state", "chat-thread", "chat-form", "chat-message",
            "send-message", "workspace-inspector", "workspace-approvals", "open-control-center",
            "control-center", "close-control-center",
        ):
            self.assertIn(identifier, self.parser.ids)
        self.assertIn("Start with the outcome you want.", self.html)
        self.assertIn("Never hidden.", self.html)
        self.assertIn("the exact proposed effect remains pending in the conversation where you asked for it", self.html)
        self.assertIn("Choose what Pixel may do without interrupting you", self.html)
        self.assertIn("Auto within policy", self.html)
        self.assertIn('api("/api/v1/chat", { headers: privateHeaders() })', self.javascript)
        self.assertIn('api("/api/v1/chat/tasks", {', self.javascript)
        self.assertIn('api("/api/v1/approvals", { headers: privateHeaders() })', self.javascript)
        self.assertIn('api("/api/v1/actions/cancel", {', self.javascript)
        self.assertIn('api("/api/v1/permissions", { headers: privateHeaders() })', self.javascript)
        self.assertIn('approvalRoute("/approve/permissions", "/api/v1/permissions")', self.javascript)
        self.assertIn('api("/api/v1/actions/request", {', self.javascript)
        self.assertIn("function renderPermissions(value)", self.javascript)
        self.assertIn("function requestFixedAction(request)", self.javascript)
        self.assertIn('"X-Pixel-Review-Token": reviewToken', self.javascript)
        self.assertIn("Pixel is working. You can continue typing while this turn runs.", self.javascript)
        self.assertIn("function chatActivityLabel(code)", self.javascript)
        self.assertIn("function chatCapabilityLabel(capability)", self.javascript)
        self.assertIn("ran automatically within local policy", self.javascript)
        self.assertIn("consequential broker evidence required", self.javascript)
        self.assertIn("bounded external action completed within policy", self.javascript)
        self.assertIn("broker accepted the request; outcome is still pending", self.javascript)
        self.assertIn("exact broker approval is required", self.javascript)
        self.assertIn("broker outcome is ambiguous—review before retry", self.javascript)
        self.assertIn("Accepted into durable private custody", self.javascript)
        self.assertIn("Progress is durable; this page can reconnect while the local agent continues.", self.javascript)
        self.assertIn('api("/api/v1/chat", { headers: privateHeaders() })', self.javascript)
        self.assertIn("function chatDeepWorkCard()", self.javascript)
        self.assertIn("let chatDraftingNew = false", self.javascript)
        self.assertIn("if (!active && conversations.length && !chatDraftingNew) active = conversations[0]", self.javascript)
        self.assertIn("Current durable goal", self.javascript)
        self.assertIn("Last retained goal snapshot", self.javascript)
        self.assertIn('const stale = value.state === "offline"', self.javascript)
        self.assertIn('const degraded = value.state === "degraded"', self.javascript)
        self.assertIn('degraded ? "Service concern"', self.javascript)
        self.assertIn("Load exact evidence and artifacts", self.javascript)
        self.assertIn("function chatHandoffControl(turn)", self.javascript)
        self.assertIn("Plan as Deep Work", self.javascript)
        self.assertIn("Deep Work handoff sealed", self.javascript)
        self.assertIn("exact inert goal declaration", self.javascript)
        self.assertIn("chatTaskHandle: workHandoffTaskHandle", self.javascript)
        self.assertIn("#deep-work-handoff-note", self.javascript)
        self.assertIn('id="chat-inline-workbench"', self.html)
        self.assertIn('id="deep-work-authoring-panel"', self.html)
        self.assertIn("function openInlineDeepWorkPlanner(taskHandle, source)", self.javascript)
        self.assertIn('$("#chat-inline-workbench-content").append($("#deep-work-authoring-panel"))', self.javascript)
        self.assertIn("closeInlineDeepWorkPlanner({ clearLink: true, resetDraft: true })", self.javascript)
        self.assertIn('id="workspace-permission-editor"', self.html)
        self.assertIn('id="workspace-permission-form"', self.html)
        self.assertIn("function populatePermissionSettings(list, settings, dataAttribute)", self.javascript)
        self.assertIn("async function savePermissionDefaults(form, dataAttribute, button, messageSelector)", self.javascript)
        self.assertIn('populatePermissionSettings($("#workspace-permission-list"), settings, "workspacePermissionKind")', self.javascript)
        self.assertIn('id="workspace-evidence-inspector"', self.html)
        self.assertIn('id="workspace-evidence-content"', self.html)
        self.assertIn("function appendChatLifecycleControls(article, goal, stale)", self.javascript)
        self.assertIn('button.dataset.chatLifecycleAction = kind', self.javascript)
        self.assertIn('requestFixedAction({ schemaVersion: 1, kind })', self.javascript)
        self.assertIn("function appendChatRecoveryGuidance(article, goal)", self.javascript)
        self.assertIn("function openWorkspaceEvidenceInspector()", self.javascript)
        self.assertIn('target.append(source.cloneNode(true))', self.javascript)
        self.assertNotIn('showControlCenter(true);\n      window.requestAnimationFrame(() => $("#deep-work-review-content")', self.javascript)
        self.assertIn("does not claim that the selected chat created it unless an explicit handoff receipt is shown", self.javascript)
        self.assertIn("async function loadDeepWorkSemanticReview()", self.javascript)
        self.assertIn("function clearDeepWorkSemanticReview()", self.javascript)
        self.assertIn("async function refresh() {\n  clearDeepWorkSemanticReview();", self.javascript)
        self.assertIn("function approvalCard(preview)", self.javascript)
        self.assertIn('approve.textContent = "Approve once"', self.javascript)
        self.assertIn("function ensureApprovalSession()", self.javascript)
        self.assertIn('popup.location.replace(`/approve/session?challenge=${challenge}`)', self.javascript)
        self.assertIn('approvalRoute("/approve/execute", "/api/v1/actions/execute")', self.javascript)
        self.assertIn('new BroadcastChannel(`pixel-approval-${challenge}`)', self.javascript)
        self.assertIn('event.data?.type !== "pixel-approval-ready"', self.javascript)
        self.assertIn('event.data?.challenge !== challenge', self.javascript)
        self.assertNotIn("confirmation-dialog", self.html)
        self.assertNotIn("dialog.showModal", self.javascript)
        self.assertIn('window.matchMedia("(max-width: 1180px)")', self.javascript)
        self.assertNotIn("rawLauncherOutput", self.javascript)
        self.assertNotIn("conversationId", self.javascript)

    def test_keyboard_reduced_motion_and_responsive_styles_are_explicit(self):
        self.assertIn(":focus-visible", self.css)
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertRegex(self.css, r"@media\s*\(max-width:")
        self.assertIn("dialog::backdrop", self.css)
        self.assertIn("[hidden] { display: none !important; }", self.css)

    def test_trust_and_provenance_ux_is_present_and_first_class(self):
        # First-user-experience / trust-and-provenance milestone: the portal must visibly
        # distinguish current evidence from remembered context, surface per-work provenance with an
        # evidence inspector, present research with citations and evidence-versus-inference labels,
        # keep the frontier review pane strictly review-only, and retain an immutable provenance
        # receipt. Locking these load-bearing contract strings prevents a silent trust-UX regression.
        self.assertIn("distinguish current evidence from remembered context", self.html)
        self.assertIn("inline citations, and explicit evidence-versus-inference labels", self.html)
        self.assertIn("visible provenance", self.html)
        self.assertIn("immutable provenance receipt", self.html)
        self.assertIn("This page cannot approve or transmit a capsule", self.html)
        # The work-card provenance surface and its evidence inspector are built in app.js.
        self.assertIn("work-card-provenance", self.javascript)
        self.assertIn("Inspect full evidence", self.javascript)
        self.assertIn("openWorkspaceEvidenceInspector", self.javascript)


if __name__ == "__main__":
    unittest.main()
