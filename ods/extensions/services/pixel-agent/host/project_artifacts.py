"""Validate build output bytes before any future workspace import/publication."""
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import threading

from project_runtime import stage_arguments
from project_snapshot import _component

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024
COMPONENT = re.compile(r"(?!__ods_)(?!__pycache__$)[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")


class InvalidProjectArtifacts(ValueError):
    pass


class MissingProjectOutput(InvalidProjectArtifacts):
    """The stopped build exists but Docker confirms its requested path is absent."""
    pass


def import_artifacts(workspace: str, project: str, job: str, artifacts: dict) -> str:
    """Import validated output create-only; never replace project source files.

    Requires the configured owner process and a controller-owned job identity.
    Incomplete output stays under a private pending directory on error.
    """
    if not isinstance(project, str) or not 0 < len(project) <= 1024:
        raise InvalidProjectArtifacts("invalid project path")
    parts = project.split("/")
    if len(parts) > 8 or not all(_component(part) for part in parts):
        raise InvalidProjectArtifacts("invalid project path")
    if not re.fullmatch(r"ods-project-[a-f0-9]{24}", job):
        raise InvalidProjectArtifacts("invalid job identity")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(workspace, flags)
    handles = [directory]
    suffix = job.removeprefix("ods-project-")
    try:
        for part in parts:
            directory = os.open(part, flags, dir_fd=directory)
            handles.append(directory)
        if os.fstat(directory).st_uid != os.getuid():
            raise InvalidProjectArtifacts("project owner mismatch")
        try:
            os.mkdir("ods-builds", 0o700, dir_fd=directory)
        except FileExistsError:
            pass
        directory = os.open("ods-builds", flags, dir_fd=directory)
        handles.append(directory)
        if os.fstat(directory).st_uid != os.getuid():
            raise InvalidProjectArtifacts("output owner mismatch")
        os.mkdir(suffix, 0o700, dir_fd=directory)  # Existing generations never overwritten.
        generation = os.open(suffix, flags, dir_fd=directory)
        handles.append(generation)
        os.mkdir("pending", 0o700, dir_fd=generation)
        output = os.open("pending", flags, dir_fd=generation)
        handles.append(output)
        for name, data in artifacts["files"].items():
            components = name.split("/")
            if not all(COMPONENT.fullmatch(part) for part in components):
                raise InvalidProjectArtifacts("invalid imported artifact path")
            parent = os.dup(output)
            try:
                for component in components[:-1]:
                    try:
                        os.mkdir(component, 0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                    child = os.open(component, flags, dir_fd=parent)
                    os.close(parent)
                    parent = child
                fd = os.open(components[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(parent)
        os.rename("pending", "site", src_dir_fd=generation, dst_dir_fd=generation)
        return project + "/ods-builds/" + suffix + "/site"
    finally:
        for handle in reversed(handles):
            os.close(handle)


def decode_artifacts(payload: bytes) -> dict:
    if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE:
        raise InvalidProjectArtifacts("artifact archive too large")
    files, seen, folded, spelling, total = {}, set(), set(), {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for count, member in enumerate(archive, 1):
                if count > 512:
                    raise InvalidProjectArtifacts("too many archive entries")
                name = member.name
                if name in (".", "./") and member.isdir():
                    continue
                if name.startswith("./"):
                    name = name[2:]
                if member.isdir():
                    name = name.rstrip("/")
                parts = name.split("/")
                if len(parts) > 16 or not all(COMPONENT.fullmatch(p) for p in parts):
                    raise InvalidProjectArtifacts("invalid artifact path")
                for i in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:i])
                    previous = spelling.setdefault(prefix.casefold(), prefix)
                    if previous != prefix:
                        raise InvalidProjectArtifacts("case-ambiguous artifact path")
                if name.casefold() in folded:
                    raise InvalidProjectArtifacts("duplicate artifact path")
                seen.add(name)
                folded.add(name.casefold())
                if any("/".join(parts[:i]) in files for i in range(1, len(parts))):
                    raise InvalidProjectArtifacts("artifact parent is a file")
                if member.isdir():
                    continue
                if (not member.isfile() or member.sparse is not None
                        or member.size < 0 or member.size > MAX_FILE or len(files) >= 128):
                    raise InvalidProjectArtifacts("unsupported artifact type or size")
                if any(p.startswith(name + "/") for p in seen):
                    raise InvalidProjectArtifacts("artifact replaces a directory")
                total += member.size
                if total > MAX_TOTAL:
                    raise InvalidProjectArtifacts("artifact total too large")
                data = archive.extractfile(member).read(MAX_FILE + 1)
                if len(data) != member.size:
                    raise InvalidProjectArtifacts("incomplete artifact")
                files[name] = data
    except (tarfile.TarError, EOFError) as error:
        raise InvalidProjectArtifacts("invalid artifact archive") from error
    if not files:
        raise InvalidProjectArtifacts("empty artifact output")
    manifest = [{"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for path, body in sorted(files.items())]
    digest = hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()
    return {"files": files, "manifest": manifest, "sha256": digest, "bytes": total}


def collect_artifacts(image: str, job: str, output_directory: str) -> dict:
    stage_arguments(image, job, "build")  # validate internal identity arguments
    if not isinstance(output_directory, str) or not COMPONENT.fullmatch(output_directory):
        raise InvalidProjectArtifacts("select one project output directory")
    name = job + "-build"
    observed = subprocess.run(["docker", "inspect", name], capture_output=True, check=True, timeout=15)
    container = json.loads(observed.stdout)[0]
    if (container["State"]["Running"] or container["State"]["ExitCode"] != 0
            or container["Config"]["Image"] != image
            or container["Config"].get("Labels", {}).get("org.osmantic.ods.project-job") != job
            or container["HostConfig"]["NetworkMode"] != "none"):
        raise InvalidProjectArtifacts("build completion identity not confirmed")
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(["docker", "cp", name + ":/home/node/" + output_directory + "/.", "-"],
                                   stdout=subprocess.PIPE, stderr=errors)
        watchdog = threading.Timer(30, process.kill)
        watchdog.start()
        try:
            payload = process.stdout.read(MAX_ARCHIVE + 1)
            if len(payload) > MAX_ARCHIVE:
                raise InvalidProjectArtifacts("artifact stream too large")
            code = process.wait(timeout=5)
            if code != 0:
                errors.seek(0)
                diagnostic = errors.read(4097)
                expected = f"Error response from daemon: Could not find the file /home/node/{output_directory}/. in container {name}"
                if code == 1 and not payload and diagnostic.strip() == expected.encode('utf-8'):
                    raise MissingProjectOutput(
                        f"Build completed, but outputDirectory '{output_directory}' was not created. "
                        "Read the build configuration or main.py and submit a new job with its actual output directory; no files were imported.")
                raise InvalidProjectArtifacts("artifact collection failed")
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
    return decode_artifacts(payload)
