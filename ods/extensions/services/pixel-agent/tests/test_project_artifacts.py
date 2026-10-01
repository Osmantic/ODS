import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_artifacts import InvalidProjectArtifacts, decode_artifacts, import_artifacts


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
    def test_framework_paths_and_exact_bytes(self):
        data = packed([("./index.html", tarfile.REGTYPE, b"<h1>actual</h1>\n"),
                       ("./_next/static/app.js", tarfile.REGTYPE, b"boot();")])
        result = decode_artifacts(data)
        self.assertEqual(result["files"]["index.html"], b"<h1>actual</h1>\n")
        self.assertEqual(result["sha256"], decode_artifacts(data)["sha256"])

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
