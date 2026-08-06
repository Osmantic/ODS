"""ExecuteCommand allowlist contracts.

The allowlist is the only thing standing between an agent and arbitrary shell
execution on the host. A command string is allowlistable only when every
program it invokes is on the list — checking the first token alone approves
`echo hi; rm -r /data` on the strength of the `echo`.
"""

import pytest


@pytest.fixture()
def ape(ape_env):
    """The imported APE module with an isolated environment."""
    import importlib

    import main as ape_main

    return importlib.reload(ape_main)


def decide(ape, command):
    """Run one ExecuteCommand string through the default policy."""
    return ape.evaluate(
        "ExecuteCommand", "bash", {"command": command}, ape.DEFAULT_POLICY
    )


# ── Single allowlisted commands still pass ───────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la /tmp",
        "cat /etc/hostname",
        "grep foo file.txt",
        "pwd",
        # A pipeline of allowlisted programs is still every-program-allowlisted.
        "grep foo file.txt | head",
        "cat access.log | grep 404 | wc -l",
        # A separator inside quotes is data, not a command boundary.
        "grep 'a;b' file.txt",
        'grep "x && y" file.txt',
    ],
)
def test_allowlisted_commands_are_permitted(ape, command):
    allowed, reason = decide(ape, command)
    assert allowed, f"{command!r} was denied: {reason}"


# ── Chaining past an allowlisted first token ─────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "echo hi; rm -r /home/node",
        "echo hi && rm -r /home/node",
        "echo hi || rm -r /home/node",
        "echo hi & rm -r /home/node",
        "ls | rm -r /home/node",
        "grep foo file.txt | tee /etc/cron.d/backdoor",
    ],
)
def test_chained_unlisted_command_is_denied(ape, command):
    """The first token is allowlisted; a later one is not."""
    allowed, reason = decide(ape, command)
    assert not allowed, f"{command!r} was allowed: {reason}"
    assert "not in allowlist" in reason


# ── Constructs that hide a command inside another ────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "echo $(cat /etc/shadow)",
        "ls `rm -r /data`",
        "cat <(rm -r /data)",
        "echo hi > >(sh)",
    ],
)
def test_command_substitution_is_denied(ape, command):
    allowed, reason = decide(ape, command)
    assert not allowed, f"{command!r} was allowed: {reason}"
    assert "substitution" in reason


def test_multiline_command_is_denied(ape):
    """A newline is a command separator to a shell but whitespace to shlex."""
    allowed, reason = decide(ape, "echo hi\nrm -r /home/node")
    assert not allowed, reason
    assert "multi-line" in reason


def test_unparsable_command_is_denied(ape):
    """Fail closed: if we cannot see the programs, we do not approve them."""
    allowed, reason = decide(ape, 'echo "unbalanced')
    assert not allowed, reason
    assert "could not be parsed" in reason


# ── Existing contracts are preserved ─────────────────────────────────────────


def test_unlisted_command_is_denied(ape):
    allowed, reason = decide(ape, "rm -rf /home/node")
    assert not allowed
    assert "'rm' not in allowlist" in reason


def test_empty_command_is_denied(ape):
    for command in ("", "   "):
        allowed, reason = decide(ape, command)
        assert not allowed
        assert reason == "empty command denied"


def test_non_string_command_is_denied(ape):
    """A tool that sends argv as a list must not crash the gate."""
    allowed, reason = decide(ape, ["rm", "-rf", "/"])
    assert not allowed
    assert reason == "empty command denied"


def test_deny_patterns_still_apply(ape):
    """rm is not allowlisted, but the pattern layer must stay wired up."""
    policy = {
        "intents": {
            "ExecuteCommand": {
                "mode": "allowlist",
                "allowed": ["echo"],
                "deny_patterns": [r"secret"],
            }
        }
    }
    allowed, reason = ape.evaluate(
        "ExecuteCommand", "bash", {"command": "echo secret"}, policy
    )
    assert not allowed
    assert "deny pattern" in reason


# ── The segmenter itself ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command,expected",
    [
        ("ls", ["ls"]),
        ("ls -la", ["ls"]),
        ("a | b", ["a", "b"]),
        ("a && b || c", ["a", "b", "c"]),
        ("a; b; c", ["a", "b", "c"]),
        ("grep 'a;b' f", ["grep"]),
        ("", []),
    ],
)
def test_command_segments(ape, command, expected):
    assert ape.command_segments(command) == expected
