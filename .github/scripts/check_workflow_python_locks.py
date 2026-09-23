#!/usr/bin/env python3
"""Inventory direct workflow pip installs and require complete hashed wheel locks.

This is a deliberately narrow static shell check, not a general shell interpreter.
Dynamic install arguments, inline directory changes and unsupported wrappers fail
closed. Matrix expressions support literal axes or literal include rows only.
"""

import argparse
import itertools
import json
from pathlib import Path
import re
import shlex

import yaml


ROOT = Path(__file__).resolve().parents[2]
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
PIP = re.compile(r"pip(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?$")
PYTHON = re.compile(r"python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?$")
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}$")


class PolicyError(ValueError):
    pass


def matrix_rows(job):
    matrix = job.get("strategy", {}).get("matrix", {})
    if not isinstance(matrix, dict):
        raise PolicyError("matrix must contain literal values")
    axes = {
        key: value for key, value in matrix.items() if key not in ("include", "exclude")
    }
    include = matrix.get("include", [])
    if axes and include:
        raise PolicyError(
            "mixed matrix axes/include are unsupported; use literal include rows"
        )

    def scalar(value):
        return isinstance(value, (str, int, float, bool)) and "${{" not in str(value)

    if include:
        if not isinstance(include, list) or not all(
            isinstance(row, dict)
            and row
            and all(scalar(value) for value in row.values())
            for row in include
        ):
            raise PolicyError("matrix include must be nonempty literal mappings")
        rows = include
    else:
        if any(
            not isinstance(values, list) or not values or not all(map(scalar, values))
            for values in axes.values()
        ):
            raise PolicyError("matrix axes must be nonempty literal lists")
        if any(len(values) > 256 for values in axes.values()):
            raise PolicyError("matrix is too large")
        size = 1
        for values in axes.values():
            size *= len(values)
        if size > 256:
            raise PolicyError("matrix is too large")
        rows = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    excluded = matrix.get("exclude", [])
    if not isinstance(excluded, list) or not all(
        isinstance(row, dict) and all(scalar(value) for value in row.values())
        for row in excluded
    ):
        raise PolicyError("matrix exclude must contain literal mappings")
    rows = [
        row
        for row in rows
        if not any(all(row.get(k) == v for k, v in skip.items()) for skip in excluded)
    ]
    if not rows or len(rows) > 256:
        raise PolicyError("matrix must resolve to 1..256 rows")
    return rows


def expand(value, matrix):
    def replace(match):
        name = match[1].strip()
        if not name.startswith("matrix.") or name[7:] not in matrix:
            raise PolicyError("unsupported or missing expression: " + name)
        return str(matrix[name[7:]])

    return EXPRESSION.sub(replace, value)


def shell_commands(script):
    # YAML folded blocks are already unfolded by the YAML parser. Support Bash
    # and PowerShell line continuations without interpreting either language.
    script = script.replace("\\\r\n", "").replace("\\\n", "")
    script = script.replace("`\r\n", "").replace("`\n", "")
    lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    current, commands = [], []
    try:
        for token in lexer:
            if token and all(char in ";&|\n()" for char in token):
                if current:
                    commands.append(current)
                    current = []
            else:
                current.append(token)
    except ValueError as error:
        raise PolicyError("unsupported shell syntax: " + str(error)) from error
    if current:
        commands.append(current)
    return commands


def executable(token):
    return token.replace("\\", "/").rsplit("/", 1)[-1]


def pip_arguments(command):
    for index, token in enumerate(command):
        if not PIP.fullmatch(executable(token)):
            continue
        if any("$" in value or "`" in value for value in command[index + 1 :]):
            raise PolicyError("dynamic pip arguments are unsupported")
        if "install" not in command[index + 1 :]:
            continue
        install = command.index("install", index + 1)
        start = index
        if index >= 2 and command[index - 1] == "-m":
            start = index - 2
            if not PYTHON.fullmatch(executable(command[start])):
                raise PolicyError("pip interpreter must be a literal python executable")
        if install != index + 1:
            raise PolicyError("unsupported pip global options")
        prefix = command[:start]
        if prefix:
            raise PolicyError("unsupported pip command prefix or environment override")
        return command[install + 1 :]
    # Do not silently accept quoted commands passed to bash -c, eval or aliases.
    if any(re.search(r"\bpip(?:3)?\s+install\b", token) for token in command):
        raise PolicyError("indirect pip install is unsupported")
    return None


def existing_path(root, cwd, value, *, directory=False):
    if any(char in value for char in "$`*?[]{}") or "://" in value:
        raise PolicyError("path must be a literal repository path: " + value)
    path = Path(value)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", value):
        raise PolicyError("path must be relative to the repository: " + value)
    path = (cwd / path).resolve()
    if not path.is_relative_to(root):
        raise PolicyError("path escapes the repository: " + value)
    if not (path.is_dir() if directory else path.is_file()):
        raise PolicyError(
            "missing " + ("directory: " if directory else "lock: ") + value
        )
    return path


def check_lock(path):
    body = path.read_text(encoding="utf-8").replace("\\\n", " ")
    entries = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not entries:
        raise PolicyError("empty lock: " + str(path))
    for entry in entries:
        unhashed = re.sub(r"--hash=sha256:[0-9a-f]{64}", "", entry)
        pinned = unhashed.partition(";")[0].strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+", pinned)
            or "://" in entry
        ):
            raise PolicyError("lock must contain only exact package pins: " + str(path))
        try:
            options = [token for token in shlex.split(entry) if token.startswith("-")]
        except ValueError as error:
            raise PolicyError("invalid lock syntax: " + str(path)) from error
        if not options or not all(HASH.fullmatch(option) for option in options):
            raise PolicyError(
                "lock requires SHA256 hashes and no pip options: " + str(path)
            )


def validate_install(root, cwd, arguments):
    required = {"--require-hashes", "--only-binary=:all:"}
    if not required <= set(arguments):
        raise PolicyError(
            "pip install requires --require-hashes and --only-binary=:all:"
        )
    locks = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in required:
            index += 1
            continue
        if argument in ("-r", "--requirement"):
            index += 1
            if index == len(arguments):
                raise PolicyError("missing requirements path")
            value = arguments[index]
        elif argument.startswith("--requirement="):
            value = argument.partition("=")[2]
        elif argument.startswith("-r") and len(argument) > 2:
            value = argument[2:]
        else:
            raise PolicyError("unsupported pip argument or loose package: " + argument)
        path = existing_path(root, cwd, value)
        check_lock(path)
        locks.append(path.relative_to(root).as_posix())
        index += 1
    if not locks:
        raise PolicyError("pip install requires at least one existing -r lock")
    return locks


def audit(root=ROOT):
    root = Path(root).resolve()
    report = {"schemaVersion": 1, "commands": [], "errors": []}
    workflows = root / ".github/workflows"
    paths = sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])
    if not paths:
        report["errors"].append({"message": "no active workflow files found"})
    for path in paths:
        location = {"workflow": path.relative_to(root).as_posix()}
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or not isinstance(
                document.get("jobs"), dict
            ):
                raise PolicyError("workflow jobs must be a mapping")
            for job_id, job in document["jobs"].items():
                if not isinstance(job, dict) or not isinstance(
                    job.get("steps", []), list
                ):
                    raise PolicyError("workflow jobs/steps must be mappings/lists")
                for step_index, step in enumerate(job.get("steps", []), 1):
                    if not isinstance(step, dict):
                        raise PolicyError("workflow steps must be mappings")
                    script = step.get("run", "")
                    normalized = (
                        re.sub(r"['\"\\]", "", script)
                        if isinstance(script, str)
                        else ""
                    )
                    if not re.search(r"\bpip(?:[0-9.]+)?\b", normalized):
                        continue
                    location = {
                        "workflow": path.relative_to(root).as_posix(),
                        "job": job_id,
                        "step": step_index,
                    }
                    try:
                        rows = matrix_rows(job)
                    except PolicyError as error:
                        report["errors"].append({**location, "message": str(error)})
                        continue
                    for row in rows:
                        context = {**location, "matrix": row}
                        try:
                            commands = shell_commands(expand(script, row))
                            installs = [
                                (command, pip_arguments(command))
                                for command in commands
                            ]
                            installs = [
                                (command, args)
                                for command, args in installs
                                if args is not None
                            ]
                            if not installs:
                                continue
                            if any(
                                command[0] in ("cd", "pushd", "popd", "Set-Location")
                                for command in commands
                            ):
                                raise PolicyError(
                                    "inline directory changes are unsupported; "
                                    "use working-directory"
                                )
                            if any(
                                re.search(r"\bPIP_[A-Za-z0-9_]+\s*=", token)
                                for command in commands
                                for token in command
                            ):
                                raise PolicyError(
                                    "inline pip environment overrides are unsupported"
                                )
                            for scope in (document, job, step):
                                env = scope.get("env", {})
                                if not isinstance(env, dict) or any(
                                    str(key).upper().startswith("PIP_") for key in env
                                ):
                                    raise PolicyError(
                                        "pip environment overrides are unsupported"
                                    )
                            cwd_value = step.get(
                                "working-directory",
                                job.get("defaults", {})
                                .get("run", {})
                                .get(
                                    "working-directory",
                                    document.get("defaults", {})
                                    .get("run", {})
                                    .get("working-directory", "."),
                                ),
                            )
                            cwd = existing_path(
                                root, root, expand(cwd_value, row), directory=True
                            )
                            for command, arguments in installs:
                                entry = {
                                    **context,
                                    "command": shlex.join(command),
                                    "workingDirectory": cwd.relative_to(
                                        root
                                    ).as_posix(),
                                    "requirements": [],
                                }
                                report["commands"].append(entry)
                                try:
                                    entry["requirements"] = validate_install(
                                        root, cwd, arguments
                                    )
                                except PolicyError as error:
                                    report["errors"].append(
                                        {
                                            **context,
                                            "command": entry["command"],
                                            "message": str(error),
                                        }
                                    )
                        except (PolicyError, OSError) as error:
                            report["errors"].append({**context, "message": str(error)})
        except (PolicyError, OSError, yaml.YAMLError) as error:
            report["errors"].append({**location, "message": str(error)})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = audit(args.root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
