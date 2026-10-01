"""Real sudo/PAM fixture; run only inside its disposable image, never on host."""

import json
import os
from pathlib import Path
import re
import sys
import time
import subprocess
import signal
import select

JOB = "ops-1790800000000-" + "a" * 12
PLAN = "b" * 64
STATE = Path("/var/lib/pixel-ops-broker")
if sys.argv[1:] == ["--setup"]:
    assert os.getuid() == 0 and Path("/qa/approval_terminal.py").exists()
    import pwd

    broker = pwd.getpwnam("pixel-ops-broker")
    policy = Path("/etc/pixel-ops-broker/policy.json")
    policy.write_text("{}")
    policy.chmod(0o644)
    plan = STATE / "plans" / f"{JOB}.json"
    plan.write_text(
        json.dumps({"jobId": JOB, "planHash": PLAN, "action": "fixture.read-only"})
    )
    plan.chmod(0o600)
    os.chown(plan, broker.pw_uid, broker.pw_gid)
    program = Path("/opt/pixel-ops-broker/broker.py")
    program.write_text(
        "#!/usr/bin/python3\nimport pathlib,sys\nassert sys.argv[-4:]=="
        + repr(["--approve", JOB, "--plan-hash", PLAN])
        + "\npathlib.Path('/var/lib/pixel-ops-broker/approved').write_text('exact-plan-approved')\n"
    )
    program.chmod(0o755)
    raise SystemExit()

if sys.argv[1:] == ["--crash-proof-root"]:
    assert os.getuid() == 0 and Path("/.dockerenv").exists(), "Docker fixture only"
    process = subprocess.Popen(
        ["runuser", "-u", "owner", "--", "python3", __file__, "--crash-driver"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 20)[0], "authentication deadline"
        receipt = json.loads(process.stdout.readline())
        timestamp = Path("/run/sudo/ts/1001")
        assert timestamp.exists(), "expected real authenticated sudo timestamp"
        os.kill(receipt["driver"], signal.SIGKILL)
        process.wait(timeout=5)
        helper = Path(f"/proc/{receipt['helper']}/stat")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = helper.exists() and helper.read_text().split()[2] != "Z"
            if not alive and not timestamp.exists():
                break
            time.sleep(0.04)
        assert not helper.exists() or helper.read_text().split()[2] == "Z", (
            "orphan helper"
        )
        for entry in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = entry.read_text().split()
            except FileNotFoundError:
                continue
            assert int(fields[4]) != receipt["helper"] or fields[2] == "Z", (
                "orphan private group"
            )
        assert not timestamp.exists(), (
            "authenticated sudo timestamp survived host death"
        )
        assert (
            subprocess.run(
                [
                    "runuser",
                    "-u",
                    "owner",
                    "--",
                    "python3",
                    "-c",
                    "import os,pty; pid,fd=pty.fork(); "
                    "os.execv('/usr/bin/sudo',['sudo','-n','-v']) if pid==0 else None; "
                    "_,status=os.waitpid(pid,0); os.close(fd); raise SystemExit(os.waitstatus_to_exitcode(status))",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            != 0
        ), "credential reuse"
        print("PASS real authenticated host death leaves no helper or sudo timestamp")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    raise SystemExit()

assert os.getuid() == 1001 and Path("/.dockerenv").exists(), "Docker fixture only"
from approval_terminal import ApprovalPTY


def wait(terminal, needle):
    end = time.monotonic() + 12
    while time.monotonic() < end:
        value = terminal.poll(0)
        if needle in value["output"]:
            return value["output"]
        if value["exitCode"] is not None:
            raise AssertionError(
                "fixture exited before expected phase: " + value["output"][:1024]
            )
        time.sleep(0.04)
    raise AssertionError("fixture phase timed out")


def stopped(terminal):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        value = terminal.poll(0)
        if value["exitCode"] is not None and value["cleanupConfirmed"]:
            return value
        time.sleep(0.04)
    raise AssertionError("cleanup unconfirmed")


if sys.argv[1:] == ["--crash-driver"]:
    t = ApprovalPTY("/home/owner/ods", JOB, PLAN)
    wait(t, "password for owner:")
    t.input(0, "fixture-only-password")
    wait(t, "Type exactly:")
    print(json.dumps({"driver": os.getpid(), "helper": t._process.pid}), flush=True)
    time.sleep(30)
    raise SystemExit(1)


if sys.argv[1:] == ["--passwordless"]:
    t = ApprovalPTY("/home/owner/ods", JOB, PLAN)
    try:
        value = stopped(t)
        assert (
            value["exitCode"] != 0
            and "Passwordless administrator access cannot establish" in value["output"]
        )
        assert "Review the complete" not in value["output"]
    finally:
        t.cancel()
    print("PASS passwordless sudo cannot establish approval")
    raise SystemExit()

# Wrong password must not reveal the protected plan or approve anything.
t = ApprovalPTY("/home/owner/ods", JOB, PLAN)
try:
    wait(t, "password for owner:")
    t.input(0, "wrong-fixture-password")
    output = wait(t, "Sorry, try again.")
    assert (
        "Review the complete" not in output and "wrong-fixture-password" not in output
    )
    assert t.cancel() == {"stopped": True, "cleanupConfirmed": True}
finally:
    t.cancel()
print("PASS wrong password cannot reach protected plan")

# Correct PAM password still needs exact unpredictable challenge.
t = ApprovalPTY("/home/owner/ods", JOB, PLAN)
try:
    wait(t, "password for owner:")
    t.input(0, "fixture-only-password")
    output = wait(t, "Type exactly:")
    assert "fixture-only-password" not in output and "fixture.read-only" in output
    t.input(1, "wrong challenge")
    value = stopped(t)
    assert value["exitCode"] != 0 and "nothing was approved" in value["output"]
finally:
    t.cancel()
print("PASS correct password with wrong challenge refuses approval")

# Original helper + real sudo switches to broker UID with only exact argv.
t = ApprovalPTY("/home/owner/ods", JOB, PLAN)
try:
    wait(t, "password for owner:")
    t.input(0, "fixture-only-password")
    output = wait(t, "Type exactly:")
    challenge = re.search(
        r"Type exactly: (APPROVE [a-f0-9]{12} [a-f0-9]{16})", output
    ).group(1)
    t.input(1, challenge)
    value = stopped(t)
    assert value["exitCode"] == 0 and value["cleanupConfirmed"], repr(value)
    assert "fixture-only-password" not in value["output"]
finally:
    t.cancel()
print("PASS exact native password/challenge approves; cleanup confirmed")

# A correct password never makes a different immutable hash approvable.
t = ApprovalPTY("/home/owner/ods", JOB, "c" * 64)
try:
    wait(t, "password for owner:")
    t.input(0, "fixture-only-password")
    value = stopped(t)
    assert value["exitCode"] != 0 and "does not match" in value["output"]
    assert "Type exactly:" not in value["output"]
finally:
    t.cancel()
print("PASS authenticated wrong plan hash refuses before challenge")
