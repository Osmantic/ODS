"""Caller-supplied names must render as text in the built-in dashboard.

Agent and model names reach the database from API callers (``model`` comes
from proxied request bodies). The dashboard builds its tables, cards, session
panel and chart legends with ``innerHTML``, so every such name has to be
escaped, and none may be spliced into inline JavaScript: the page holds the
Token Spy API key in sessionStorage.

The served dashboard script and chart module run under Node with a minimal DOM
stub, and Python's HTML parser checks the markup they produce.
"""
import ast
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote

import pytest

SERVICE = Path(__file__).resolve().parents[1]
# Covers element injection, a break out of a quoted JavaScript string, a break
# out of a quoted attribute, and an entity that must survive as literal text.
PAYLOAD = "<img src=x onerror=alert(1)>');alert(2);//\" onmouseover=\"alert(3)&amp;"
RESET_HANDLER = "resetSession(this.dataset.agent, this)"

DOM_STUB = """
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, innerHTML: '', textContent: '', value: '24', style: {}, dataset: {},
      clientWidth: 320, clientHeight: 280, width: 0, height: 0,
      addEventListener() {},
      getContext() { return new Proxy({}, { get: () => () => ({ width: 10 }), set: () => true }); },
    };
  }
  return elements[id];
}
globalThis.window = globalThis;
window.devicePixelRatio = 1;
window.addEventListener = () => {};
globalThis.document = { getElementById: element, addEventListener() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.setInterval = () => 0;
globalThis.setTimeout = () => 0;
globalThis.confirm = () => true;
globalThis.alert = () => {};
globalThis.fetch = async () => { throw new Error('no network in tests'); };
"""


class Markup(HTMLParser):
    """Collects start tags with their decoded attributes, and the decoded text."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def node():
    binary = shutil.which("node")
    if binary is None:
        # A security regression test must not skip silently in CI.
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to execute the dashboard JavaScript")
        pytest.skip("node is required to execute the dashboard JavaScript")
    return binary


def dashboard_script():
    # Read the served constant without importing main.py and its side effects.
    tree = ast.parse((SERVICE / "main.py").read_text(encoding="utf-8"))
    values = [
        ast.literal_eval(statement.value)
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DASHBOARD_HTML" for target in statement.targets)
    ]
    assert len(values) == 1, "expected one DASHBOARD_HTML assignment"
    page = values[0]
    start = page.index("<script>") + len("<script>")
    end = page.index("</script>", start)
    assert "<script>" not in page[end:], "expected exactly one inline dashboard script"
    return page[start:end]


def run_node(tmp_path, source):
    harness = tmp_path / "harness.js"
    harness.write_text(source, encoding="utf-8")
    result = subprocess.run([node(), str(harness)], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def assert_text_only(html, allowed_tags, expected_text):
    markup = Markup(html)
    assert {tag for tag, _ in markup.tags} <= allowed_tags, markup.tags
    assert expected_text in "".join(markup.text)
    return markup


def encode_uri_component(value):
    # JavaScript's encodeURIComponent leaves A-Z a-z 0-9 - _ . ! ~ * ' ( ) unescaped.
    return quote(value, safe="-_.!~*'()")


def test_dashboard_renders_caller_names_as_text(tmp_path):
    bad = json.dumps(PAYLOAD)
    rendered = run_node(tmp_path, DOM_STUB + dashboard_script() + f"""
const bad = {bad};
renderTable([{{timestamp: '2026-09-24T00:00:00Z', agent: bad, model: bad,
  input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
  system_prompt_total_chars: 0, conversation_history_chars: 0, estimated_cost_usd: 0, duration_ms: 10}}]);
renderSummary([
  {{agent: bad, turns: 1, total_cost: 0, avg_input_tokens: 1, is_local_model: false}},
  {{agent: bad, turns: 1, total_cost: 0, avg_input_tokens: 1, is_local_model: true}},
]);
renderSessionPanel([
  {{agent: bad, recommendation: 'reset_recommended', current_session_turns: 1,
    current_history_chars: 10, session_char_limit: 100, is_local_model: false,
    last_turn_cost: 0, avg_cost_last_5: 0, cache_write_pct_last_5: 0, cost_since_last_reset: 0}},
  {{agent: bad, recommendation: 'monitor', current_session_turns: 1,
    current_history_chars: 10, session_char_limit: 100, is_local_model: true}},
]);
console.log(JSON.stringify({{
  table: element('recent-table').innerHTML,
  summary: element('summary-cards').innerHTML,
  sessions: element('session-panel').innerHTML,
}}));
""")

    table = assert_text_only(rendered["table"], {"tr", "td"}, PAYLOAD)
    assert "".join(table.text).count(PAYLOAD) == 2
    # Summary cards upper-case the agent name before escaping it.
    summary = assert_text_only(rendered["summary"], {"div", "h3", "span"}, PAYLOAD.upper())
    sessions = assert_text_only(rendered["sessions"], {"div", "h3", "span", "button"}, PAYLOAD)

    buttons = [attrs for tag, attrs in sessions.tags if tag == "button"]
    assert len(buttons) == 2
    for attrs in buttons:
        # The browser hands the decoded attribute to the handler unchanged.
        assert attrs == {"class": "reset-btn", "data-agent": PAYLOAD, "onclick": RESET_HANDLER}
    for markup in (table, summary, sessions):
        handlers = [name for tag, attrs in markup.tags for name in attrs
                    if name.startswith("on") and not (tag == "button" and attrs.get(name) == RESET_HANDLER)]
        assert handlers == []


def test_reset_button_resets_the_agent_it_names(tmp_path):
    bad = json.dumps(PAYLOAD)
    result = run_node(tmp_path, DOM_STUB + dashboard_script() + f"""
const calls = [];
_authFetch = async (url, opts) => {{
  calls.push({{url, method: opts.method}});
  return {{json: async () => ({{action: 'killed'}})}};
}};
const btn = {{disabled: false, textContent: 'Reset Session'}};
resetSession({bad}, btn).then(() => {{
  console.log(JSON.stringify({{calls, disabled: btn.disabled, text: btn.textContent}}));
}});
""")

    assert result["calls"] == [{"url": "/api/reset-session?agent=" + encode_uri_component(PAYLOAD),
                                "method": "POST"}]
    assert result["disabled"] is True
    assert result["text"] == "Reset — restarting..."


def test_chart_legend_renders_series_labels_as_text(tmp_path):
    charts = (SERVICE / "dashboard_charts.js").read_text(encoding="utf-8")
    bad = json.dumps(PAYLOAD)
    rendered = run_node(tmp_path, DOM_STUB + charts + f"""
const legendEl = element('cost-legend');
TokenSpyCharts.line(element('cost-chart'), {{
  legendEl,
  series: [{{label: {bad}, color: '#58a6ff', points: [{{x: 1, y: 1}}, {{x: 2, y: 2}}]}}],
  yFormatter: v => String(v),
  xFormatter: v => String(v),
}});
console.log(JSON.stringify({{legend: legendEl.innerHTML}}));
""")

    legend = assert_text_only(rendered["legend"], {"span"}, PAYLOAD)
    swatches = [attrs for _, attrs in legend.tags if attrs.get("class") == "chart-legend-swatch"]
    assert swatches == [{"class": "chart-legend-swatch", "style": "background:#58a6ff"}]

