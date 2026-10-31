"""Portal's project copies and restores (plugin/project-snapshots.py).

The helper does all its work through folder descriptors (*at() calls). Where
Python has none (Windows), these tests drive the same logic through
PathFolder below, a path-based stand-in that refuses links but cannot stop a
race; the tests that prove a swapped folder is never followed, and the ones
about POSIX permissions, run only with the real descriptors.
"""
import errno
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
SPEC = importlib.util.spec_from_file_location("project_snapshots_helper", PLUGIN / "project-snapshots.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
DESCRIPTORS = helper.supported()
ORIGINAL = "export const chars = ['elf', 'orc'];\n"


def run(n):
    return f"chatcmpl_00000000-0000-4000-8000-{n:012d}"


class PathFolder:
    """The helper's Folder over paths, for test runs without *at() calls. Links (and junctions) are refused."""

    def __init__(self, path):
        self.path = path

    @staticmethod
    def opened(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise OSError(errno.ELOOP, "link", path)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(errno.ENOTDIR, "not a folder", path)
        return PathFolder(path)

    def close(self):
        pass

    def info(self):
        return os.stat(self.path)

    def names(self):
        return os.listdir(self.path)

    def lstat(self, name):
        try:
            info = os.lstat(os.path.join(self.path, name))
        except FileNotFoundError:
            return None
        if getattr(info, "st_reparse_tag", 0):  # a junction reads as the link it is
            return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_dev=info.st_dev, st_ino=info.st_ino,
                                         st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                                         st_ctime_ns=info.st_ctime_ns, st_nlink=info.st_nlink)
        return info

    def child(self, name):
        return PathFolder.opened(os.path.join(self.path, name))

    def make(self, name, mode=0o777):
        os.mkdir(os.path.join(self.path, name), mode)

    def read(self, name, expected, limit):
        target = os.path.join(self.path, name)
        info = os.lstat(target)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                or helper.identity(info) != helper.identity(expected)):
            return None
        with open(target, "rb") as stream:
            data = stream.read(info.st_size + 1)
        return data if len(data) == info.st_size and helper.identity(os.lstat(target)) == helper.identity(info) else None

    def write(self, name, data, mode, durable=True, prefix=helper.LEFTOVER):
        temporary = os.path.join(self.path, prefix + secrets.token_hex(8))
        with open(temporary, "xb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, os.path.join(self.path, name))

    def unlink(self, name):
        os.unlink(os.path.join(self.path, name))

    def rmdir(self, name):
        os.rmdir(os.path.join(self.path, name))

    def sync(self):
        pass


OPENER = helper.open_root if DESCRIPTORS else PathFolder.opened
FOLDER = helper.Folder if DESCRIPTORS else PathFolder


def put(path, text):
    Path(path).write_bytes(text.encode())


def touch(path, seconds=5):
    """Only the file's times change."""
    later = time.time() + seconds
    os.utime(path, (later, later))


def leave(folder, name):
    """A file created and never renamed, as a restore killed midway leaves its temporary."""
    if hasattr(folder, "fd"):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=folder.fd)
    else:
        fd = os.open(os.path.join(folder.path, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, b"half written")
    os.close(fd)


class Killed(BaseException):
    """The helper process dying where it stands (SIGKILL, the plugin's timeout)."""


def snapshots(answer):
    return [entry["snapshot"] for entry in answer["projects"]]


def write_tree(folder, files):
    for name, body in files.items():
        target = Path(folder, *name.split("/"))
        if body is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body if isinstance(body, bytes) else body.encode())


def tree(folder):
    found = {}
    folder = Path(folder)
    if not folder.exists():
        return found
    for path in sorted(folder.rglob("*")):
        key = path.relative_to(folder).as_posix()
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            found[key] = "<link>"
        elif path.is_dir():
            found[key] = "<dir>"
        else:
            found[key] = path.read_text(errors="replace")
    return {key: value for key, value in found.items() if not any(
        key.startswith(link + "/") for link, kind in found.items() if kind == "<link>")}


def link(target, name):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(name))
    else:
        os.symlink(target, name)


class Workspace:
    """A workspace with Playground/<project>, a private state folder, and the helper's operations on them."""

    def __init__(self, test, files=None, project="game", limits=None):
        self.base = Path(tempfile.mkdtemp(prefix="ods-project-copies-")).resolve()
        test.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "workspace"
        self.state = self.base / "state" / ".ods-project-snapshots"
        (self.base / "state").mkdir()
        self.playground = self.root / "Playground"
        self.project = self.playground / project
        self.project.mkdir(parents=True)
        write_tree(self.project, files or {})
        self.limits = limits

    def call(self, operation, **fields):
        request = {"operation": operation, "workspace": str(self.root), "root": str(self.state), **fields}
        if self.limits:
            request["limits"] = self.limits
        return helper.handle(json.loads(json.dumps(request)), OPENER)

    def fingerprint(self):
        return self.call("fingerprint")

    def copy(self, n, *projects, start=None):
        return self.call("copy", id=run(n), projects=[{"name": name, "start": (start or {}).get(name)}
                                                     for name in (projects or ("game",))])

    def after(self, n, *projects):
        return self.call("after", id=run(n), projects=[{"name": name} for name in (projects or ("game",))])

    def turn(self, n, change, *projects):
        """One Portal turn: its copy before it touches the project, the change, and its end."""
        self.copy(n, *projects)
        change()
        return self.after(n, *projects)

    def restore(self, snapshot, project="game", confirm=None):
        return self.call("restore", project=project, snapshot=snapshot, **({"confirm": confirm} if confirm else {}))

    def options(self, *runs):
        return self.call("options", runs=list(runs))

    def records(self, project="game"):
        folder = self.state / helper.store_key(str(self.root), project) / "copies"
        return sorted(name[16:-5] for name in os.listdir(folder)) if folder.exists() else []

    def objects(self, project="game"):
        return os.listdir(self.state / helper.store_key(str(self.root), project) / "objects")

    def text(self, name):
        return (self.project / name).read_text()


class CopyAndRestoreTests(unittest.TestCase):
    def test_a_turn_that_doubles_a_file_is_put_back_and_the_undo_reverses_it(self):
        w = Workspace(self, {"chars.js": ORIGINAL, "index.html": "<script src=chars.js></script>"})
        self.assertEqual(w.copy(1), {"projects": {"game": {"status": "kept", "late": False}}})
        self.assertEqual(w.copy(1), {"projects": {"game": {"status": "exists"}}}, "once per response")
        put(w.project / "chars.js", ORIGINAL * 2)
        self.assertEqual(w.after(1), {"projects": {"game": {"status": "changed"}}})
        restored = w.restore(run(1))
        self.assertEqual(restored["status"], "restored")
        self.assertEqual((restored["written"], restored["removed"], restored["left"], restored["failed"]), (1, 0, [], []))
        self.assertRegex(restored["undo"], r"^restore_[0-9a-f-]{36}$")
        self.assertEqual(w.text("chars.js"), ORIGINAL)
        undone = w.restore(restored["undo"])
        self.assertEqual(undone["status"], "restored")
        self.assertEqual(w.text("chars.js"), ORIGINAL * 2)
        self.assertNotEqual(undone["undo"], restored["undo"], "the undo itself can be reversed")

    def test_a_retried_restore_returns_the_same_receipt_and_keeps_its_undo(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.turn(1, lambda: put(w.project / "chars.js", "broken"))
        first = w.restore(run(1))
        self.assertEqual(w.restore(run(1)), first)
        undone = w.restore(first["undo"])
        self.assertEqual(w.restore(first["undo"]), undone, "a retried undo too")
        self.assertEqual(w.text("chars.js"), "broken")

    def test_a_restore_that_could_not_change_every_file_is_finished_by_a_retry_under_the_same_undo(self):
        w = Workspace(self, {"a.js": "a", "b.js": "b"})
        w.turn(1, lambda: (put(w.project / "a.js", "A"), put(w.project / "b.js", "B")))
        real = FOLDER.write

        def in_use(folder, name, *args, **kwargs):
            if name == "b.js":
                raise OSError(errno.EBUSY, "in use")
            return real(folder, name, *args, **kwargs)
        with mock.patch.object(FOLDER, "write", in_use):
            first = w.restore(run(1))
        self.assertEqual((first["status"], first["written"], first["failed"], first["failure"]),
                         ("restored", 1, ["b.js"], "in-use"))
        self.assertEqual((w.text("a.js"), w.text("b.js")), ("a", "B"))
        second = w.restore(run(1))
        self.assertEqual((second["written"], second["failed"], second["undo"]), (1, [], first["undo"]))
        self.assertEqual(w.text("b.js"), "b")
        self.assertEqual(w.restore(run(1)), second, "a retry after that gets the same answer")
        undone = w.restore(first["undo"])
        self.assertEqual((undone["status"], w.text("a.js"), w.text("b.js")), ("restored", "A", "B"))
        # An undo that could not change every file is finished the same way.
        with mock.patch.object(FOLDER, "write", in_use):
            again = w.restore(undone["undo"])
        self.assertEqual((again["failed"], w.text("a.js")), (["b.js"], "a"))
        self.assertEqual(w.restore(undone["undo"])["undo"], again["undo"])
        self.assertEqual((w.text("a.js"), w.text("b.js")), ("a", "b"))

    def test_the_undo_survives_a_reload_through_options(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.turn(1, lambda: put(w.project / "chars.js", "broken"))
        self.assertEqual(w.options(run(1)), {"projects": [{"project": "Playground/game", "snapshot": run(1),
                                                           "late": False, "restore": None}]})
        first = w.restore(run(1))
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], {"undo": first["undo"], "side": "before"})
        undone = w.restore(first["undo"])
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], {"undo": undone["undo"], "side": "after"})
        self.assertEqual(w.text("chars.js"), "broken")
        # The Undo's own undo puts the files from before the turn back again, and so on.
        again = w.restore(undone["undo"])
        self.assertEqual((again["status"], w.text("chars.js")), ("restored", ORIGINAL))
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], {"undo": again["undo"], "side": "before"})
        self.assertEqual(w.restore(again["undo"])["status"], "restored")
        self.assertEqual(w.options(run(1))["projects"][0]["restore"]["side"], "after")
        # Files changed since: the undo is still offered, and asks first when it would overwrite a change.
        offered = w.options(run(1))["projects"][0]["restore"]
        touch(w.project / "chars.js")
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], offered, "a touch hides nothing")
        put(w.project / "chars.js", "edited by the owner")
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], offered)
        asked = w.restore(offered["undo"])
        self.assertEqual((asked["status"], asked["confirm"]["overwrite"]), ("confirm", ["chars.js"]))
        self.assertEqual(w.text("chars.js"), "edited by the owner")
        self.assertEqual(w.options(run(2)), {"projects": []})
        # A later turn that changes the project takes over its Put back, without the old undo.
        w.turn(2, lambda: put(w.project / "chars.js", "turn 2"))
        self.assertEqual(w.options(run(1), run(2))["projects"], [{"project": "Playground/game", "snapshot": run(2),
                                                                  "late": False, "restore": None}])

    def test_changed_deleted_and_created_files_and_folders_are_put_back(self):
        w = Workspace(self, {"chars.js": ORIGINAL, "src/a.js": "a", "src/b.js": "b", "assets": None})
        before = tree(w.project)

        def change():
            put(w.project / "chars.js", "broken")
            (w.project / "src" / "b.js").unlink()
            write_tree(w.project, {"src/new.js": "new", "tmp/x/y.txt": "y"})
            (w.project / "assets").rmdir()
            (w.project / "src" / "a.js").unlink()
            write_tree(w.project, {"src/a.js/inner.txt": "inner"})
        w.turn(1, change)
        turned = tree(w.project)
        restored = w.restore(run(1))
        self.assertEqual(tree(w.project), before)
        self.assertEqual((restored["written"], restored["removed"]), (3, 3))
        w.restore(restored["undo"])
        self.assertEqual(tree(w.project), turned)

    def test_a_removed_project_and_a_removed_playground_come_back(self):
        w = Workspace(self, {"chars.js": ORIGINAL, "src/a.js": "a"})
        before = tree(w.project)
        w.turn(1, lambda: shutil.rmtree(w.project))
        self.assertEqual(w.restore(run(1))["status"], "restored")
        self.assertEqual(tree(w.project), before)
        w.turn(2, lambda: shutil.rmtree(w.playground))
        restored = w.restore(run(2))
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(tree(w.project), before)

    def test_a_project_the_turn_created_goes_back_to_an_empty_folder(self):
        w = Workspace(self)
        shutil.rmtree(w.project)
        w.turn(1, lambda: write_tree(w.project, {"index.html": "x", "js/app.js": "y"}))
        restored = w.restore(run(1))
        self.assertEqual((restored["status"], restored["removed"]), ("restored", 2))
        self.assertEqual(tree(w.project), {})

    def test_nothing_to_put_back_says_so_without_an_undo(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.copy(1)
        put(w.project / "chars.js", "broken")
        w.after(1)
        put(w.project / "chars.js", ORIGINAL)  # the owner fixed it by hand
        answer = w.restore(run(1))
        self.assertEqual((answer["status"], answer["undo"], answer["left"]), ("unchanged", None, []))


class WhatACopyHoldsTests(unittest.TestCase):
    def test_code_is_admitted_before_assets_when_the_budget_is_short(self):
        files = {"src/chars.js": "GOOD CODE"}
        files.update({f"assets/sprite{i}.png": "0123456789" for i in range(10)})
        w = Workspace(self, files, limits={"totalBytes": 100})
        w.turn(1, lambda: put(w.project / "src" / "chars.js", "BROKEN CODE"))
        restored = w.restore(run(1))
        self.assertEqual(w.text("src/chars.js"), "GOOD CODE")
        self.assertEqual((restored["written"], restored["left"]), (1, []))

    def test_a_file_grown_past_the_limit_is_named_and_never_called_matching(self):
        w = Workspace(self, {"chars.js": "x" * 40}, limits={"fileBytes": 50})
        w.turn(1, lambda: put(w.project / "chars.js", "x" * 80))
        answer = w.restore(run(1))
        self.assertEqual((answer["status"], answer["left"], answer["leftCount"]), ("unchanged", ["chars.js"], 1))
        self.assertEqual(len(w.text("chars.js")), 80)

    def test_bulk_folders_links_and_hard_links_are_listed_never_entered_or_reported_as_noise(self):
        outside = Path(tempfile.mkdtemp(prefix="ods-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        put(outside / "secret.txt", "SECRET-OUTSIDE")
        w = Workspace(self, {"chars.js": ORIGINAL, "node_modules/lib/index.js": "lib", ".git/HEAD": "ref",
                             "dist/app.js": "built", ".env": "KEY=1", "one/two/three/deep.js": "deep"},
                      limits={"depth": 3})
        link(outside, w.project / "assets")
        os.link(outside / "secret.txt", w.project / "hard.txt")
        put(w.project / ".ods-restore-0123abcd", "a crashed restore's temporary")
        w.copy(1)
        store = w.state / helper.store_key(str(w.root), "game")
        record = json.loads(next((store / "copies").iterdir()).read_text())
        saved = json.loads((store / "lists" / record["list"]).read_text())
        self.assertEqual([item[0] for item in saved["files"]], [".env", "chars.js"])
        self.assertEqual({item[0]: item[1] for item in saved["skipped"]},
                         {".git": "bulk", "assets": "link", "dist": "bulk", "hard.txt": "other",
                          "node_modules": "bulk", "one/two/three": "bulk"})
        for name in os.listdir(store / "objects"):
            self.assertNotIn("SECRET-OUTSIDE", (store / "objects" / name).read_text())
        put(w.project / "chars.js", "broken")
        w.after(1)
        restored = w.restore(run(1))
        self.assertEqual((restored["written"], restored["left"], restored["leftCount"]), (1, [], 0))
        self.assertTrue((w.project / ".ods-restore-0123abcd").exists(), "a restore removes only temporaries it wrote")
        self.assertEqual(tree(outside), {"secret.txt": "SECRET-OUTSIDE"})

    def test_a_link_the_turn_put_in_place_of_a_file_is_named_and_left(self):
        outside = Path(tempfile.mkdtemp(prefix="ods-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        w = Workspace(self, {"chars.js": ORIGINAL, "src/a.js": "a"})

        def change():
            shutil.rmtree(w.project / "src")
            link(outside, w.project / "src")
        w.turn(1, change)
        answer = w.restore(run(1))
        self.assertEqual(answer["left"], ["src", "src/a.js"])
        self.assertEqual(tree(outside), {})

    def test_an_incomplete_copy_of_the_project_now_writes_only_what_it_holds(self):
        w = Workspace(self, {"b.js": "b", "c.js": "c", "z.js": "saved z"}, limits={"entries": 4})
        w.turn(1, lambda: (put(w.project / "z.js", "TURN VERSION OF z.js"),
                           write_tree(w.project, {"a1.js": "new 1", "a2.js": "new 2"})))
        before_restore = tree(w.project)
        restored = w.restore(run(1))
        self.assertEqual((restored["removed"], restored["left"]), (2, ["z.js"]))
        self.assertEqual(w.text("z.js"), "TURN VERSION OF z.js", "never overwritten without a copy to undo it")
        w.restore(restored["undo"])
        self.assertEqual(tree(w.project), before_restore, "the undo puts back everything the restore changed")

    def test_contents_are_stored_once_and_unchanged_files_are_not_read_again(self):
        w = Workspace(self, {f"f{i}.js": f"file {i}" for i in range(20)})
        w.turn(1, lambda: put(w.project / "f0.js", "changed"))
        reads = []
        real = helper.Folder.read if DESCRIPTORS else PathFolder.read

        def counting(folder, name, expected, limit):
            reads.append(name)
            return real(folder, name, expected, limit)
        target = helper.Folder if DESCRIPTORS else PathFolder
        target.read = counting
        try:
            w.copy(2)
        finally:
            target.read = real
        # The turn's end hashed f0.js without storing it; every other file is known by its stat.
        self.assertEqual([name for name in reads if name.endswith(".js")], ["f0.js"])
        self.assertEqual(len(w.objects()), 21, "20 first contents and the one change, each once")


class WhichTurnAndWhenTests(unittest.TestCase):
    def test_only_the_latest_changing_turn_is_offered_and_read_only_turns_never_push_it_out(self):
        w = Workspace(self, {"chars.js": "good"})
        w.turn(1, lambda: put(w.project / "chars.js", "broken by turn 1"))
        for n in range(2, 8):
            self.assertEqual(w.turn(n, lambda: None), {"projects": {"game": {"status": "unchanged"}}})
        self.assertEqual([entry["snapshot"] for entry in w.options(*[run(n) for n in range(1, 8)])["projects"]], [run(1)])
        self.assertEqual(w.restore(run(1))["status"], "restored")
        self.assertEqual(w.text("chars.js"), "good")
        w.turn(8, lambda: put(w.project / "chars.js", "turn 8"))
        self.assertEqual([entry["snapshot"] for entry in w.options(run(1), run(8))["projects"]], [run(8)])

    def test_later_changes_need_the_owners_yes_and_the_dry_run_names_them(self):
        w = Workspace(self, {"chars.js": "v0"})
        w.turn(1, lambda: put(w.project / "chars.js", "v1 (turn 1)"))
        put(w.project / "chars.js", "edited by the owner")
        put(w.project / "notes-by-owner.md", "the owner wrote this by hand")
        before = tree(w.project)
        asked = w.restore(run(1))
        self.assertEqual(asked["status"], "confirm")
        self.assertEqual((asked["confirm"]["overwrite"], asked["confirm"]["remove"]), (["chars.js"], ["notes-by-owner.md"]))
        self.assertEqual((asked["written"], asked["removed"], asked["undo"]), (0, 0, None))
        self.assertEqual(tree(w.project), before, "a dry run changes nothing")
        # The project changes again: the old yes is not a yes to this.
        put(w.project / "more.md", "more")
        again = w.restore(run(1), confirm=asked["confirm"]["token"])
        self.assertEqual(again["status"], "confirm")
        done = w.restore(run(1), confirm=again["confirm"]["token"])
        self.assertEqual((done["status"], done["removed"]), ("restored", 2))
        self.assertEqual(tree(w.project), {"chars.js": "v0"})
        w.restore(done["undo"])
        self.assertEqual(w.text("notes-by-owner.md"), "the owner wrote this by hand")

    def test_changes_the_restore_does_not_touch_need_no_yes(self):
        w = Workspace(self, {"chars.js": "v0", "big.bin": "x" * 60}, limits={"fileBytes": 50})
        w.turn(1, lambda: put(w.project / "chars.js", "v1"))
        put(w.project / "big.bin", "y" * 70)  # left out of every copy
        answer = w.restore(run(1))
        self.assertEqual((answer["status"], answer["left"]), ("restored", ["big.bin"]))

    def test_a_copy_taken_after_the_project_already_changed_is_marked_late(self):
        w = Workspace(self, {"chars.js": "GOOD"})
        start = w.fingerprint()
        self.assertEqual(set(start["projects"]), {"game"})
        put(w.project / "chars.js", "BROKEN")  # an exec that named no project
        copied = w.copy(1, start=start["projects"])
        self.assertEqual(copied["projects"]["game"], {"status": "kept", "late": True})
        self.assertEqual(w.after(1)["projects"]["game"]["status"], "changed", "a late copy always counts")
        self.assertTrue(w.options(run(1))["projects"][0]["late"])
        answer = w.restore(run(1))
        self.assertEqual((answer["status"], answer["late"]), ("unchanged", True))

    def test_a_project_absent_at_the_start_and_created_before_its_copy_is_late(self):
        w = Workspace(self, {"chars.js": "x"})
        start = w.fingerprint()
        write_tree(w.playground / "fresh", {"index.html": "made by an exec"})
        copied = w.call("copy", id=run(1), projects=[{"name": "fresh", "start": "absent"}, {"name": "game",
                                                                                          "start": start["projects"]["game"]}])
        self.assertEqual(copied["projects"], {"fresh": {"status": "kept", "late": True},
                                              "game": {"status": "kept", "late": False}})


class RetentionTests(unittest.TestCase):
    def test_five_changing_turns_and_the_latest_restore_copy_are_kept(self):
        w = Workspace(self, {"chars.js": "v0"})
        for n in range(1, 8):
            w.turn(n, lambda n=n: put(w.project / "chars.js", f"v{n}"))
        self.assertEqual(w.records(), [run(n) for n in range(3, 8)])
        self.assertEqual(w.restore(run(1)), {"error": "pruned"})
        self.assertEqual(w.restore(run(9)), {"error": "unavailable-copy"})
        first = w.restore(run(7))
        second = w.restore(first["undo"])
        # The latest restore's undo, and the copy it put back for a retry.
        self.assertEqual({record for record in w.records() if record.startswith("restore_")}, {first["undo"], second["undo"]})
        third = w.restore(second["undo"])
        self.assertEqual({record for record in w.records() if record.startswith("restore_")}, {second["undo"], third["undo"]})

    def test_the_size_cap_drops_the_oldest_copies_first(self):
        w = Workspace(self, {"a.js": "a" * 4000}, limits={"storeBytes": 10000})
        for n in range(1, 5):
            w.turn(n, lambda n=n: put(w.project / "a.js", str(n) * 4000))
        w.call("copy", id=run(5), projects=[{"name": "game"}], collect=True)
        self.assertEqual(w.records(), [run(4), run(5)])
        total = sum((w.state / helper.store_key(str(w.root), "game") / "objects" / name).stat().st_size
                    for name in w.objects())
        self.assertLessEqual(total, 10000)

    def test_copies_of_a_project_missing_for_two_weeks_are_dropped_counted_from_when_it_went_missing(self):
        w = Workspace(self, {"a.js": "a"})
        w.turn(1, lambda: put(w.project / "a.js", "b"))
        write_tree(w.playground / "other", {"x.js": "x"})
        store = w.state / helper.store_key(str(w.root), "game")
        old = next((store / "copies").iterdir())
        old.rename(old.with_name("000000000000001" + old.name[15:]))  # a turn from long ago
        shutil.rmtree(w.project)
        w.call("copy", id=run(2), projects=[{"name": "other"}], collect=True)
        self.assertTrue(store.exists(), "kept two weeks from when ODS found it missing, however old its copies")
        self.assertEqual(snapshots(w.options(run(1))), [run(1)])
        # Back meanwhile, then gone again: the two weeks start over.
        w.project.mkdir()
        w.call("copy", id=run(3), projects=[{"name": "other"}], collect=True)
        self.assertFalse((store / "missing.json").exists())
        shutil.rmtree(w.project)
        w.call("copy", id=run(4), projects=[{"name": "other"}], collect=True)
        missing = json.loads((store / "missing.json").read_text())
        (store / "missing.json").write_text(json.dumps({**missing, "since": missing["since"] - 13 * 86400}))
        w.call("copy", id=run(5), projects=[{"name": "other"}], collect=True)
        self.assertTrue(store.exists())
        (store / "missing.json").write_text(json.dumps({**missing, "since": missing["since"] - 15 * 86400}))
        w.call("copy", id=run(6), projects=[{"name": "other"}], collect=True)
        self.assertFalse(store.exists())

    def test_the_size_cap_never_drops_a_projects_only_put_back_copy_for_another_projects_copy(self):
        w = Workspace(self, {"chars.js": "x" * 60}, limits={"storeBytes": 100})
        w.turn(1, lambda: put(w.project / "chars.js", "BROKEN"))
        write_tree(w.playground / "other", {"readme.md": "y" * 60})
        w.call("copy", id=run(2), projects=[{"name": "other"}], collect=True, live=[run(2)])
        self.assertEqual(sorted(snapshots(w.options(run(1), run(2)))), [run(1), run(2)])
        self.assertEqual(w.restore(run(1))["status"], "restored")
        self.assertEqual(w.text("chars.js"), "x" * 60)


class LiveRunTests(unittest.TestCase):
    """The gateway names the runs it still runs (live); a copy's run outside them has ended."""

    def test_a_copy_whose_run_is_gone_is_ended_at_the_next_request_and_never_hides_the_changing_turn(self):
        w = Workspace(self, {"chars.js": "v0"})
        w.turn(1, lambda: put(w.project / "chars.js", "BROKEN by turn 1"))
        w.call("copy", id=run(2), projects=[{"name": "game"}], live=[run(2)])  # turn 2 reads; the gateway restarts
        self.assertEqual(snapshots(w.call("options", runs=[run(1), run(2)], live=[run(2)])), [run(2)],
                         "while turn 2 runs, it may still change the project")
        self.assertEqual(snapshots(w.call("options", runs=[run(1), run(2)], live=[])), [run(1)])
        self.assertEqual(w.restore(run(1))["status"], "restored", "nothing changed after turn 1: no question")
        # A turn that changed the project before the gateway stopped is offered, and asks first.
        w.call("copy", id=run(3), projects=[{"name": "game"}], live=[run(3)])
        put(w.project / "chars.js", "changed by turn 3 before the gateway stopped")
        self.assertEqual(snapshots(w.call("options", runs=[run(1), run(3)], live=[])), [run(3)])
        asked = w.call("restore", project="game", snapshot=run(3), live=[])
        self.assertEqual((asked["status"], asked["confirm"]["overwrite"]), ("confirm", ["chars.js"]))
        done = w.call("restore", project="game", snapshot=run(3), confirm=asked["confirm"]["token"], live=[])
        self.assertEqual((done["status"], w.text("chars.js")), ("restored", "v0"))

    def test_a_live_runs_copy_is_never_dropped_and_a_turn_that_ran_beside_another_change_asks_first(self):
        w = Workspace(self, {"chars.js": "v0"})
        w.call("copy", id=run(1), projects=[{"name": "game"}], live=[run(1)])
        w.call("after", id=run(1), projects=[{"name": "game"}], live=[run(1)])  # an end recorded too early
        time.sleep(0.01)
        w.call("copy", id=run(2), projects=[{"name": "game"}], live=[run(1), run(2)])  # another chat's turn
        self.assertEqual(w.records(), [run(1), run(2)], "a live run's copy is kept, though nothing changed yet")
        time.sleep(0.01)
        put(w.project / "chars.js", "changed by turn 1")
        w.call("after", id=run(1), projects=[{"name": "game"}], live=[run(2)])
        time.sleep(0.01)
        w.call("after", id=run(2), projects=[{"name": "game"}], live=[])
        # Turn 2 only read, but turn 1 changed the project while it ran: putting either back asks first.
        for snapshot in (run(2), run(1)):
            asked = w.restore(snapshot)
            self.assertEqual((asked["status"], asked["confirm"]["overwrite"]), ("confirm", ["chars.js"]), snapshot)
        self.assertEqual(w.text("chars.js"), "changed by turn 1")
        # Turns one after the other ask nothing.
        w.turn(3, lambda: put(w.project / "chars.js", "turn 3"))
        self.assertEqual(w.restore(run(3))["status"], "restored")

    def test_the_fingerprint_says_when_anything_in_each_project_last_changed(self):
        w = Workspace(self, {"chars.js": "x"})
        write_tree(w.playground / "other", {"a.js": "a"})
        time.sleep(0.05)
        before = os.stat(w.project)
        put(w.project / "chars.js", "edited in place")
        os.utime(w.project, ns=(before.st_atime_ns, before.st_mtime_ns))
        newest = w.fingerprint()["newest"]
        self.assertGreater(newest["game"], newest["other"])
        self.assertGreaterEqual(newest["game"], os.stat(w.project / "chars.js").st_mtime_ns // 1_000_000)


def killed_after_one_write():
    """Folder.write that writes one project file, then dies in the middle of the next, leaving its temporary."""
    real, written = FOLDER.write, []

    def write(folder, name, data, mode, durable=True, prefix=helper.LEFTOVER):
        if prefix != helper.LEFTOVER and written:
            leave(folder, prefix + "0123456789abcdef")
            raise Killed()
        if prefix != helper.LEFTOVER:  # a project file, not the store's
            written.append(name)
        return real(folder, name, data, mode, durable, prefix)
    return write


class KilledRestoreTests(unittest.TestCase):
    def test_a_restore_killed_midway_is_finished_by_its_retry_and_its_undo_keeps_the_turns_version(self):
        w = Workspace(self, {"a.js": "before a", "b.js": "before b", "c.js": "before c"})
        w.turn(1, lambda: [put(w.project / name, f"TURN {name}") for name in ("a.js", "b.js", "c.js")])
        turned = tree(w.project)
        with mock.patch.object(FOLDER, "write", killed_after_one_write()), self.assertRaises(Killed):
            w.restore(run(1))
        self.assertEqual(sum(w.text(name).startswith("before") for name in ("a.js", "b.js", "c.js")), 1)
        state = json.loads((w.state / helper.store_key(str(w.root), "game") / "restore.json").read_text())
        self.assertTrue(state["pending"])
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], None, "no undo before the restore is finished")
        w.turn(2, lambda: None, "game")  # a later turn's copy and prune keep the killed restore's undo copy
        retry = w.restore(run(1))
        self.assertEqual((retry["status"], retry["undo"], retry["written"]), ("restored", state["undo"], 2))
        self.assertEqual([w.text(name) for name in ("a.js", "b.js", "c.js")], ["before a", "before b", "before c"])
        self.assertEqual([name for name in os.listdir(w.project) if name.startswith(".ods-restore-")], [],
                         "the retry removes what its killed attempt left")
        w.restore(retry["undo"])
        self.assertEqual(tree(w.project), turned, "the undo puts back the turn's version, not the half-restored one")

    def test_an_undo_killed_midway_is_offered_again_and_finished_under_its_own_undo(self):
        w = Workspace(self, {"a.js": "before a", "b.js": "before b"})
        w.turn(1, lambda: [put(w.project / name, f"TURN {name}") for name in ("a.js", "b.js")])
        first = w.restore(run(1))
        with mock.patch.object(FOLDER, "write", killed_after_one_write()), self.assertRaises(Killed):
            w.restore(first["undo"])
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], {"undo": first["undo"], "side": "before"},
                         "the next step is that Undo again")
        retry = w.restore(first["undo"])
        self.assertEqual((retry["status"], w.text("a.js"), w.text("b.js")), ("restored", "TURN a.js", "TURN b.js"))
        self.assertEqual(w.options(run(1))["projects"][0]["restore"], {"undo": retry["undo"], "side": "after"})
        w.restore(retry["undo"])
        self.assertEqual((w.text("a.js"), w.text("b.js")), ("before a", "before b"))

    def test_a_restore_that_cannot_list_its_result_still_answers_restored_and_its_retry_keeps_the_undo(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.turn(1, lambda: put(w.project / "chars.js", "broken"))
        real, walks = helper.walk, []

        def unlistable_after_applying(project, limits):
            walks.append(project)
            if len(walks) == 2:
                raise helper.HelperError("folder-unusable")
            return real(project, limits)
        with mock.patch.object(helper, "walk", unlistable_after_applying):
            first = w.restore(run(1))
        self.assertEqual((first["status"], first["written"]), ("restored", 1), "never 'nothing was changed'")
        self.assertEqual(w.text("chars.js"), ORIGINAL)
        again = w.restore(run(1))
        self.assertEqual((again["status"], again["undo"], again["written"]), ("restored", first["undo"], 0))
        self.assertEqual(w.restore(run(1)), again, "the result is known now")
        w.restore(first["undo"])
        self.assertEqual(w.text("chars.js"), "broken")


class RefusalTests(unittest.TestCase):
    def test_a_damaged_copy_changes_nothing(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.turn(1, lambda: put(w.project / "chars.js", "broken"))
        objects = w.state / helper.store_key(str(w.root), "game") / "objects"
        for name in os.listdir(objects):
            if (objects / name).read_text() == ORIGINAL:
                put(objects / name, "x" * len(ORIGINAL))  # same size, other bytes
        self.assertEqual(w.restore(run(1)), {"error": "damaged"})
        self.assertEqual(w.text("chars.js"), "broken")

    def test_a_project_path_that_is_a_link_or_a_file_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="ods-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.turn(1, lambda: put(w.project / "chars.js", "broken"))
        shutil.rmtree(w.project)
        link(outside, w.project)
        self.assertEqual(w.copy(2), {"projects": {"game": {"error": "folder-unusable"}}})
        self.assertEqual(w.restore(run(1)), {"error": "folder-unusable"})
        self.assertEqual(tree(outside), {})

    def test_a_listing_read_back_with_a_path_outside_its_project_is_damaged(self):
        w = Workspace(self, {"chars.js": ORIGINAL, "src/a.js": "a"})
        w.turn(1, lambda: put(w.project / "chars.js", "broken"))
        store = w.state / helper.store_key(str(w.root), "game")
        record_file = next((store / "copies").iterdir())
        record = json.loads(record_file.read_text())
        saved = json.loads((store / "lists" / record["list"]).read_text())
        for bad in ("../outside.js", "src//a.js", "./chars.js", "/chars.js", "src/..", ""):
            doc = {**saved, "files": [[bad] + saved["files"][0][1:]] + saved["files"][1:]}
            data = helper.canonical(doc)
            (store / "lists" / helper.sha256(data)).write_bytes(data)
            record_file.write_text(json.dumps({**record, "list": helper.sha256(data)}))
            self.assertEqual(w.restore(run(1)), {"error": "damaged"}, bad)
        self.assertEqual(w.text("chars.js"), "broken")
        self.assertEqual(sorted(os.listdir(w.root.parent)), ["state", "workspace"])

    def test_a_name_with_a_control_character_is_never_listed_named_or_removed(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        w.turn(1, lambda: (put(w.project / "chars.js", "broken"), put(w.project / "notes\x85.md", "made by the turn")))
        restored = w.restore(run(1))
        self.assertEqual((restored["written"], restored["removed"], restored["left"]), (1, 0, []))
        self.assertTrue((w.project / "notes\x85.md").exists())
        self.assertFalse(helper.visible("a\x9fb") or helper.visible("a\x7fb") or helper.visible("a\x1fb"))
        self.assertTrue(helper.visible("a\xa0b"))

    def test_requests_are_checked(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        for bad in ({"operation": "copy", "id": run(1), "projects": [{"name": "../x"}]},
                    {"operation": "copy", "id": "x", "projects": [{"name": "game"}]},
                    {"operation": "restore", "project": "game", "snapshot": run(1), "confirm": "not-a-token"},
                    {"operation": "restore", "project": "game/src", "snapshot": run(1)},
                    {"operation": "copy", "id": run(1), "projects": [{"name": "game"}], "limits": {"files": 10 ** 9}},
                    {"operation": "copy", "id": run(1), "projects": [{"name": "game"}], "live": ["x"]},
                    {"operation": "options", "runs": [], "live": run(1)},
                    {"operation": "delete"}):
            self.assertEqual(w.call(**{"operation": bad.pop("operation")}, **bad), {"error": "invalid"}, bad)

    @unittest.skipIf(DESCRIPTORS, "this system has the descriptors the helper needs")
    def test_without_descriptors_the_helper_answers_unsupported(self):
        answer = subprocess.run([sys.executable, "-I", "-S", "-B", str(PLUGIN / "project-snapshots.py")],
                                input=b"{}", capture_output=True, check=True)
        self.assertEqual(json.loads(answer.stdout), {"error": "unsupported"})


@unittest.skipUnless(DESCRIPTORS, "folder descriptors (POSIX)")
class DescriptorTests(unittest.TestCase):
    """What only the real *at() calls guarantee: a folder swapped for a link is never followed."""

    def outside(self, files):
        folder = Path(tempfile.mkdtemp(prefix="ods-outside-")).resolve()
        self.addCleanup(shutil.rmtree, folder, True)
        write_tree(folder, files)
        return folder

    def test_the_project_swapped_for_a_link_during_a_restore_keeps_every_change_inside_the_workspace(self):
        w = Workspace(self, {"a.txt": "saved a", "payload.txt": "FROM THE SAVED COPY"})
        outside = self.outside({"victim.txt": "OWNER FILE OUTSIDE", "payload.txt": "original outside"})

        def change():
            put(w.project / "a.txt", "turn a")
            (w.project / "payload.txt").unlink()
            put(w.project / "victim.txt", "turn-created")
        w.turn(1, change)
        real = helper.apply_plan

        def swapped(project, steps, objects, undo):
            os.rename(w.project, w.playground / ".g")
            os.symlink(outside, w.project)
            return real(project, steps, objects, undo)
        helper.apply_plan = swapped
        try:
            restored = w.restore(run(1))
        finally:
            helper.apply_plan = real
        self.assertEqual(tree(outside), {"victim.txt": "OWNER FILE OUTSIDE", "payload.txt": "original outside"})
        self.assertEqual(tree(w.playground / ".g"), {"a.txt": "saved a", "payload.txt": "FROM THE SAVED COPY"})
        self.assertEqual((restored["written"], restored["removed"]), (2, 1))

    def test_a_subfolder_swapped_for_a_link_during_a_restore_is_never_written_through(self):
        w = Workspace(self, {"src/a.js": "saved", "src/b.js": "b"})
        outside = self.outside({"a.js": "outside a"})
        w.turn(1, lambda: put(w.project / "src" / "a.js", "turn"))
        real = helper.apply_plan

        def swapped(project, steps, objects, undo):
            os.rename(w.project / "src", w.project / ".src")
            os.symlink(outside, w.project / "src")
            return real(project, steps, objects, undo)
        helper.apply_plan = swapped
        try:
            restored = w.restore(run(1))
        finally:
            helper.apply_plan = real
        self.assertEqual(tree(outside), {"a.js": "outside a"})
        self.assertEqual((restored["failed"], restored["failure"]), (["src/a.js"], "changed"))

    def test_the_project_swapped_for_a_link_during_a_copy_never_pulls_outside_files_in(self):
        w = Workspace(self, {"openclaw.json": "decoy", "index.html": "<p>game</p>"})
        outside = self.outside({"openclaw.json": '{"gateway":{"auth":{"token":"SECRET-GATEWAY-TOKEN"}}}'})
        real, swapped = helper.Folder.names, []

        def names(folder):
            listed = real(folder)
            if not swapped and "index.html" in listed:
                swapped.append(True)
                os.rename(w.project, w.playground / ".g")
                os.symlink(outside, w.project)
            return listed
        helper.Folder.names = names
        try:
            w.copy(1)
        finally:
            helper.Folder.names = real
        self.assertTrue(swapped)
        store = w.state / helper.store_key(str(w.root), "game")
        for name in os.listdir(store / "objects"):
            self.assertNotIn("SECRET-GATEWAY-TOKEN", (store / "objects" / name).read_text())

    def test_failures_say_why(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores folder permissions")
        w = Workspace(self, {"src/a.js": "saved"})
        w.turn(1, lambda: put(w.project / "src" / "a.js", "turn"))
        os.chmod(w.project / "src", 0o555)
        self.addCleanup(os.chmod, w.project / "src", 0o755)
        answer = w.restore(run(1))
        self.assertEqual((answer["failed"], answer["failure"]), (["src/a.js"], "permission"))

    def test_a_folder_this_process_may_not_list_is_left_as_it_is_and_named(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores folder permissions")
        w = Workspace(self, {"src/a.js": "a", "locked/x.js": "x", "b.js": "b"})
        os.chmod(w.project / "locked", 0)
        self.addCleanup(os.chmod, w.project / "locked", 0o755)
        w.turn(1, lambda: put(w.project / "b.js", "B"))
        restored = w.restore(run(1))
        self.assertEqual((restored["written"], restored["removed"], restored["left"]), (1, 0, ["locked"]))
        os.chmod(w.project / "locked", 0o755)
        self.assertEqual(tree(w.project), {"b.js": "b", "locked": "<dir>", "locked/x.js": "x", "src": "<dir>", "src/a.js": "a"})
        os.chmod(w.project, 0)
        self.addCleanup(os.chmod, w.project, 0o755)
        self.assertEqual(w.restore(restored["undo"]), {"error": "folder-unusable"})

    def test_a_mode_read_back_from_the_store_is_only_permission_bits(self):
        w = Workspace(self, {"tool.sh": "echo hi"})
        os.chmod(w.project / "tool.sh", 0o755)
        w.turn(1, lambda: put(w.project / "tool.sh", "broken"))
        store = w.state / helper.store_key(str(w.root), "game")
        record_file = next((store / "copies").iterdir())
        record = json.loads(record_file.read_text())
        saved = json.loads((store / "lists" / record["list"]).read_text())
        saved["files"][0][3] = 0o4755
        data = helper.canonical(saved)
        (store / "lists" / helper.sha256(data)).write_bytes(data)
        record_file.write_text(json.dumps({**record, "list": helper.sha256(data)}))
        self.assertEqual(w.restore(run(1))["status"], "restored")
        self.assertEqual(stat.S_IMODE(os.stat(w.project / "tool.sh").st_mode), 0o755)

    def test_an_unprivate_copies_folder_is_refused(self):
        w = Workspace(self, {"a.js": "a"})
        w.copy(1)
        os.chmod(w.state, 0o755)
        self.assertEqual(w.restore(run(1)), {"error": "copies-unusable"})

    def test_a_project_refused_while_opening_leaves_the_lock_free_for_the_next(self):
        w = Workspace(self, {"a.js": "a"})
        w.copy(1)
        shutil.rmtree(w.project)
        put(w.project, "a file where the project folder was")
        self.assertEqual(w.restore(run(1)), {"error": "folder-unusable"})
        lock = os.open(w.state / ".lock", os.O_RDWR)
        self.addCleanup(os.close, lock)
        helper.fcntl.flock(lock, helper.fcntl.LOCK_EX | helper.fcntl.LOCK_NB)  # BlockingIOError while still held
        helper.fcntl.flock(lock, helper.fcntl.LOCK_UN)
        # One helper process answers every project of a request: the next one is not stopped.
        write_tree(w.playground / "other", {"b.js": "b"})
        self.assertEqual(w.copy(2, "game", "other"),
                         {"projects": {"game": {"error": "folder-unusable"}, "other": {"status": "kept", "late": False}}})

    def test_the_command_line_answers_one_request(self):
        w = Workspace(self, {"a.js": "a"})
        request = {"operation": "copy", "workspace": str(w.root), "root": str(w.state), "id": run(1),
                   "projects": [{"name": "game"}]}
        answer = subprocess.run([sys.executable, "-I", "-S", "-B", str(PLUGIN / "project-snapshots.py")],
                                input=json.dumps(request).encode(), capture_output=True, check=True, cwd="/")
        self.assertEqual(json.loads(answer.stdout), {"projects": {"game": {"status": "kept", "late": False}}})


if __name__ == "__main__":
    unittest.main()
