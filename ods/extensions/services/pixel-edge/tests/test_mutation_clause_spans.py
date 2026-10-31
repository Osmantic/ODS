"""Directive spans and bounded processing of hostile clause/quote whitespace."""
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


@pytest.mark.parametrize(("text", "verbs"), [
    ("Create /workspace/result.txt.", ("Create",)),
    ("\tCreate /workspace/result.txt.", ("Create",)),
    ("Read the files, then write the report to /workspace/answer.txt.", ("write",)),
    ("Do not edit files; then please write /workspace/out.txt.", ("write",)),
    ("Read files without changing them, but create /workspace/report.", ("create",)),
    ("Do not create files and write files.", ()),
    ("Tell me whether to write files.", ()),
    ("Read a file; write the answer.", ()),
    ("Never edit files; Create files.", ("Create",)),
    ("Please create files and update files.", ("create", "update")),
    ('Create "/workspace/answer.txt", then edit files.', ("Create", "edit")),
    ("then write files.", ()),
    (".write files.", ()),
    ("\nthen write files.", ("write",)),
    ("Read files; instead\twrite files.", ("write",)),
    ('Review this text: "Create files and write /workspace/out.txt."', ()),
    ("Review:\n```text\nCreate files.\n```", ()),
    ("Review:\n~~~text\nCreate files.\n~~~", ()),
    ("Review:\n\t\n\t> Create files.\n\t> edit files.\n", ()),
    ('Review an open quote: "line \\\nCreate files.', ()),
    ("Read `write /workspace/out.txt` and explain it.", ()),
    ("\u2003Create files;\r\n\u2003never edit files. Then update files.", ("Create", "update")),
])
def test_positive_directive_positions_preserve_quotes_scope_and_negation(edge, text, verbs):
    # Compare exact offsets in the owner's original text, not just a boolean.
    assert edge._workspace_mutation_positions(text) == {text.index(verb) for verb in verbs}


@pytest.mark.parametrize("text", [
    "\t" * 60000 + "Create files.",
    "\t\n" * 40000 + "Create files.",
    ";\t" * 20000 + "Create files.",
])
def test_long_tab_and_delimiter_runs_keep_the_exact_directive_span(edge, text):
    started = time.perf_counter()
    assert edge._workspace_mutation_positions(text) == {text.index("Create")}
    assert time.perf_counter() - started < 1.0


def test_blank_tab_lines_before_a_quote_do_not_repeatedly_scan_to_the_end(edge):
    text = "\t\n" * 40000 + "> Create files.\n"
    started = time.perf_counter()
    assert edge._workspace_mutation_positions(text) == set()
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(("text", "accepted"), [
    ("hi", True), ("hi\t\n!?\t", True), ("hi! ?", False),
    ("just testing one two three four\t!\n", True),
    ("test one two three four five", False), ("high", False),
    ("test . !", True), ("test . .", True), ("test foo....", True),
    ("hi! !", False), ("hi !?", True), ("\u2003H\u0130\t!?\n", True),
    ("test " + "." * 50 + "\t" * 50 + "*", False),
    ("hi" + "\t" * 40000 + "x", False),
])
def test_short_test_language_is_preserved_without_ambiguous_trailing_space(edge, text, accepted):
    started = time.perf_counter()
    assert edge._is_short_test_message(text) is accepted
    assert time.perf_counter() - started < 1.0


def test_the_existing_short_reply_length_bound_is_still_enforced(edge):
    text = "test " + "x" * 200
    assert edge._SHORT_TEST_MESSAGE.fullmatch(text)
    assert not edge._is_short_test_message(text)
    assert edge._empty_reply_fallback({"messages": [{"role": "user", "content": text}]}) == edge._EMPTY_REPLY


def test_overlong_direct_helper_input_is_rejected_before_matching(edge, monkeypatch):
    class NoMatchAllowed:
        def fullmatch(self, _text):
            raise AssertionError("Overlong input must never enter the grammar")

    monkeypatch.setattr(edge, "_SHORT_TEST_MESSAGE", NoMatchAllowed())
    assert not edge._is_short_test_message("hi" + "\t" * 200)
    assert not edge._is_short_test_message("test " + "." * 40000 + "\t" * 40000 + "*")


def test_existing_message_trimming_happens_before_the_short_reply_length_guard(edge):
    text = "\u2003" * 200 + "hi" + "\t\n" * 200
    assert not edge._is_short_test_message(text)
    assert edge._empty_reply_fallback({"messages": [{"role": "user", "content": text}]}) == edge._SHORT_TEST_REPLY
