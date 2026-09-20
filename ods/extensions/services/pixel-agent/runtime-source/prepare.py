"""Prepare exact managed runtime source without changing an existing checkout.

This developer build input is not an installed-runtime attestation. It never
touches gateway configuration, services, credentials, or the source repository.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent


def load_lock(directory):
    raw = (directory / "source-lock.json").read_bytes()
    lock = json.loads(raw)
    fields = {
        "schemaVersion",
        "upstreamCommit",
        "sourceTree",
        "runtimeVersion",
        "nodeVersion",
        "packageManager",
        "patches",
    }
    if (
        not isinstance(lock, dict)
        or set(lock) != fields
        or type(lock["schemaVersion"]) is not int
        or lock["schemaVersion"] != 1
        or any(
            not isinstance(lock[k], str) or not re.fullmatch(r"[a-f0-9]{40}", lock[k])
            for k in ("upstreamCommit", "sourceTree")
        )
        or not isinstance(lock["patches"], list)
        or not lock["patches"]
    ):
        raise ValueError("invalid-source-lock")
    patches = []
    for item in lock["patches"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"file", "sha256"}
            or not isinstance(item["file"], str)
            or not re.fullmatch(r"[a-zA-Z0-9.-]+\.patch", item["file"])
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
        ):
            raise ValueError("invalid-source-patch")
        path = directory / item["file"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe-source-patch")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("source-patch-drift")
        patches.append(content)
    return lock, hashlib.sha256(raw).hexdigest(), patches


def validate_link_chains(links):
    for link in links:
        pending = list(link.parts)
        resolved = []
        traversals = 0
        while pending:
            part = pending.pop(0)
            if part == "..":
                if not resolved:
                    raise ValueError("escaping-source-link-chain")
                resolved.pop()
            else:
                resolved.append(part)
            key = PurePosixPath(*resolved)
            if key in links:
                traversals += 1
                if traversals > 40:
                    raise ValueError("cyclic-or-excessive-source-link-chain")
                resolved.pop()
                pending = list(links[key].parts) + pending


def extract_source(archive_path, destination):
    """Reject special files and escaping links before extracting any content."""
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        names = set()
        links = {}
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or ".git" in path.parts
                or "\\" in member.name
                or member.name in names
                or not (member.isdir() or member.isfile() or member.issym())
            ):
                raise ValueError("unsafe-source-archive")
            names.add(member.name)
            if member.issym():
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or "\\" in member.linkname:
                    raise ValueError("unsafe-source-link")
                depth = len(path.parent.parts)
                for part in target.parts:
                    depth += -1 if part == ".." else 1
                    if depth < 0:
                        raise ValueError("escaping-source-link")
                links[path] = target
        # Lexically internal links can still escape through another link and
        # a later '..'. Resolve the archive's link graph before writing files.
        validate_link_chains(links)
        for member in members:
            path = PurePosixPath(member.name)
            if any(parent in links for parent in path.parents):
                raise ValueError("source-link-parent")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif member.issym():
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                target.chmod(0o700 if member.mode & 0o111 else 0o600)


def prepare(upstream, parent, *, inputs=HERE):
    if os.name != "posix":
        raise ValueError("source-preparation-requires-posix")
    lock, lock_sha, patches = load_lock(inputs)
    upstream = upstream.resolve(strict=True)
    parent = parent.resolve(strict=True)
    # Only read committed objects from the supplied repository. Never use its
    # index, hooks, worktree files, checkout attributes, or global Git settings.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(parent),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    commit = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", lock["upstreamCommit"] + "^{commit}"],
        env=env,
        text=True,
    ).strip()
    if commit != lock["upstreamCommit"]:
        raise ValueError("upstream-commit-mismatch")
    stage = Path(tempfile.mkdtemp(prefix="ods-managed-runtime-", dir=parent))
    stage.chmod(0o700)
    root = stage / "source"
    root.mkdir(mode=0o700)
    archive = stage / "upstream.tar"
    subprocess.run(
        [
            "git",
            "-C",
            str(upstream),
            "archive",
            "--format=tar",
            "--output=" + str(archive),
            commit,
        ],
        env=env,
        check=True,
    )
    extract_source(archive, root)
    env["HOME"] = str(stage)

    def git(*args, content=None):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-C",
                    str(root),
                    *args,
                ],
                env=env,
                input=content,
            )
            .decode()
            .strip()
        )

    git("init", "--quiet")
    # The upstream tree includes intentionally tracked CRLF files. A normal
    # git add re-applies its text attributes and silently changes those blobs.
    # These private index attributes preserve the archive's exact bytes only;
    # the source .gitattributes and caller's repository are never modified.
    (root / ".git/info/attributes").write_text("* -text -filter -ident\n")
    git("add", "--force", "--all")
    # Archive filters must not silently remove or rewrite committed inputs.
    expected_base = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", commit + "^{tree}"],
        env=env,
        text=True,
    ).strip()
    if git("write-tree") != expected_base:
        raise ValueError("upstream-archive-tree-mismatch")
    for content in patches:
        git("apply", "--index", "--check", "-", content=content)
        git("apply", "--index", "-", content=content)
    tree = git("write-tree")
    if tree != lock["sourceTree"]:
        raise ValueError("patched-source-tree-mismatch")
    package = json.loads((root / "package.json").read_bytes())
    if (
        package["version"] != lock["runtimeVersion"]
        or package["packageManager"].split("+", 1)[0] != lock["packageManager"]
    ):
        raise ValueError("runtime-package-mismatch")
    receipt = {
        "schemaVersion": 1,
        "upstreamCommit": commit,
        "sourceTree": tree,
        "sourceLockSha256": lock_sha,
        "source": str(root),
        "built": False,
        "installed": False,
    }
    with (stage / "source-receipt.json").open("x") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-checkout", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.upstream_checkout, args.output_parent), indent=2))


if __name__ == "__main__":
    main()
