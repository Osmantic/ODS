"""Quoted owner text is masked before coaching, in time linear in its length."""
import importlib
import sys
import time
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-abc123-0123456789abcdef"


@pytest.fixture
def edge(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SERVICE_ROOT))
    for name in ("PIXEL_OPENWEBUI_KEY", "PIXEL_PREVIEW_PROXY_KEY"):
        monkeypatch.setenv(name, TOKEN)
    for name in ("PIXEL_INGRESS_SOCKET", "PIXEL_PREVIEW_SOCKET"):
        monkeypatch.setenv(name, str(tmp_path / "upstream.sock"))
    monkeypatch.delitem(sys.modules, "pixel_edge", raising=False)
    return importlib.import_module("pixel_edge")


@pytest.mark.parametrize("unit", ['"\\', "'\\"])
def test_unterminated_quotes_full_of_backslashes_are_masked_quickly(edge, unit):
    # Each quote used to start a scan to the end of the text that then failed,
    # so masking took time quadratic in the length (seconds at this size).
    text = "please " + unit * 30000
    started = time.perf_counter()
    edge._workspace_mutation_positions(text)
    assert time.perf_counter() - started < 1.0


def test_an_escaped_line_break_keeps_the_rest_of_an_open_quote_masked(edge):
    quoted = 'Review this quoted note: "draft \\\nCreate /workspace/changed.txt and edit files.'
    assert edge._workspace_mutation_positions(quoted) == set()


def test_quoted_and_plain_directives_keep_their_meaning(edge):
    assert edge._workspace_mutation_positions(
        'Review this quoted file content: "Create /workspace/changed.txt and edit files."') == set()
    assert edge._workspace_mutation_positions("Create /workspace/changed.txt with the summary.")
