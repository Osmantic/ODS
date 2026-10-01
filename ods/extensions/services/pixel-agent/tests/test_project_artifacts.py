import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import json
import subprocess
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_artifacts import InvalidProjectArtifacts, MissingProjectOutput, collect_artifacts, decode_artifacts, import_artifacts


def packed(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, kind, body in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "../../outside" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            member.size = len(body) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(body))
    return out.getvalue()


class ProjectArtifactsTests(unittest.TestCase):
    def test_only_exact_missing_path_response_is_recoverable(self):
        image, job = 'sha256:' + 'a' * 64, 'ods-project-' + 'b' * 24
        exact = f'Error response from daemon: Could not find the file /home/node/dist/. in container {job}-build'
        container = {'State': {'Running': False, 'ExitCode': 0},
                     'Config': {'Image': image, 'Labels': {'org.osmantic.ods.project-job': job}},
                     'HostConfig': {'NetworkMode': 'none'}}
        for message, expected in ((exact, MissingProjectOutput), ('Cannot connect to the Docker daemon', InvalidProjectArtifacts),
                                  (exact.replace('/dist/', '/out/'), InvalidProjectArtifacts)):
            with self.subTest(message=message):
                def copy(*args, **kwargs):
                    kwargs['stderr'].write(message.encode())
                    process = MagicMock()
                    process.stdout = io.BytesIO(b'')
                    process.wait.return_value = 1
                    process.poll.return_value = 1
                    return process
                with patch('project_artifacts.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps([container]).encode())), patch('project_artifacts.subprocess.Popen', side_effect=copy):
                    with self.assertRaises(expected) as raised:
                        collect_artifacts(image, job, 'dist')
                    self.assertIs(type(raised.exception), expected)

    def test_framework_paths_and_exact_bytes(self):
        data = packed([("./index.html", tarfile.REGTYPE, b"<h1>actual</h1>\n"),
                       ("./_next/static/app.js", tarfile.REGTYPE, b"boot();")])
        result = decode_artifacts(data)
        self.assertEqual(result["files"]["index.html"], b"<h1>actual</h1>\n")
        self.assertEqual(result["sha256"], decode_artifacts(data)["sha256"])

    def test_next_dynamic_route_assets_round_trip_without_renaming(self):
        paths = ['_next/static/chunks/app/games/[slug]/page.js',
                 '_next/static/chunks/app/docs/[...slug]/page.js',
                 '_next/static/chunks/app/docs/[[...slug]]/page.js']
        output = decode_artifacts(packed([(p, tarfile.REGTYPE, b'actual();') for p in paths]))
        self.assertEqual(set(output['files']), set(paths))
        if os.name == 'posix':
            with tempfile.TemporaryDirectory() as root:
                (Path(root) / 'project').mkdir()
                imported = import_artifacts(root, 'project', 'ods-project-' + 'c' * 24, output)
                for path in paths:
                    self.assertEqual((Path(root) / imported / path).read_bytes(), b'actual();')

    def test_rejects_links_devices_and_traversal(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("/outside", tarfile.REGTYPE),
                           (".env", tarfile.REGTYPE), ("__ods_meta.json", tarfile.REGTYPE),
                           ("asset", tarfile.SYMTYPE), ("asset", tarfile.LNKTYPE),
                           ("asset", tarfile.CHRTYPE)):
            with self.subTest(name=name, kind=kind), self.assertRaises(InvalidProjectArtifacts):
                decode_artifacts(packed([(name, kind, b"x")]))

    def test_duplicate_and_file_directory_collisions(self):
        for names in (("a", "a"), ("A", "a"), ("A/b", "a/c"), ("a", "a/b"), ("a/b", "a")):
            with self.subTest(names=names), self.assertRaises(InvalidProjectArtifacts):
                decode_artifacts(packed([(n, tarfile.REGTYPE, b"x") for n in names]))

    @unittest.skipUnless(os.name == "posix", "POSIX owner importer")
    def test_output_directory_symlink_never_receives_files(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"
            outside = Path(root) / "outside"
            project.mkdir()
            outside.mkdir()
            (project / "ods-builds").symlink_to(outside, target_is_directory=True)
            artifacts = decode_artifacts(packed([("index.html", tarfile.REGTYPE, b"x")]))
            with self.assertRaises(OSError):
                import_artifacts(root, "project", "ods-project-" + "a" * 24, artifacts)
            self.assertEqual(list(outside.iterdir()), [])
