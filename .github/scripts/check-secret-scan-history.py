#!/usr/bin/env python3
"""Check actual Gitleaks history coverage using an isolated synthetic repository."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", default="gitleaks")
    parser.add_argument("--log-opts", required=True)
    args = parser.parse_args()
    executable = shutil.which(args.gitleaks)
    if executable is None:
        raise SystemExit("Gitleaks executable is required")
    config = Path(__file__).resolve().parents[2] / ".gitleaks.toml"
    with tempfile.TemporaryDirectory(prefix="ods-secret-history-") as directory:
        temporary = Path(directory)
        repository = temporary / "repository"
        repository.mkdir()
        hooks = temporary / "empty-hooks"
        hooks.mkdir()

        def git(*arguments):
            return subprocess.check_output(
                ["git", "-c", f"core.hooksPath={hooks}", "-C", str(repository), *arguments],
                text=True, stderr=subprocess.PIPE,
            ).strip()

        def commit(message):
            git("add", "--all")
            git("commit", "-m", message)
            return git("rev-parse", "HEAD")

        def marker(name):
            # Locally generated synthetic markers; never issued or contacted.
            payload = hashlib.sha256(("ODS history regression " + name).encode()).hexdigest()
            (repository / name).write_text('github_token="' + "ghp_" + payload[:36] + '"\n')

        def scan(name, options):
            report = temporary / (name + ".json")
            command = [executable, "detect", "--source", str(repository),
                       "--config", str(config), "--redact", "--report-format", "json",
                       "--report-path", str(report), "--exit-code", "1"]
            if options is not None:
                command.append("--log-opts=" + options)
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode not in (0, 1) or not report.is_file():
                raise SystemExit("History scan did not complete with a valid report")
            findings = json.loads(report.read_text())
            if completed.returncode != int(bool(findings)):
                raise SystemExit("History scan exit code disagrees with findings")
            return {item["Commit"] for item in findings if item["RuleID"] == "github-pat"}

        git("init", "--initial-branch=main")
        git("config", "user.name", "ODS synthetic scanner test")
        git("config", "user.email", "scanner-test@example.invalid")
        git("config", "commit.gpgsign", "false")
        (repository / "README").write_text("Synthetic history coverage test.\n")
        commit("clean base")
        git("switch", "-c", "unrelated")
        marker("unrelated.txt")
        unrelated = commit("unrelated synthetic marker")
        git("switch", "main")
        assert scan("default-all-branches", None) == {unrelated}, "Negative control did not detect unrelated branch"
        assert scan("clean-head", args.log_opts) == set(), "Unrelated branch contaminated HEAD scan"
        marker("reachable.txt")
        reachable = commit("reachable synthetic marker")
        assert scan("reachable-head", args.log_opts) == {reachable}, "Reachable marker was missed"
        (repository / "reachable.txt").unlink()
        commit("remove marker from current tree")
        assert scan("deleted-history", args.log_opts) == {reachable}, "Deleted historical marker was missed"
        git("switch", "-c", "feature")
        marker("merged.txt")
        merged = commit("feature synthetic marker")
        git("switch", "main")
        git("merge", "--no-ff", "feature", "-m", "synthetic proposed merge")
        assert scan("merged-history", args.log_opts) == {reachable, merged}, "Merged parent history was missed"
    print("PASS: unrelated branch isolated; reachable, deleted and merged history detected; default-scan negative control reproduced")


if __name__ == "__main__":
    main()
