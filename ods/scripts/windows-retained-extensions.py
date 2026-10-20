#!/usr/bin/env python3
"""Native Windows entry point to the existing Compose user-recipe resolver.

Like compose-cache-policy.py, load installed ODS policy, never recipe code.
The complete Python body has one explicit heredoc boundary; Bash/WSL is not
required. Windows retains authority over its core and bundled service plan.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys


def resolve(install_dir, flags, *, tier="1", backend="none", gpu_count=1, mode="local"):
    root = Path(install_dir).resolve()
    if not isinstance(flags, list) or flags[:2] != ["--env-file", ".env"]:
        raise ValueError("Expected the installer's explicit .env Compose selection")
    files = []
    for index in range(2, len(flags), 2):
        if flags[index] != "-f" or index + 1 >= len(flags):
            raise ValueError("Invalid installer Compose file arguments")
        name = flags[index + 1]
        if not isinstance(name, str) or any(c in name for c in "\x00\r\n\\"):
            raise ValueError("Invalid installer Compose path")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or not (root / path).is_file():
            raise ValueError("Installer Compose file must exist within the installation")
        (root / path).resolve().relative_to(root)
        files.append(name)
    if not files:
        raise ValueError("Installer Compose selection is empty")

    resolver = root / "scripts/resolve-compose-stack.sh"
    source = resolver.read_text(encoding="utf-8")
    start, end = "<<'PY'\n", "\nPY\n"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Installed Compose resolver has an ambiguous Python boundary")
    body = source.split(start, 1)[1].split(end, 1)[0]
    spec = importlib.util.spec_from_file_location("ods_compose_policy", root / "scripts/compose-cache-policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    def validate_selected(selected):
        return policy.validate_flags(root, ["--env-file", ".env"] +
                                     [arg for name in selected for arg in ("-f", name)])
    namespace = {"__name__": "__main__", "__file__": str(resolver),
                 "_selected_stack": files, "_validate_selected": validate_selected}
    original_argv = sys.argv
    output, diagnostics = io.StringIO(), io.StringIO()
    try:
        sys.argv = [str(resolver), str(root), str(tier), str(backend), "", "false",
                    "false", str(gpu_count), str(mode), "", str(root), "false"]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
            exec(compile(body, str(resolver), "exec"), namespace)
    except (Exception, SystemExit) as exc:
        raise ValueError("Retained extension resolution failed: " +
                         (diagnostics.getvalue().strip() or str(exc))) from exc
    finally:
        sys.argv = original_argv
    selected = json.loads(output.getvalue())
    if not isinstance(selected, list) or selected[:len(files)] != files:
        raise ValueError("Retained extension resolution changed the installer selection")
    selected = [Path(name).as_posix() for name in selected]
    result = ["--env-file", ".env"] + [arg for name in selected for arg in ("-f", name)]
    return policy.validate_flags(root, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--tier", default="1")
    parser.add_argument("--backend", default="none")
    parser.add_argument("--gpu-count", type=int, default=1)
    parser.add_argument("--mode", default="local")
    args = parser.parse_args()
    try:
        flags = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result = resolve(args.install_dir, flags, tier=args.tier, backend=args.backend,
                         gpu_count=args.gpu_count, mode=args.mode)
    except (ValueError, OSError, ImportError) as exc:
        print(f"Retained extension Compose selection requires review: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
