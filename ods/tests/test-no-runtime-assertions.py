"""Product Python must not use `assert` for runtime checks.

`python -O` (and `PYTHONOPTIMIZE=1`) strips every assert statement. A module
that relies on one for a runtime guarantee therefore behaves differently in an
optimized interpreter, and the failure is worse than the check it replaced:

    if response is None:
        assert last_error is not None
        raise last_error          # -> `raise None` -> TypeError, not the 502

Under -O the caller gets "exceptions must derive from BaseException" instead of
the diagnostic the code meant to produce. The same applies to assertions that
merely narrow a type for mypy: they document an invariant the optimized
interpreter does not enforce.

Assertions inside the test suites are fine — pytest depends on them, and tests
are never run under -O.
"""

import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache"}

# Files whose assertions are already being addressed in their own change.
# Each entry is only *permitted* to contain assertions, never required to, so
# an entry left behind after its fix lands is harmless — delete it when you
# notice it.
ALLOWED = {
    # covered by issue #6088
    "extensions/services/dashboard-api/routers/extensions.py",
    # covered by issue #6090
    "extensions/services/dashboard-api/routers/magic_link.py",
}


def _is_test_path(relative_path):
    """Tests may assert freely; they are never run under -O."""
    return any("test" in part.lower() for part in relative_path.split(os.sep))


def iter_product_sources():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            absolute = os.path.join(dirpath, filename)
            relative = os.path.relpath(absolute, REPO_ROOT)
            if _is_test_path(relative):
                continue
            yield relative, absolute


def find_assertions():
    offenders = []
    for relative, absolute in iter_product_sources():
        with open(absolute, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        try:
            tree = ast.parse(source, filename=absolute)
        except SyntaxError:
            # Not importable by this interpreter (a newer syntax, a template).
            # Type checking and the unit suites are responsible for those.
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                offenders.append((relative, node.lineno))
    return offenders


def test_no_runtime_assertions_in_product_python():
    offenders = [
        (path, line)
        for path, line in find_assertions()
        if path.replace(os.sep, "/") not in ALLOWED
    ]
    if offenders:
        listed = "\n".join(f"  {path}:{line}" for path, line in sorted(offenders))
        raise AssertionError(
            "`assert` is stripped by `python -O`, so these runtime checks do not "
            "hold in an optimized interpreter. Raise an explicit error instead:\n"
            f"{listed}"
        )


if __name__ == "__main__":
    try:
        test_no_runtime_assertions_in_product_python()
    except AssertionError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
    print("No runtime assertions in product Python.")
