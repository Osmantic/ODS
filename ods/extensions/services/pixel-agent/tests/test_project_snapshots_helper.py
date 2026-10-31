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

    def write(self, name, data, mode, durable=True):
        temporary = os.path.join(self.path, helper.LEFTOVER + secrets.token_hex(8))
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
        # A later change hides the undo: putting files back then would lose it.
        put(w.project / "chars.js", "edited by the owner")
        self.assertIsNone(w.options(run(1))["projects"][0]["restore"])
        self.assertEqual(w.options(run(2)), {"projects": []})

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
        self.assertFalse((w.project / ".ods-restore-0123abcd").exists(), "a restore clears old temporaries")
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

    def test_copies_of_a_project_gone_for_two_weeks_are_dropped(self):
        w = Workspace(self, {"a.js": "a"})
        w.turn(1, lambda: put(w.project / "a.js", "b"))
        write_tree(w.playground / "other", {"x.js": "x"})
        store = w.state / helper.store_key(str(w.root), "game")
        shutil.rmtree(w.project)
        w.call("copy", id=run(2), projects=[{"name": "other"}], collect=True)
        self.assertTrue(store.exists(), "kept while recent: the owner may still put it back")
        old = next((store / "copies").iterdir())
        old.rename(old.with_name("000000000000001" + old.name[15:]))
        w.call("copy", id=run(3), projects=[{"name": "other"}], collect=True)
        self.assertFalse(store.exists())


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

    def test_requests_are_checked(self):
        w = Workspace(self, {"chars.js": ORIGINAL})
        for bad in ({"operation": "copy", "id": run(1), "projects": [{"name": "../x"}]},
                    {"operation": "copy", "id": "x", "projects": [{"name": "game"}]},
                    {"operation": "restore", "project": "game", "snapshot": run(1), "confirm": "not-a-token"},
                    {"operation": "restore", "project": "game/src", "snapshot": run(1)},
                    {"operation": "copy", "id": run(1), "projects": [{"name": "game"}], "limits": {"files": 10 ** 9}},
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

        def swapped(project, steps, objects):
            os.rename(w.project, w.playground / ".g")
            os.symlink(outside, w.project)
            return real(project, steps, objects)
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

        def swapped(project, steps, objects):
            os.rename(w.project / "src", w.project / ".src")
            os.symlink(outside, w.project / "src")
            return real(project, steps, objects)
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

    def test_an_unprivate_copies_folder_is_refused(self):
        w = Workspace(self, {"a.js": "a"})
        w.copy(1)
        os.chmod(w.state, 0o755)
        self.assertEqual(w.restore(run(1)), {"error": "copies-unusable"})

    def test_the_command_line_answers_one_request(self):
        w = Workspace(self, {"a.js": "a"})
        request = {"operation": "copy", "workspace": str(w.root), "root": str(w.state), "id": run(1),
                   "projects": [{"name": "game"}]}
        answer = subprocess.run([sys.executable, "-I", "-S", "-B", str(PLUGIN / "project-snapshots.py")],
                                input=json.dumps(request).encode(), capture_output=True, check=True, cwd="/")
        self.assertEqual(json.loads(answer.stdout), {"projects": {"game": {"status": "kept", "late": False}}})


if __name__ == "__main__":
    unittest.main()
