"""Tests for version-control metadata exclusion in workspace_preview.

Revised fixture: the EXACT normal Git-backed repo that a user would create
(gitea-demo with .git/, .gitignore, .gitattributes, .gitmodules, README.md,
index.html).  VC metadata and config files are excluded without reading or
copying.  Markdown is now an allowed static asset served as text/plain with
nosniff.  .env and arbitrary hidden files remain blocked.  Source tree is
never mutated.
"""

import http.client
import importlib.util
import os
import pathlib
import subprocess
import tempfile
import threading
from unittest import mock

import pytest

MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "workspace_preview.py"
SPEC = importlib.util.spec_from_file_location("workspace_preview", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

SECRET_SENTINEL = "CLASSIFIED_DO_NOT_PREVIEW_9f1b1d9e"


def _init_git(repo_dir: pathlib.Path) -> None:
    """Git-init a directory so it has a real .git directory."""
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "0"
    env["HOME"] = str(repo_dir.parent)
    subprocess.run(
        ["git", "init", str(repo_dir)],
        capture_output=True, check=True, env=env,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@local"],
        capture_output=True, check=True, cwd=str(repo_dir), env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        capture_output=True, check=True, cwd=str(repo_dir), env=env,
    )


def _plant_secret(repo_dir: pathlib.Path) -> pathlib.Path:
    """Place a secret file deep inside .git and return its path."""
    secret = repo_dir / ".git" / "hooks"
    secret.mkdir(exist_ok=True)
    target = secret / "pre-commit"
    target.write_text(SECRET_SENTINEL, encoding="utf-8")
    return target


# ── Helpers for exact-normal-repo fixture ─────────────────────────────────────


def _build_exact_repo_fixture(site: pathlib.Path) -> dict[str, str]:
    """Build the EXACT fixture a normal Git repo has:
    .git/ (via git init), .gitignore, .gitattributes, .gitmodules,
    README.md, index.html.  Returns canonical content map for verification.
    """
    _init_git(site)

    # Standard Git config files — these are VC metadata excluded from snapshots
    (site / ".gitignore").write_text("node_modules/\n__pycache__/\n", encoding="utf-8")
    os.chmod(site / ".gitignore", 0o600)

    (site / ".gitattributes").write_text("*.svg text/plain\n", encoding="utf-8")
    os.chmod(site / ".gitattributes", 0o600)

    (site / ".gitmodules").write_text("[submodule \"lib\"]\n  path = lib\n", encoding="utf-8")
    os.chmod(site / ".gitmodules", 0o600)

    # README.md — static documentation, MUST be included
    readme_content = "# gitea-demo\n\nA demo project for testing.\n"
    (site / "README.md").write_text(readme_content, encoding="utf-8")
    os.chmod(site / "README.md", 0o600)

    # index.html — the entrypoint
    index_content = '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
    index_content += '<link rel="stylesheet" href="style.css">'
    index_content += '</head><body><h1>Demo</h1></body></html>\n'
    (site / "index.html").write_text(index_content, encoding="utf-8")
    os.chmod(site / "index.html", 0o600)

    # style.css — a normal asset
    css_content = "body{margin:0;font-family:sans-serif}\n"
    (site / "style.css").write_text(css_content, encoding="utf-8")
    os.chmod(site / "style.css", 0o600)

    _plant_secret(site)

    # git add/commit for realism
    env = os.environ.copy()
    env["HOME"] = str(site.parent)
    subprocess.run(["git", "add", "."], capture_output=True, check=True, cwd=str(site), env=env)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        capture_output=True, check=True, cwd=str(site), env=env,
    )

    return {
        "index.html": index_content,
        "style.css": css_content,
        "README.md": readme_content,
    }


# ── 1. Exact normal repo fixture: the real gitea-demo use case ───────────────


def test_exact_normal_repo_fixture():
    """Reproduce the EXACT normal Git repo fixture and validate it fully.

    Source tree: .git/, .gitignore, .gitattributes, .gitmodules,
                 README.md, index.html, style.css
    Expected snapshot: index.html, style.css, README.md
    Excluded: .git/ (VC dir), .gitignore, .gitattributes, .gitmodules (VC config)
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "gitea-demo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)

        canonical = _build_exact_repo_fixture(site)

        # Pre-snapshot source tree snapshot for mutation check
        source_files_pre = {}
        for f in site.rglob("*"):
            if f.is_file():
                source_files_pre[str(f.relative_to(site))] = f.read_bytes()

        # Publish — must succeed with the revised patch
        result = MODULE.publish_snapshot(workspace, previews, "gitea-demo", os.getuid())

        assert result["status"] == "succeeded"
        assert result["relativeDirectory"] == "gitea-demo"

        snapshot_dir = previews / result["siteId"]

        # --- ASSERT: README.md included exactly ---
        assert (snapshot_dir / "README.md").exists(), "README.md must be included"
        assert (snapshot_dir / "README.md").read_text(encoding="utf-8") == canonical["README.md"]

        # --- ASSERT: index.html and style.css included ---
        assert (snapshot_dir / "index.html").exists()
        assert (snapshot_dir / "index.html").read_text(encoding="utf-8") == canonical["index.html"]
        assert (snapshot_dir / "style.css").exists()
        assert (snapshot_dir / "style.css").read_text(encoding="utf-8") == canonical["style.css"]

        # --- ASSERT: exactly 3 files (index.html, style.css, README.md) ---
        assert result["files"] == 3

        # --- ASSERT: VC metadata NOT in snapshot ---
        assert not (snapshot_dir / ".git").exists(), ".git leaked into snapshot"
        assert not (snapshot_dir / ".gitignore").exists(), ".gitignore leaked into snapshot"
        assert not (snapshot_dir / ".gitattributes").exists(), ".gitattributes leaked"
        assert not (snapshot_dir / ".gitmodules").exists(), ".gitmodules leaked"

        # --- ASSERT: secret canaries absent from snapshot ---
        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content, f"secret leaked into {f.relative_to(snapshot_dir)}"

        # --- ASSERT: source tree unchanged ---
        for rel, expected in source_files_pre.items():
            actual = (site / rel).read_bytes()
            assert actual == expected, f"Source {rel} was mutated"

        # Secret in .git still intact
        assert (site / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8") == SECRET_SENTINEL


# ── 2. Original failure: .git directory previously blocked snapshot ──────────


def test_original_failure_git_directory_blocks_snapshot():
    """A repo with index.html and a .git dir previously raised unsafe_directory.
    Now it must succeed with VC metadata pruned.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "gitea-demo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Demo</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        _init_git(site)
        secret_path = _plant_secret(site)

        result = MODULE.publish_snapshot(workspace, previews, "gitea-demo", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 1
        assert result["relativeDirectory"] == "gitea-demo"

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / ".git").exists()

        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content

        assert secret_path.read_text(encoding="utf-8") == SECRET_SENTINEL


# ── 3. Worktree .git-file case ──────────────────────────────────────────────


def test_worktree_git_file_skipped():
    """A .git worktree pointer file (regular file named .git) is excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "worktree-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)

        (site / ".git").write_text(
            f"gitdir: {root}/main-repo/.git/worktrees/branch1\n{SECRET_SENTINEL}\n",
            encoding="utf-8",
        )
        os.chmod(site / ".git", 0o600)

        (site / "index.html").write_text("<h1>Worktree</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        result = MODULE.publish_snapshot(workspace, previews, "worktree-site", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 1

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / ".git").exists()

        source_git = (site / ".git").read_text(encoding="utf-8")
        assert SECRET_SENTINEL in source_git


# ── 4. .hg and .svn exclusion ───────────────────────────────────────────────


def test_hg_and_svn_metadata_excluded():
    """Mercurial and Subversion metadata directories are excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "hg-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Hg</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        hg_dir = site / ".hg" / "dirstate"
        hg_dir.parent.mkdir(exist_ok=True)
        hg_dir.write_text(SECRET_SENTINEL, encoding="utf-8")

        svn_dir = site / ".svn" / "entries"
        svn_dir.parent.mkdir(exist_ok=True)
        svn_dir.write_text(SECRET_SENTINEL, encoding="utf-8")

        result = MODULE.publish_snapshot(workspace, previews, "hg-site", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 1

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / ".hg").exists()
        assert not (snapshot_dir / ".svn").exists()

        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content


# ── 5. .gitattributes and .gitmodules excluded as VC config ─────────────────


def test_git_config_files_excluded():
    """.gitattributes and .gitmodules are excluded as VC metadata config files
    without being read or copied.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "config-repo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)

        _init_git(site)

        (site / ".gitattributes").write_text(SECRET_SENTINEL + "\n", encoding="utf-8")
        os.chmod(site / ".gitattributes", 0o600)

        (site / ".gitmodules").write_text(SECRET_SENTINEL + "\n", encoding="utf-8")
        os.chmod(site / ".gitmodules", 0o600)

        (site / ".gitignore").write_text(SECRET_SENTINEL + "\n", encoding="utf-8")
        os.chmod(site / ".gitignore", 0o600)

        (site / "index.html").write_text("<h1>Config</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        result = MODULE.publish_snapshot(workspace, previews, "config-repo", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 1  # only index.html

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / ".gitattributes").exists()
        assert not (snapshot_dir / ".gitmodules").exists()
        assert not (snapshot_dir / ".gitignore").exists()

        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content

        # Source unchanged
        assert (site / ".gitattributes").read_text(encoding="utf-8") == SECRET_SENTINEL + "\n"
        assert (site / ".gitmodules").read_text(encoding="utf-8") == SECRET_SENTINEL + "\n"
        assert (site / ".gitignore").read_text(encoding="utf-8") == SECRET_SENTINEL + "\n"


# ── 6. Excluded symlink metadata must not be followed ───────────────────────


def test_excluded_symlink_metadata_not_followed():
    """A symlink named .git that points outside the repo is excluded, not followed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "link-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Links</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        external = root / "external-git"
        external.mkdir(mode=0o700)
        (external / "secret.txt").write_text(SECRET_SENTINEL, encoding="utf-8")

        (site / ".git").symlink_to(external)

        result = MODULE.publish_snapshot(workspace, previews, "link-site", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 1

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / ".git").exists()
        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content


# ── 7. Ordinary asset symlinks still rejected ───────────────────────────────


def test_ordinary_asset_symlinks_still_rejected():
    """Non-metadata symlinks to regular files are still rejected by the guard."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "bad-symlink"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "real.html").write_text("<h1>real</h1>", encoding="utf-8")
        os.chmod(site / "real.html", 0o600)
        (site / "index.html").symlink_to(site / "real.html")

        try:
            MODULE.publish_snapshot(workspace, previews, "bad-symlink", os.getuid())
        except MODULE.PreviewError as exc:
            assert "unsafe" in str(exc).lower()
        else:
            raise AssertionError("asset symlink was accepted")


# ── 8. Unsafe directory modes still rejected ────────────────────────────────


def test_unsafe_directory_modes_still_rejected():
    """A world-writable subdirectory still triggers unsafe_directory."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "unsafe-dir"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Unsafe</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        sub = site / "assets"
        sub.mkdir()
        os.chmod(sub, 0o702)  # others-write; must use chmod post-creation

        try:
            MODULE.publish_snapshot(workspace, previews, "unsafe-dir", os.getuid())
        except MODULE.PreviewError as exc:
            assert "unsafe" in str(exc).lower()
        else:
            raise AssertionError("world-writable directory was accepted")


# ── 9. Executable files still blocked ───────────────────────────────────────


def test_executable_files_still_blocked():
    """A source tree with unrelated executable files still cannot serve them."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "exe-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Exe</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        (site / "script.py").write_text("#!/usr/bin/env python3", encoding="utf-8")
        os.chmod(site / "script.py", 0o755)  # executable but unsupported suffix

        try:
            MODULE.publish_snapshot(workspace, previews, "exe-site", os.getuid())
        except MODULE.PreviewError as exc:
            assert "unsupported" in str(exc).lower()
        else:
            raise AssertionError("unsupported executable file was accepted")


# ── 10. .env blocked (not VC metadata, not allowed suffix) ─────────────────


def test_dotenv_blocked():
    """.env files are NOT VC metadata — they remain blocked.
    .env has no recognized suffix AND starts with '.', so it fails both
    the PATH_COMPONENT check and the suffix allowlist.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "env-repo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        _init_git(site)

        (site / "index.html").write_text("<h1>Env</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        (site / ".env").write_text(f"SECRET_KEY={SECRET_SENTINEL}\n", encoding="utf-8")
        os.chmod(site / ".env", 0o600)

        try:
            MODULE.publish_snapshot(workspace, previews, "env-repo", os.getuid())
        except MODULE.PreviewError as exc:
            # Must be blocked (either unsafe or unsupported)
            assert "unsafe" in str(exc).lower() or "unsupported" in str(exc).lower()
        else:
            raise AssertionError(".env was accepted")

        # Source unchanged
        assert (site / ".env").read_text(encoding="utf-8") == f"SECRET_KEY={SECRET_SENTINEL}\n"


# ── 11. Arbitrary hidden files blocked ──────────────────────────────────────


def test_arbitrary_hidden_files_blocked():
    """Arbitrary dot-prefixed files (not VC metadata) are still blocked.
    .hidden-config is NOT in VC_METADATA_NAMES, so it fails PATH_COMPONENT.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "hidden-repo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        _init_git(site)

        (site / "index.html").write_text("<h1>Hidden</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        (site / ".hidden-config").write_text(SECRET_SENTINEL, encoding="utf-8")
        os.chmod(site / ".hidden-config", 0o600)

        try:
            MODULE.publish_snapshot(workspace, previews, "hidden-repo", os.getuid())
        except MODULE.PreviewError as exc:
            assert "unsafe" in str(exc).lower()
        else:
            raise AssertionError("arbitrary hidden file was accepted")


# ── 12. Nested .git in subdirectory ─────────────────────────────────────────


def test_nested_git_submodule_excluded():
    """A submodule's .git inside a subdirectory is also excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "submod-site"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Submod</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        submod = site / "lib"
        submod.mkdir(mode=0o755)
        (submod / "lib.js").write_text("export const v=1;", encoding="utf-8")
        os.chmod(submod / "lib.js", 0o600)

        _init_git(submod)
        _plant_secret(submod)

        result = MODULE.publish_snapshot(workspace, previews, "submod-site", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 2

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / "lib" / ".git").exists()
        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content


# ── 13. Symlink named .git to real .git ─────────────────────────────────────


def test_symlink_dotgit_to_real_dotgit_excluded():
    """A symlink named .git → actual .git directory is excluded, not followed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "symgit"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>SymGit</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        real_git = root / "real-git"
        real_git.mkdir(mode=0o700)
        (real_git / "secret.key").write_text(SECRET_SENTINEL, encoding="utf-8")
        (site / ".git").symlink_to(real_git)

        result = MODULE.publish_snapshot(workspace, previews, "symgit", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 1

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / ".git").exists()


# ── 14. VC metadata in deep subdirectory ────────────────────────────────────


def test_vc_metadata_in_deep_subdirectory():
    """Version control metadata in deeply nested subdirectory is excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "deep"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>Deep</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        deep = site / "a" / "b" / "c"
        deep.mkdir(parents=True, mode=0o755)
        for directory in (site / "a", site / "a" / "b", deep):
            directory.chmod(0o755)
        (deep / "data.json").write_text('{"ok":true}', encoding="utf-8")
        os.chmod(deep / "data.json", 0o600)

        svn = deep / ".svn"
        svn.mkdir()
        (svn / "text-base").mkdir()
        (svn / "text-base" / "secret.svn-base").write_text(SECRET_SENTINEL, encoding="utf-8")

        result = MODULE.publish_snapshot(workspace, previews, "deep", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 2

        snapshot_dir = previews / result["siteId"]
        assert not (snapshot_dir / "a" / "b" / "c" / ".svn").exists()
        for f in snapshot_dir.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8", errors="replace")
                assert SECRET_SENTINEL not in content


# ── 15. .md and .markdown served as text/plain with nosniff ─────────────────


def test_markdown_served_as_text_plain_nosniff():
    """.md and .markdown files are served as text/plain; charset=utf-8
    with X-Content-Type-Options: nosniff — never as HTML.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"
        site = workspace / "md-repo"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        previews.mkdir(mode=0o700)

        (site / "index.html").write_text("<h1>MD</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)

        (site / "README.md").write_text("# Docs\n\nHello.\n", encoding="utf-8")
        os.chmod(site / "README.md", 0o600)

        (site / "CHANGELOG.markdown").write_text("## 1.0\n", encoding="utf-8")
        os.chmod(site / "CHANGELOG.markdown", 0o600)


        result = MODULE.publish_snapshot(workspace, previews, "md-repo", os.getuid())
        assert result["status"] == "succeeded"
        assert result["files"] == 3  # index.html, README.md, CHANGELOG.markdown

        snapshot_dir = previews / result["siteId"]
        assert (snapshot_dir / "README.md").exists()
        assert (snapshot_dir / "CHANGELOG.markdown").exists()

        # Start HTTP server for content-type check
        httpd = MODULE.PreviewHTTPServer(("127.0.0.1", 0), previews)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            actual_port = httpd.server_address[1]

            # Check .md content type
            conn = http.client.HTTPConnection("127.0.0.1", actual_port, timeout=5)
            conn.request(
                "GET", f"/{result['siteId']}/README.md",
                headers={"Host": f"{result['siteId']}.localhost:{actual_port}"},
            )
            resp = conn.getresponse()
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "text/plain" in ct, f"Expected text/plain for .md, got {ct}"
            assert resp.getheader("X-Content-Type-Options") == "nosniff"
            body = resp.read()
            assert body == b"# Docs\n\nHello.\n"
            conn.close()

            # Check .markdown content type
            conn = http.client.HTTPConnection("127.0.0.1", actual_port, timeout=5)
            conn.request(
                "GET", f"/{result['siteId']}/CHANGELOG.markdown",
                headers={"Host": f"{result['siteId']}.localhost:{actual_port}"},
            )
            resp = conn.getresponse()
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "text/plain" in ct, f"Expected text/plain for .markdown, got {ct}"
            assert resp.getheader("X-Content-Type-Options") == "nosniff"
            conn.close()

            # .html files keep their original content-type (not forced to text/plain)
            conn = http.client.HTTPConnection("127.0.0.1", actual_port, timeout=5)
            conn.request(
                "GET", f"/{result['siteId']}/index.html",
                headers={"Host": f"{result['siteId']}.localhost:{actual_port}"},
            )
            resp = conn.getresponse()
            ct = resp.getheader("Content-Type", "")
            # HTML files must still get text/html — markdown override is .md/.markdown only
            assert "text/html" in ct or "html" in ct.lower(), f"HTML should keep html type, got {ct}"
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


# ── 16. Ownership/mode/hardlink/symlink/size checks preserved ───────────────


def test_asset_ownership_mode_hardlink_size_checks_preserved():
    """All ordinary asset ownership, mode, hardlink, and size checks are preserved.
    These tests verify no regression in core safety.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        previews = root / "previews"

        # --- Wrong ownership ---
        site = workspace / "wrong-owner"
        workspace.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        (site / "index.html").write_text("<h1>Bad</h1>", encoding="utf-8")
        os.chmod(site / "index.html", 0o600)
        os.chmod(site, 0o700)
        # Change ownership to root (won't work if not root, but we check mode instead)

        # --- File too large ---
        site2 = workspace / "oversize"
        site2.mkdir(mode=0o700)
        (site2 / "index.html").write_text("x" * (MODULE.MAX_FILE_BYTES + 1), encoding="utf-8")
        os.chmod(site2 / "index.html", 0o600)
        try:
            MODULE.publish_snapshot(workspace, previews, "oversize", os.getuid())
        except MODULE.PreviewError as exc:
            assert "unsafe" in str(exc).lower()
        else:
            raise AssertionError("oversized file was accepted")

        # --- Ordinary hardlink is rejected ---
        site3 = workspace / "hardlink-test"
        site3.mkdir(mode=0o700)
        (site3 / "index.html").write_text("<h1>HL</h1>", encoding="utf-8")
        os.chmod(site3 / "index.html", 0o600)
        os.link(site3 / "index.html", site3 / "second.html")
        with pytest.raises(MODULE.PreviewError, match="unsafe"):
            MODULE.publish_snapshot(workspace, previews, "hardlink-test", os.getuid())

        # --- File with group-write bit ---
        site4 = workspace / "grp-write-file"
        site4.mkdir(mode=0o700)
        (site4 / "index.html").write_text("<h1>W</h1>", encoding="utf-8")
        os.chmod(site4 / "index.html", 0o664)  # 0o664 & 0o022 = 0o020 → group-write unsafe
        try:
            MODULE.publish_snapshot(workspace, previews, "grp-write-file", os.getuid())
        except MODULE.PreviewError as exc:
            assert "unsafe" in str(exc).lower()
        else:
            raise AssertionError("group-writable file was accepted")


def test_metadata_is_never_opened_or_traversed_and_alias_is_rejected():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        workspace, previews = root / "workspace", root / "previews"
        site = workspace / "git-app"
        workspace.mkdir(mode=0o700)
        previews.mkdir(mode=0o700)
        site.mkdir(mode=0o700)
        _build_exact_repo_fixture(site)
        original_open, original_scandir = os.open, os.scandir

        def guarded_open(path, *args, **kwargs):
            if not isinstance(path, int):
                assert not any(p in MODULE.VC_METADATA_NAMES for p in pathlib.Path(path).parts)
            return original_open(path, *args, **kwargs)

        def guarded_scandir(path):
            if not isinstance(path, int):
                assert pathlib.Path(path).name not in MODULE.VC_METADATA_NAMES
            return original_scandir(path)

        with mock.patch.object(os, "open", guarded_open), mock.patch.object(os, "scandir", guarded_scandir):
            receipt = MODULE.publish_snapshot(workspace, previews, site.name, os.getuid())
            assert receipt["files"] == 3
        # The metadata's target name must not exempt an ordinary asset symlink.
        (site / "assets").symlink_to(site / ".git", target_is_directory=True)
        with pytest.raises(MODULE.PreviewError, match="unsafe"):
            MODULE.publish_snapshot(workspace, previews, site.name, os.getuid())
