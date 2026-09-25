"""WriteFile classification contracts.

`WriteFile` is the only intent whose default policy is `path_guard`. Every
other file intent is `mode: allow`. So a write that is classified as anything
else is a write that nothing checks — the path guard never runs on it.
"""

import os

import pytest


@pytest.fixture()
def ape(ape_env):
    """The imported APE module with an isolated environment."""
    import importlib

    import main as ape_main

    return importlib.reload(ape_main)


# ── Edit-shaped tools are writes ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name,args",
    [
        # The name says write, in one spelling or another.
        ("write_file", {"path": "/etc/cron.d/x", "content": "x"}),
        ("edit_file", {"path": "/etc/cron.d/x", "content": "x"}),
        ("apply_patch", {"path": "/etc/cron.d/x", "content": "x"}),
        ("str_replace_editor", {"path": "/root/.ssh/authorized_keys",
                                "new_str": "ssh-rsa AAAA"}),
        ("update_config", {"file": "/home/other/.bashrc", "content": "x"}),
        ("Edit", {"file_path": "/etc/passwd", "new_string": "x"}),
        # The name says nothing, but the payload does.
        ("do_thing", {"path": "/etc/cron.d/x", "content": "x"}),
        ("do_thing", {"path": "/etc/cron.d/x", "text": "x"}),
        ("do_thing", {"path": "/etc/cron.d/x", "new_str": "x"}),
        # An explicit non-read mode is a write whatever the payload looks like.
        ("open", {"path": "/etc/hosts", "mode": "w"}),
        ("open", {"path": "/etc/hosts", "mode": "a"}),
        ("open", {"path": "/etc/hosts", "mode": "wb"}),
    ],
)
def test_writes_are_classified_as_writefile(ape, tool_name, args):
    assert ape.classify_intent(tool_name, args) == "WriteFile"


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("write_file", {"path": "/etc/cron.d/x", "content": "x"}),
        ("edit_file", {"path": "/etc/cron.d/x", "content": "x"}),
        ("str_replace_editor", {"path": "/root/.ssh/authorized_keys",
                                "new_str": "ssh-rsa AAAA"}),
        ("Edit", {"file_path": "/etc/passwd", "new_string": "x"}),
    ],
)
def test_writes_outside_the_workspace_are_denied(ape, tool_name, args):
    """End to end: classification plus the guard it exists to reach."""
    intent = ape.classify_intent(tool_name, args)
    allowed, reason = ape.evaluate(intent, tool_name, args, ape.DEFAULT_POLICY)
    assert not allowed, f"{tool_name} {args} was allowed: {reason}"
    assert "outside allowed paths" in reason


# ── Reads stay reads ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("read_file", {"path": "/etc/hostname"}),
        ("cat", {"path": "/etc/hostname"}),
        ("view", {"path": "/etc/hostname"}),
        ("head", {"file": "/etc/hostname"}),
        # No verb match and no write payload: still a read.
        ("some_tool", {"path": "/etc/hostname"}),
        ("open", {"path": "/etc/hostname", "mode": "r"}),
        ("open", {"path": "/etc/hostname", "mode": "rb"}),
    ],
)
def test_reads_are_classified_as_readfile(ape, tool_name, args):
    assert ape.classify_intent(tool_name, args) == "ReadFile"


def test_reads_are_not_blocked_by_the_write_guard(ape):
    """A read outside the workspace is still allowed — this is not a lockdown."""
    args = {"path": "/etc/hostname"}
    intent = ape.classify_intent("read_file", args)
    allowed, _ = ape.evaluate(intent, "read_file", args, ape.DEFAULT_POLICY)
    assert allowed


# ── Writes inside the workspace still pass ───────────────────────────────────


@pytest.mark.skipif(
    os.name == "nt",
    reason="path_guard joins allowed_paths with a literal '/', so the prefix "
           "match cannot succeed against a Windows realpath. APE ships in a "
           "Linux container; assert the allow side where it runs.",
)
def test_write_inside_an_allowed_path_is_permitted(ape, tmp_path):
    """Built from realpath so the assertion holds on any platform."""
    allowed_root = os.path.realpath(str(tmp_path))
    target = os.path.join(allowed_root, "notes.txt")
    policy = {
        "intents": {
            "WriteFile": {"mode": "path_guard", "allowed_paths": [allowed_root]}
        }
    }
    args = {"path": target, "content": "ok"}
    intent = ape.classify_intent("edit_file", args)
    assert intent == "WriteFile"
    allowed, reason = ape.evaluate(intent, "edit_file", args, policy)
    assert allowed, reason


# ── Other intents are untouched ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name,args,expected",
    [
        ("exec", {"command": "ls"}, "ExecuteCommand"),
        ("run_shell", {"command": "ls"}, "ExecuteCommand"),
        ("anything", {"command": "ls"}, "ExecuteCommand"),
        ("web_fetch", {"url": "https://example.com"}, "NetworkFetch"),
        ("anything", {"url": "https://example.com"}, "NetworkFetch"),
        ("spawn_agent", {}, "SpawnAgent"),
        ("mystery", {}, "Other"),
    ],
)
def test_other_intents_unchanged(ape, tool_name, args, expected):
    assert ape.classify_intent(tool_name, args) == expected


def test_exec_wins_over_a_write_shaped_name(ape):
    """`run` is an exec verb; the exec check must stay first."""
    assert ape.classify_intent("run_update", {"command": "ls"}) == "ExecuteCommand"


# ── The path-argument helper is shared with path_guard ───────────────────────


@pytest.mark.parametrize(
    "key", ["path", "file", "filename", "file_path", "filepath",
            "target_path", "dest", "destination"],
)
def test_path_guard_reads_every_documented_path_key(ape, key):
    args = {key: "/etc/cron.d/backdoor", "content": "x"}
    assert ape.classify_intent("edit", args) == "WriteFile"
    allowed, reason = ape.evaluate("WriteFile", "edit", args, ape.DEFAULT_POLICY)
    assert not allowed, f"{key} was not guarded: {reason}"
