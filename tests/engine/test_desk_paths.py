"""Desk root layout (``aegis_desk.desk_paths``): every path the tools share, derived from one
root, and the runtime directories a tool may create (and the owner files it never creates)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import os
import tempfile
import unittest

from aegis_desk import desk_paths

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")

EXPECTED = {
    "desk_toml": "desk.toml",
    "positions_csv": "positions.csv",
    "rules_dir": "rules",
    "adopted_file": "rules/ADOPTED",
    "research_dir": "research",
    "calendar_toml": "research/calendar.toml",
    "state_dir": "state",
    "heartbeat_dir": "state/heartbeat",
    "desk_state_json": "state/desk_state.json",
    "acks_jsonl": "state/acks.jsonl",
    "rule_state_json": "state/rule_state.json",
    "lock_path": "state/.lock",
    "positions_snapshots_dir": "data/positions",
    "book_dir": "data/book",
    "cache_dir": "data/cache",
    "journal_dir": "journal",
    "levels_dir": "levels",
    "plans_dir": "plans",
    "tickets_dir": "tickets",
    "reports_dir": "reports",
    "incidents_dir": "incidents",
    "fixtures_dir": "fixtures",
}
RUNTIME = ("state", "state/heartbeat", "data/cache", "data/book", "data/positions", "journal", "levels",
           "reports", "incidents")


class ForRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-paths-")
        self.root = os.path.realpath(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_path_is_absolute_and_under_the_root(self) -> None:
        paths = desk_paths.for_root(self.root)
        self.assertEqual(self.root, paths.root)
        fields = {field.name for field in dataclasses.fields(paths)}
        self.assertEqual(set(EXPECTED) | {"root"}, fields)
        for name, rel in EXPECTED.items():
            value = dataclasses.asdict(paths)[name]
            self.assertTrue(os.path.isabs(value), name)
            self.assertEqual(os.path.join(self.root, *rel.split("/")), value, name)
        self.assertEqual(os.path.join(self.root, "fixtures", "tradier"), paths.tradier_fixtures_dir)
        self.assertEqual("tradier", desk_paths.TRADIER_FIXTURES_SUBDIR)

    def test_trailing_separator_and_dot_segments_are_normalised(self) -> None:
        paths = desk_paths.for_root(self.root + os.sep)
        self.assertEqual(self.root, paths.root)
        paths = desk_paths.for_root(os.path.join(self.root, ".", "sub", ".."))
        self.assertEqual(self.root, paths.root)

    def test_relative_root_is_refused(self) -> None:
        with self.assertRaises(desk_paths.DeskPathsError) as ctx:
            desk_paths.for_root("relative/desk")
        self.assertIn("absolute", str(ctx.exception))
        self.assertIn("relative/desk", str(ctx.exception))
        self.assertIsInstance(ctx.exception, ValueError)
        with self.assertRaises(desk_paths.DeskPathsError):
            desk_paths.for_root("")
        with self.assertRaises(desk_paths.DeskPathsError):
            desk_paths.for_root(".")

    def test_missing_directory_is_refused(self) -> None:
        missing = os.path.join(self.root, "nope")
        with self.assertRaises(desk_paths.DeskPathsError) as ctx:
            desk_paths.for_root(missing)
        self.assertIn(missing, str(ctx.exception))

    def test_file_is_refused(self) -> None:
        file_path = os.path.join(self.root, "desk.toml")
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("")
        with self.assertRaises(desk_paths.DeskPathsError):
            desk_paths.for_root(file_path)

    def test_non_string_is_refused(self) -> None:
        with self.assertRaises(desk_paths.DeskPathsError):
            desk_paths.for_root(None)  # type: ignore[arg-type]

    def test_paths_are_frozen(self) -> None:
        paths = desk_paths.for_root(self.root)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            paths.root = "/elsewhere"  # type: ignore[misc]

    def test_relative_helper(self) -> None:
        paths = desk_paths.for_root(self.root)
        self.assertEqual("state/.lock", paths.relative(paths.lock_path))
        self.assertEqual("desk.toml", paths.relative(paths.desk_toml))


class EnsureRuntimeDirsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-paths-")
        self.root = os.path.realpath(self._tmp.name)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def listing(self) -> list:
        found = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames + filenames:
                found.append(os.path.relpath(os.path.join(dirpath, name), self.root).replace(os.sep, "/"))
        return sorted(found)

    def test_creates_exactly_the_runtime_directories(self) -> None:
        created = desk_paths.ensure_runtime_dirs(self.paths)
        self.assertEqual(sorted(os.path.join(self.root, *rel.split("/")) for rel in RUNTIME), created)
        self.assertEqual(sorted(set(RUNTIME) | {"data"}), self.listing())
        for rel in RUNTIME:
            self.assertTrue(os.path.isdir(os.path.join(self.root, *rel.split("/"))), rel)
        self.assertEqual(tuple(RUNTIME), desk_paths.RUNTIME_DIRS)

    def test_never_creates_owner_files_or_rules(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        for rel in ("desk.toml", "positions.csv", "rules", "research", "plans", "tickets", "fixtures",
                    "state/desk_state.json", "state/.lock", "rules/ADOPTED"):
            self.assertFalse(os.path.exists(os.path.join(self.root, *rel.split("/"))), rel)

    def test_idempotent_and_reports_nothing_new_the_second_time(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        before = self.listing()
        self.assertEqual([], desk_paths.ensure_runtime_dirs(self.paths))
        self.assertEqual(before, self.listing())

    def test_existing_content_is_left_alone(self) -> None:
        os.makedirs(os.path.join(self.root, "state"))
        marker = os.path.join(self.root, "state", "desk_state.json")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("{}")
        created = desk_paths.ensure_runtime_dirs(self.paths)
        self.assertNotIn(os.path.join(self.root, "state"), created)
        self.assertIn(os.path.join(self.root, "state", "heartbeat"), created)
        with open(marker, encoding="utf-8") as handle:
            self.assertEqual("{}", handle.read())


class FixtureDeskRootTest(unittest.TestCase):
    def test_fixture_desk_root_resolves(self) -> None:
        paths = desk_paths.for_root(FIXTURE_DESK)
        self.assertTrue(os.path.isfile(paths.desk_toml), paths.desk_toml)
        self.assertTrue(os.path.isfile(paths.positions_csv), paths.positions_csv)
        self.assertTrue(os.path.isdir(paths.tradier_fixtures_dir), paths.tradier_fixtures_dir)
        for name in ("regular", "after_close", "stale", "pre_open", "holiday"):
            self.assertTrue(os.path.isdir(os.path.join(paths.tradier_fixtures_dir, name)), name)
        # The committed fixture root holds only owner files and recorded responses: no runtime state.
        for rel in ("state", "data", "journal", "levels", "reports", "incidents"):
            self.assertFalse(os.path.exists(os.path.join(FIXTURE_DESK, rel)), rel)
        # Decision 2 of the PR 2c addendum: rules/ holds the adopted example rulebook and nothing else.
        self.assertEqual(["ADOPTED", "CHANGELOG.md", "exit_rules-1.0.0.toml"],
                         sorted(os.listdir(os.path.join(FIXTURE_DESK, "rules"))))
        self.assertTrue(os.path.isfile(paths.adopted_file), paths.adopted_file)


if __name__ == "__main__":
    unittest.main()
