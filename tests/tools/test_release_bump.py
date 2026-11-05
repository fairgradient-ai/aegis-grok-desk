"""``scripts/release_bump.py``: move the release version everywhere at once, or nothing.

Every test runs ``main(argv)`` in-process on a temporary copy of the gate fixture tree
(``tests/fixtures/gates/good``, a minimal tree in the shape of this repository that passes every gate), with pins
planted in the copy so each kind of file is exercised: a Markdown page, a Cursor rule, a TOML comment, a skill whose
template hash must move, the manifests and the template. A ``.git`` directory or file is built by hand in the copy
for the tag checks; the tool reads it as files. A directory a user who is not root cannot read is faked with mocks of
``os.stat``, ``os.lstat``, ``os.listdir``, ``os.scandir`` and ``open`` (root ignores mode bits, so chmod would prove
nothing here). ``scripts/README.md`` must name every script. Nothing under ``tests/fixtures`` is written. The copied real tree,
the generators' ``--check`` runs and ``check.sh`` on a bumped tree are in ``tests/gates/test_release_bump_tree.py``.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import errno
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from unittest import mock

import check_manifests
import gen_role_skills
import release_bump

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOOD = os.path.join(ROOT, "tests", "fixtures", "gates", "good")
SHA = "0123456789abcdef0123456789abcdef01234567"
MANIFESTS = (
    "plugin.json", ".grok-plugin/plugin.json", ".grok-plugin/marketplace.json", ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json", ".cursor-plugin/plugin.json", "gemini-extension.json",
    ".agents/plugins/marketplace.json",
)
PLANTED = ("docs/FAQ.md", "rules/aegis-desk.mdc", "routines/manifest.toml", "skills/aegis-desk-charter/SKILL.md")
EXPECTED_CHANGES = set(MANIFESTS) | {"template/grok-bot.json"} | set(PLANTED)


def read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8", newline="") as handle:
        return handle.read()


def write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def append(root: str, rel: str, text: str) -> None:
    write(root, rel, read(root, rel) + text)


def sha256_of(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def rehash(root: str, table: str, name: str) -> None:
    """Re-pin one claimed skill's sha256 in the copy's template, editing only the digest text."""
    template = read(root, "template/grok-bot.json")
    old = json.loads(template)[table][name]
    write(root, "template/grok-bot.json", template.replace(old, sha256_of(root, "skills/%s/SKILL.md" % name)))


def snapshot(root: str) -> Dict[str, bytes]:
    found: Dict[str, bytes] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                found[os.path.relpath(path, root)] = ("-> " + os.readlink(path)).encode("utf-8")
                continue
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root).replace(os.sep, "/")] = handle.read()
    return found


@contextlib.contextmanager
def fixture_tree(plant: bool = True) -> Iterator[str]:
    """A temporary copy of the good gate tree; with ``plant``, one ``v0.1.0`` pin in each kind of pinned file."""
    with tempfile.TemporaryDirectory(prefix="aegis-release-bump-") as temporary:
        root = os.path.join(os.path.realpath(temporary), "tree")
        shutil.copytree(GOOD, root)
        if plant:
            append(root, "docs/FAQ.md", "\nInstall the release tag `v0.1.0`.\n")
            append(root, "rules/aegis-desk.mdc", "\nThe checkout is the clean tag v0.1.0.\n")
            append(root, "routines/manifest.toml", "\n# routines run from the clean tag v0.1.0\n")
            append(root, "skills/aegis-desk-charter/SKILL.md", "\nThis skill ships in the tag v0.1.0.\n")
            rehash(root, "skills", "aegis-desk-charter")
        yield root


def run(*argv: str) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = release_bump.main(list(argv))
        except SystemExit as exc:  # argparse usage errors
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def run_json(*argv: str) -> Tuple[int, Dict]:
    code, out, err = run(*(argv + ("--json",)))
    try:
        return code, json.loads(out)
    except ValueError:
        raise AssertionError("not one JSON document (exit %s):\n%s\n%s" % (code, out, err)) from None


def changed(before: Dict[str, bytes], after: Dict[str, bytes]) -> set:
    return {rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel)}


def temporaries(root: str) -> List[str]:
    return sorted(rel for rel in snapshot(root) if ".release-bump-" in rel)


@contextlib.contextmanager
def unreadable(directories: Sequence[str] = (), files: Sequence[str] = ()) -> Iterator[None]:
    """Answer as the kernel answers a user who is not root when each of ``directories`` has mode 000 and each of
    ``files`` cannot be opened: such a directory can itself be stat'ed, but nothing inside it can be reached and it
    cannot be listed. Root ignores mode bits, and the suite runs as root too, so the answer is faked here instead of
    made with chmod; it is the same on every interpreter and for every user."""
    real = {"stat": os.stat, "lstat": os.lstat, "listdir": os.listdir, "scandir": os.scandir, "open": open}
    closed = [os.path.abspath(directory) for directory in directories]
    sealed = {os.path.abspath(name) for name in files}

    def absolute(path: Any) -> Optional[str]:
        if isinstance(path, int):
            return None
        path = os.fspath(path)
        return os.path.abspath(os.fsdecode(path) if isinstance(path, bytes) else path)

    def denied(path: Any, listing: bool) -> bool:
        full = absolute(path)
        if full is None:
            return False
        return any(full.startswith(os.path.join(directory, "")) or (listing and full == directory)
                   for directory in closed)

    def guard(name: str) -> Any:
        def call(path: Any = ".", *args: Any, **kwargs: Any) -> Any:
            if denied(path, name in ("listdir", "scandir")):
                raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
            return real[name](path, *args, **kwargs)
        return call

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if denied(file, False) or absolute(file) in sealed:
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(file))
        return real["open"](file, *args, **kwargs)

    with mock.patch("os.stat", guard("stat")), mock.patch("os.lstat", guard("lstat")), \
            mock.patch("os.listdir", guard("listdir")), mock.patch("os.scandir", guard("scandir")), \
            mock.patch("builtins.open", guarded_open):
        yield


class BumpTests(unittest.TestCase):
    def test_bump_moves_every_version_ref_pin_and_hash_together(self) -> None:
        with fixture_tree() as root:
            before = snapshot(root)
            code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(0, code, out + err)
            after = snapshot(root)
            self.assertEqual(EXPECTED_CHANGES, changed(before, after))
            for rel in MANIFESTS:
                text = read(root, rel)
                self.assertNotIn("0.1.0", text, rel)
            self.assertEqual("0.1.1", json.loads(read(root, "plugin.json"))["version"])
            self.assertEqual("v0.1.1", json.loads(read(root, ".claude-plugin/marketplace.json"))["plugins"][0]["source"]["ref"])
            self.assertEqual("0.1.1", json.loads(read(root, ".claude-plugin/marketplace.json"))["metadata"]["version"])
            template = json.loads(read(root, "template/grok-bot.json"))
            self.assertEqual("0.1.1", template["source"]["release"])
            self.assertIn("(release 0.1.1)", template["description"])
            self.assertEqual(sha256_of(root, "skills/aegis-desk-charter/SKILL.md"), template["skills"]["aegis-desk-charter"])
            for rel in PLANTED:
                self.assertIn("v0.1.1", read(root, rel), rel)
                self.assertNotIn("v0.1.0", read(root, rel), rel)
            result = check_manifests.run(root)
            self.assertEqual([], result["errors"])
            self.assertEqual("0.1.1", result["version"])
            self.assertIn("written: %d file(s)" % len(EXPECTED_CHANGES), out)

    def test_edits_only_the_version_text_and_keeps_formatting(self) -> None:
        with fixture_tree() as root:
            before = {rel: read(root, rel) for rel in EXPECTED_CHANGES}
            old_hash = json.loads(before["template/grok-bot.json"])["skills"]["aegis-desk-charter"]
            self.assertEqual(0, run("0.1.1", "--root", root)[0])
            for rel in MANIFESTS:
                expected = before[rel].replace('"0.1.0"', '"0.1.1"').replace('"v0.1.0"', '"v0.1.1"')
                self.assertEqual(expected, read(root, rel), rel)
            new_hash = sha256_of(root, "skills/aegis-desk-charter/SKILL.md")
            expected = (before["template/grok-bot.json"].replace('"0.1.0"', '"0.1.1"')
                        .replace("release 0.1.0", "release 0.1.1").replace(old_hash, new_hash))
            self.assertEqual(expected, read(root, "template/grok-bot.json"))
            for rel in PLANTED:
                self.assertEqual(before[rel].replace("v0.1.0", "v0.1.1"), read(root, rel), rel)

    def test_changes_are_listed_with_what_moves(self) -> None:
        with fixture_tree() as root:
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["written"])
            self.assertEqual("write", payload["mode"])
            self.assertEqual(("0.1.0", "0.1.1"), (payload["current"], payload["next"]))
            self.assertEqual(EXPECTED_CHANGES, {entry["file"] for entry in payload["changes"]})
            what = {entry["file"]: entry["what"] for entry in payload["changes"]}
            self.assertIn("version", what["plugin.json"])
            self.assertIn("source.ref", what[".grok-plugin/marketplace.json"])
            self.assertIn("source.release", what["template/grok-bot.json"])
            self.assertIn("description release", what["template/grok-bot.json"])
            self.assertIn("skills.aegis-desk-charter sha256", what["template/grok-bot.json"])
            self.assertIn("1 tag pin(s)", what["docs/FAQ.md"])
            self.assertEqual([], payload["problems"])

    def test_numeric_order_and_a_second_bump(self) -> None:
        with fixture_tree() as root:
            self.assertEqual(0, run("0.1.9", "--root", root)[0])
            code, out, err = run("0.1.10", "--root", root)
            self.assertEqual(0, code, out + err)
            self.assertEqual("0.1.10", json.loads(read(root, "plugin.json"))["version"])
            self.assertIn("v0.1.10", read(root, "docs/FAQ.md"))
            self.assertEqual([], check_manifests.run(root)["errors"])
            code, out, _err = run("0.1.9", "--root", root)
            self.assertEqual(3, code, out)
            self.assertIn("not greater than the current version 0.1.10", out)

    def test_major_bump(self) -> None:
        with fixture_tree() as root:
            self.assertEqual(0, run("1.0.0", "--root", root)[0])
            self.assertEqual("1.0.0", json.loads(read(root, "gemini-extension.json"))["version"])

    def test_file_mode_is_kept(self) -> None:
        with fixture_tree() as root:
            os.chmod(os.path.join(root, "docs/FAQ.md"), 0o640)
            self.assertEqual(0, run("0.1.1", "--root", root)[0])
            self.assertEqual(0o640, stat.S_IMODE(os.stat(os.path.join(root, "docs/FAQ.md")).st_mode))

    def test_other_json_files_move_their_pins_and_still_parse(self) -> None:
        with fixture_tree() as root:
            write(root, "docs/samples.json", '{\n  "install": "the tag v0.1.0",\n  "plain": "0.1.0"\n}\n')
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            self.assertEqual({"install": "the tag v0.1.1", "plain": "0.1.0"}, json.loads(read(root, "docs/samples.json")))
            self.assertIn({"file": "docs/samples.json", "what": "install"}, payload["changes"])

    def test_json_pin_the_text_edit_cannot_reach_is_refused(self) -> None:
        with fixture_tree() as root:
            # An escaped pin parses as v0.1.0 but is not that text; the edit would leave it behind.
            write(root, "docs/samples.json", '{"install": "the tag v0.1.0", "escaped": "\\u0076\\u0030.1.0"}\n')
            self.assertEqual("v0.1.0", json.loads(read(root, "docs/samples.json"))["escaped"])
            code, out, _err = run("0.1.1", "--root", root)
            self.assertEqual(3, code, out)
            self.assertIn("docs/samples.json: the version cannot be moved without changing another value (escaped)", out)

    def test_crlf_line_endings_are_kept(self) -> None:
        with fixture_tree() as root:
            write(root, "docs/WINDOWS.md", "# Windows\r\n\r\nThe tag `v0.1.0`.\r\n")
            self.assertEqual(0, run("0.1.1", "--root", root)[0])
            self.assertEqual("# Windows\r\n\r\nThe tag `v0.1.1`.\r\n", read(root, "docs/WINDOWS.md"))


class RecordTests(unittest.TestCase):
    """CHANGELOG history and the release record are never written."""

    def test_changelog_is_never_written(self) -> None:
        with fixture_tree() as root:
            append(root, "CHANGELOG.md", "\n## 0.1.0\n\nTagged `v0.1.0`; installs from v0.1.0.\n")
            before = read(root, "CHANGELOG.md")
            self.assertEqual(0, run("0.1.1", "--root", root)[0])
            self.assertEqual(before, read(root, "CHANGELOG.md"))

    def test_release_record_without_a_pin_is_left_as_it_is(self) -> None:
        with fixture_tree() as root:
            record = "# Release 0.1.0\n\nThe receipt of release 0.1.0. The tag is the letter v and 0.1.0.\n"
            write(root, "docs/RELEASE.md", record)
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            self.assertEqual(record, read(root, "docs/RELEASE.md"))
            self.assertNotIn("docs/RELEASE.md", {entry["file"] for entry in payload["changes"]})
            self.assertNotIn("docs/RELEASE.md", {entry["file"] for entry in payload["review"]})

    def test_release_record_that_names_a_tag_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "docs/RELEASE.md", "# Release\n\nTagged `v0.1.0`.\n")
            before = snapshot(root)
            code, out, _err = run("0.1.1", "--root", root)
            self.assertEqual(3, code, out)
            self.assertIn("docs/RELEASE.md: names v0.1.0", out)
            self.assertIn("never rewritten", out)
            self.assertEqual(before, snapshot(root))


class RefusalTests(unittest.TestCase):
    def assert_refused(self, root: str, argv: Tuple[str, ...], *needles: str, code: int = 3) -> str:
        before = snapshot(root)
        got, out, err = run(*argv)
        self.assertEqual(code, got, out + err)
        for needle in needles:
            self.assertIn(needle, out + err)
        self.assertEqual(before, snapshot(root), "a refused run wrote something")
        self.assertEqual([], temporaries(root))
        return out + err

    def test_versions_that_are_not_plain(self) -> None:
        with fixture_tree() as root:
            for version in ("v0.1.1", "0.1", "0.1.1-rc1", "0.1.1 ", " 0.1.1", "01.1.0", "0.01.1", "0.1.01",
                            "0.1.1.0", "", "0.1.1+build", "０.1.1", "0.1.١"):
                self.assert_refused(root, (version, "--root", root), "not a plain X.Y.Z version", code=2)

    def test_not_greater(self) -> None:
        with fixture_tree() as root:
            self.assert_refused(root, ("0.1.0", "--root", root), "not greater than the current version 0.1.0")
            self.assert_refused(root, ("0.0.9", "--root", root), "not greater than the current version 0.1.0")
            self.assert_refused(root, ("0.0.9", "--check", "--root", root),
                                "not greater than the current version 0.1.0")

    def test_stale_pin_is_refused_with_its_place(self) -> None:
        with fixture_tree() as root:
            append(root, "docs/FAQ.md", "Older: v0.0.9.\n")
            self.assert_refused(root, ("0.1.1", "--root", root), "docs/FAQ.md:", "names v0.0.9")

    def test_pin_to_the_next_version_already_present_is_refused(self) -> None:
        with fixture_tree() as root:
            append(root, "docs/FAQ.md", "Next: v0.1.1.\n")
            self.assert_refused(root, ("0.1.1", "--root", root), "docs/FAQ.md:", "names v0.1.1")

    def test_manifest_out_of_step_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, ".grok-plugin/plugin.json",
                  read(root, ".grok-plugin/plugin.json").replace('"0.1.0"', '"0.0.9"'))
            self.assert_refused(root, ("0.1.1", "--root", root), ".grok-plugin/plugin.json", "version '0.0.9'")

    def test_marketplace_ref_out_of_step_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, ".agents/plugins/marketplace.json",
                  read(root, ".agents/plugins/marketplace.json").replace('"v0.1.0"', '"main"'))
            self.assert_refused(root, ("0.1.1", "--root", root), ".agents/plugins/marketplace.json",
                                "source.ref 'main'")

    def test_template_release_out_of_step_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "template/grok-bot.json",
                  read(root, "template/grok-bot.json").replace('"release": "0.1.0"', '"release": "0.0.9"'))
            self.assert_refused(root, ("0.1.1", "--root", root), "template/grok-bot.json", "source.release '0.0.9'")

    def test_template_description_naming_another_release_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "template/grok-bot.json",
                  read(root, "template/grok-bot.json").replace("(release 0.1.0)", "(release 0.0.9)"))
            self.assert_refused(root, ("0.1.1", "--root", root), "template/grok-bot.json", "release 0.0.9")

    def test_template_hash_that_does_not_match_its_file_is_refused(self) -> None:
        with fixture_tree() as root:
            append(root, "skills/aegis-desk-charter/SKILL.md", "An edit nobody hashed.\n")
            self.assert_refused(root, ("0.1.1", "--root", root),
                                "skills.aegis-desk-charter sha256 does not match skills/aegis-desk-charter/SKILL.md")

    def test_pinned_file_that_is_a_symbolic_link_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "notes/pin.txt", "The tag v0.1.0.\n")
            os.symlink(os.path.join("..", "notes", "pin.txt"), os.path.join(root, "docs", "LINK.md"))
            self.assert_refused(root, ("0.1.1", "--root", root), "docs/LINK.md", "symbolic link")

    def test_unreadable_plugin_json_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "plugin.json", "{not json")
            self.assert_refused(root, ("0.1.1", "--root", root), "plugin.json")

    def test_prerelease_current_version_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "plugin.json", read(root, "plugin.json").replace('"0.1.0"', '"0.1.0-dev"'))
            self.assert_refused(root, ("0.1.1", "--root", root), "plugin.json", "0.1.0-dev")

    def test_every_problem_is_reported_at_once(self) -> None:
        with fixture_tree() as root:
            append(root, "docs/FAQ.md", "Older: v0.0.9.\n")
            append(root, "rules/aegis-desk.mdc", "Older: v0.0.8.\n")
            output = self.assert_refused(root, ("0.1.1", "--root", root), "names v0.0.9", "names v0.0.8")
            self.assertIn("refused: nothing written", output)


class CheckModeTests(unittest.TestCase):
    def test_check_prints_the_plan_writes_nothing_and_exits_1(self) -> None:
        with fixture_tree() as root:
            before = snapshot(root)
            code, out, err = run("0.1.1", "--check", "--root", root)
            self.assertEqual(1, code, out + err)
            self.assertEqual(before, snapshot(root))
            for rel in sorted(EXPECTED_CHANGES):
                self.assertIn("change: %s:" % rel, out)
            self.assertIn("would change %d file(s); nothing written (--check)" % len(EXPECTED_CHANGES), out)

    def test_check_json(self) -> None:
        with fixture_tree() as root:
            code, payload = run_json("0.1.1", "--check", "--root", root)
            self.assertEqual(1, code)
            self.assertEqual("check", payload["mode"])
            self.assertFalse(payload["written"])
            self.assertFalse(payload["ok"])
            self.assertEqual(EXPECTED_CHANGES, {entry["file"] for entry in payload["changes"]})

    def test_check_with_the_current_version_verifies_the_tree(self) -> None:
        with fixture_tree() as root:
            code, out, err = run("0.1.0", "--check", "--root", root)
            self.assertEqual(0, code, out + err)
            self.assertIn("nothing to change: every pin already names 0.1.0", out)
            self.assertEqual(0, run("0.1.1", "--root", root)[0])
            code, out, err = run("0.1.1", "--check", "--root", root)
            self.assertEqual(0, code, out + err)

    def test_check_with_the_current_version_still_refuses_a_stale_pin(self) -> None:
        with fixture_tree() as root:
            append(root, "docs/FAQ.md", "Older: v0.0.9.\n")
            code, out, _err = run("0.1.0", "--check", "--root", root)
            self.assertEqual(3, code, out)
            self.assertIn("names v0.0.9", out)


class ReviewTests(unittest.TestCase):
    def test_plain_mentions_are_listed_for_review_and_left_alone(self) -> None:
        with fixture_tree() as root:
            append(root, "docs/FAQ.md", "Status: release 0.1.0 is being prepared.\n")
            lines = read(root, "docs/FAQ.md").split("\n")
            number = next(i for i, line in enumerate(lines, 1) if line.startswith("Status: release 0.1.0"))
            code, out, _err = run("0.1.1", "--check", "--root", root)
            self.assertEqual(1, code, out)
            self.assertIn("review: docs/FAQ.md:%d: names 0.1.0 without the leading v" % number, out)
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            review = {(entry["file"], entry["line"]) for entry in payload["review"]}
            self.assertIn(("docs/FAQ.md", number), review)
            self.assertIn("Status: release 0.1.0 is being prepared.", read(root, "docs/FAQ.md"))
            # A skill's own metadata version is not a release pin and is not listed.
            self.assertNotIn("skills/aegis-desk-charter/SKILL.md", {entry["file"] for entry in payload["review"]})
            self.assertNotIn("CHANGELOG.md", {entry["file"] for entry in payload["review"]})
            # After the bump the line names an older version; only the current one is reviewed.
            code, payload = run_json("0.1.2", "--root", root)
            self.assertEqual(0, code)
            self.assertNotIn(("docs/FAQ.md", number), {(entry["file"], entry["line"]) for entry in payload["review"]})


class GeneratedFileTests(unittest.TestCase):
    """Role skills and docs/TOOLS.md move with the version when the tree carries their generators (check.sh steps
    9 and 10 run only then)."""

    AGENT = "agents/desk-lead.md"
    ROLE = "skills/aegis-role-desk-lead/SKILL.md"

    def add_role_skill(self, root: str) -> None:
        shutil.copyfile(os.path.join(ROOT, "scripts", "gen_role_skills.py"),
                        os.path.join(root, "scripts", "gen_role_skills.py"))
        self.render_role(root, "0.1.0")
        template = read(root, "template/grok-bot.json")
        self.assertIn('"roleSkills": {}', template)
        write(root, "template/grok-bot.json", template.replace(
            '"roleSkills": {}', '"roleSkills": {\n    "aegis-role-desk-lead": "%s"\n  }' % sha256_of(root, self.ROLE)))

    def render_role(self, root: str, release: str) -> str:
        text = gen_role_skills.render_skill(gen_role_skills.read_agent(root, self.AGENT), release)
        write(root, self.ROLE, text)
        return text

    def test_role_skill_is_rendered_with_the_new_version_and_rehashed(self) -> None:
        with fixture_tree() as root:
            self.add_role_skill(root)
            code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(0, code, out + err)
            expected = gen_role_skills.render_skill(gen_role_skills.read_agent(root, self.AGENT), "0.1.1")
            self.assertEqual(expected, read(root, self.ROLE))
            self.assertIn('  version: "0.1.1"', read(root, self.ROLE))
            template = json.loads(read(root, "template/grok-bot.json"))
            self.assertEqual(sha256_of(root, self.ROLE), template["roleSkills"]["aegis-role-desk-lead"])

    def test_agent_pin_moves_in_the_agent_and_its_role_skill(self) -> None:
        with fixture_tree() as root:
            agent = read(root, self.AGENT)
            self.assertIn("## System prompt\n", agent)
            write(root, self.AGENT, agent.replace("## System prompt\n", "## System prompt\n\nThe clean tag v0.1.0.\n", 1))
            self.add_role_skill(root)
            self.assertIn("The clean tag v0.1.0.", read(root, self.ROLE))
            code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(0, code, out + err)
            self.assertIn("The clean tag v0.1.1.", read(root, self.AGENT))
            expected = gen_role_skills.render_skill(gen_role_skills.read_agent(root, self.AGENT), "0.1.1")
            self.assertEqual(expected, read(root, self.ROLE))
            self.assertIn("source_sha256: %s" % sha256_of(root, self.AGENT), read(root, self.ROLE))

    def test_role_skill_drift_is_refused(self) -> None:
        with fixture_tree() as root:
            self.add_role_skill(root)
            write(root, self.ROLE, read(root, self.ROLE) + "A hand edit.\n")
            rehash(root, "roleSkills", "aegis-role-desk-lead")
            before = snapshot(root)
            code, out, _err = run("0.1.1", "--root", root)
            self.assertEqual(3, code, out)
            self.assertIn(self.ROLE, out)
            self.assertIn("gen_role_skills.py", out)
            self.assertEqual(before, snapshot(root))

    def test_tools_doc_tool_versions_move(self) -> None:
        with fixture_tree() as root:
            write(root, "scripts/gen_tools_doc.py", '"""A stand-in generator."""\nfrom __future__ import annotations\n')
            write(root, "docs/TOOLS.md", "# Tools\n\n## ack\n\n- tool version: 0.1.0\n\n## book\n\n- tool version: 0.1.0\n")
            code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(0, code, out + err)
            self.assertEqual("# Tools\n\n## ack\n\n- tool version: 0.1.1\n\n## book\n\n- tool version: 0.1.1\n",
                             read(root, "docs/TOOLS.md"))

    def test_tools_doc_drift_is_refused(self) -> None:
        with fixture_tree() as root:
            write(root, "scripts/gen_tools_doc.py", '"""A stand-in generator."""\nfrom __future__ import annotations\n')
            write(root, "docs/TOOLS.md", "# Tools\n\n## ack\n\n- tool version: 0.0.9\n")
            code, out, _err = run("0.1.1", "--root", root)
            self.assertEqual(3, code, out)
            self.assertIn("docs/TOOLS.md", out)
            self.assertIn("gen_tools_doc.py", out)

    def test_tools_doc_without_its_generator_keeps_tool_versions(self) -> None:
        with fixture_tree() as root:
            write(root, "docs/TOOLS.md", "# Tools\n\n- tool version: 0.1.0\n")
            self.assertEqual(0, run("0.1.1", "--root", root)[0])
            self.assertEqual("# Tools\n\n- tool version: 0.1.0\n", read(root, "docs/TOOLS.md"))


class TagTests(unittest.TestCase):
    """The tag check reads ``.git`` as files: loose refs, ``packed-refs``, a worktree's ``.git`` file and its
    ``commondir``. It refuses what it cannot read rather than guess."""

    @staticmethod
    def git_dir(root: str) -> str:
        path = os.path.join(root, ".git")
        os.makedirs(os.path.join(path, "refs", "tags"))
        write(root, ".git/HEAD", "ref: refs/heads/main\n")
        return path

    def assert_tag_refused(self, root: str, *needles: str) -> None:
        before = snapshot(root)
        code, out, _err = run("0.1.1", "--root", root)
        self.assertEqual(3, code, out)
        for needle in needles:
            self.assertIn(needle, out)
        self.assertEqual(before, snapshot(root))

    def test_loose_tag_refuses(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            write(root, ".git/refs/tags/v0.1.1", SHA + "\n")
            self.assert_tag_refused(root, "tag v0.1.1 already exists")

    def test_packed_tag_refuses(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            write(root, ".git/packed-refs", "# pack-refs with: peeled fully-peeled sorted \n"
                  "%s refs/heads/main\n%s refs/tags/v0.1.1\n^%s\n" % (SHA, SHA, SHA[::-1]))
            self.assert_tag_refused(root, "tag v0.1.1 already exists")

    def test_similar_tags_do_not_count(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            write(root, ".git/refs/tags/v0.1.10", SHA + "\n")
            write(root, ".git/packed-refs", "%s refs/tags/v0.1.1-rc1\n%s refs/heads/v0.1.1\n%s refs/tags/v0.1.0\n"
                  % (SHA, SHA, SHA))
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            self.assertEqual({"name": "v0.1.1", "checkout": True, "exists": False}, {
                key: payload["tag"][key] for key in ("name", "checkout", "exists")})
            self.assertEqual([], [note for note in payload["notes"] if "v0.1.0 has no local tag" in note])

    def test_missing_current_tag_is_noted(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            self.assertTrue(any("v0.1.0 has no local tag" in note for note in payload["notes"]), payload["notes"])

    def worktree(self, base: str, root: str, relative: bool) -> str:
        """A main repository's ``.git`` under ``base/main`` and ``root/.git`` as a worktree's pointer file."""
        common = os.path.join(base, "main", ".git")
        os.makedirs(os.path.join(common, "refs", "tags"))
        gitdir = os.path.join(common, "worktrees", "tree")
        os.makedirs(gitdir)
        with open(os.path.join(gitdir, "commondir"), "w", encoding="utf-8") as handle:
            handle.write("../..\n")
        pointer = os.path.relpath(gitdir, root) if relative else gitdir
        write(root, ".git", "gitdir: %s\n" % pointer)
        return common

    def test_worktree_reads_the_common_directory(self) -> None:
        for relative in (False, True):
            with self.subTest(relative=relative), fixture_tree() as root:
                common = self.worktree(os.path.dirname(root), root, relative)
                with open(os.path.join(common, "packed-refs"), "w", encoding="utf-8") as handle:
                    handle.write("%s refs/tags/v0.1.1\n" % SHA)
                self.assert_tag_refused(root, "tag v0.1.1 already exists")

    def test_worktree_loose_tag_in_the_common_directory(self) -> None:
        with fixture_tree() as root:
            common = self.worktree(os.path.dirname(root), root, False)
            with open(os.path.join(common, "refs", "tags", "v0.1.1"), "w", encoding="utf-8") as handle:
                handle.write(SHA + "\n")
            self.assert_tag_refused(root, "tag v0.1.1 already exists")

    def test_worktree_without_the_tag_proceeds(self) -> None:
        with fixture_tree() as root:
            common = self.worktree(os.path.dirname(root), root, True)
            code, payload = run_json("0.1.1", "--root", root)
            self.assertEqual(0, code, payload)
            self.assertTrue(payload["tag"]["checkout"])
            self.assertFalse(payload["tag"]["exists"])
            self.assertEqual(os.path.realpath(common), os.path.realpath(payload["tag"]["refs"]))

    def test_lookup_function(self) -> None:
        with fixture_tree() as root:
            self.assertEqual((False, False), release_bump.tag_lookup(root, "v0.1.1")[:2])
            self.git_dir(root)
            self.assertEqual((True, False), release_bump.tag_lookup(root, "v0.1.1")[:2])
            write(root, ".git/refs/tags/v0.1.1", SHA + "\n")
            self.assertEqual((True, True), release_bump.tag_lookup(root, "v0.1.1")[:2])

    def test_unreadable_git_metadata_refuses(self) -> None:
        cases = {
            "malformed pointer": lambda root: write(root, ".git", "not a pointer\n"),
            "pointer to nothing": lambda root: write(root, ".git", "gitdir: %s\n" % os.path.join(root, "nowhere")),
            "empty pointer": lambda root: write(root, ".git", ""),
        }
        for label, make in cases.items():
            with self.subTest(label), fixture_tree() as root:
                make(root)
                self.assert_tag_refused(root, "cannot read the tags of", ".git")

    def assert_refused_while_unreadable(self, root: str, directories: Sequence[str] = (),
                                        files: Sequence[str] = ()) -> str:
        before = snapshot(root)
        with unreadable(directories, files):
            code, out, _err = run("0.1.1", "--root", root)
        self.assertEqual(3, code, out)
        self.assertIn("cannot read the tags of", out)
        self.assertNotIn("is not a local tag", out)
        self.assertEqual(before, snapshot(root), "nothing is written")
        return out

    def test_an_unreadable_tags_directory_refuses(self) -> None:
        # The tag exists, but a user who is not root cannot see into refs/tags: never "not a local tag".
        with fixture_tree() as root:
            self.git_dir(root)
            write(root, ".git/refs/tags/v0.1.1", SHA + "\n")
            out = self.assert_refused_while_unreadable(root, [os.path.join(root, ".git", "refs", "tags")])
            self.assertIn(os.path.join(".git", "refs", "tags", "v0.1.1"), out)

    def test_an_unreadable_git_directory_refuses(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            write(root, ".git/packed-refs", "%s refs/tags/v0.1.1\n" % SHA)
            self.assert_refused_while_unreadable(root, [os.path.join(root, ".git")])

    def test_an_unreadable_packed_refs_file_refuses(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            write(root, ".git/packed-refs", "%s refs/tags/v0.1.1\n" % SHA)
            self.assert_refused_while_unreadable(root, files=[os.path.join(root, ".git", "packed-refs")])

    def test_an_unreadable_worktree_directory_refuses(self) -> None:
        # The worktree's own directory hides its commondir file; reading the worktree directory as if it were the
        # common one would miss the tag.
        with fixture_tree() as root:
            common = self.worktree(os.path.dirname(root), root, False)
            with open(os.path.join(common, "refs", "tags", "v0.1.1"), "w", encoding="utf-8") as handle:
                handle.write(SHA + "\n")
            self.assert_refused_while_unreadable(root, [os.path.join(common, "worktrees", "tree")])

    def test_an_unreadable_common_directory_refuses(self) -> None:
        with fixture_tree() as root:
            common = self.worktree(os.path.dirname(root), root, True)
            self.assert_refused_while_unreadable(root, [common])

    def test_the_fake_permission_answer_matches_the_kernel_s(self) -> None:
        # The helper above answers as the kernel does for a user who is not root: the directory itself can be
        # stat'ed, an entry inside it cannot, and a missing entry inside it is not reported as missing.
        with tempfile.TemporaryDirectory(prefix="aegis-release-bump-perm-") as temporary:
            closed = os.path.join(temporary, "closed")
            os.makedirs(closed)
            with unreadable([closed]):
                self.assertTrue(stat.S_ISDIR(os.stat(closed).st_mode))
                for probe in (os.path.join(closed, "missing"), os.path.join(closed, "a", "b")):
                    with self.assertRaises(PermissionError):
                        os.lstat(probe)
                with self.assertRaises(PermissionError):
                    os.listdir(closed)
                self.assertFalse(os.path.lexists(os.path.join(closed, "missing")))
            self.assertEqual([], os.listdir(closed))

    def test_reftable_repository_refuses(self) -> None:
        with fixture_tree() as root:
            self.git_dir(root)
            os.makedirs(os.path.join(root, ".git", "reftable"))
            self.assert_tag_refused(root, "cannot read the tags of", "reftable")

    def test_not_a_checkout_is_said_out_loud(self) -> None:
        with fixture_tree() as root:
            code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(0, code, out + err)
            self.assertIn("tag: not a git checkout; no local tag v0.1.1 can exist here", out)


class AtomicWriteTests(unittest.TestCase):
    """Every file is staged next to its target first; a failure while staging replaces nothing, and a failure while
    replacing restores every file already replaced."""

    def test_failure_while_staging_writes_nothing(self) -> None:
        real = tempfile.mkstemp
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError(28, "No space left on device")
            return real(*args, **kwargs)

        with fixture_tree() as root:
            before = snapshot(root)
            with mock.patch("tempfile.mkstemp", side_effect=flaky):
                code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(4, code, out + err)
            self.assertIn("No space left on device", out)
            self.assertIn("nothing written", out)
            self.assertEqual(before, snapshot(root))
            self.assertEqual([], temporaries(root))

    def test_failure_while_replacing_restores_every_file(self) -> None:
        real = os.replace
        calls = {"n": 0}

        def flaky(source, target):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError(5, "Input/output error")
            return real(source, target)

        with fixture_tree() as root:
            before = snapshot(root)
            with mock.patch("os.replace", side_effect=flaky):
                code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(4, code, out + err)
            self.assertIn("Input/output error", out)
            self.assertIn("restored 2 file(s)", out)
            self.assertEqual(before, snapshot(root))
            self.assertEqual([], temporaries(root))

    def test_a_restore_that_fails_is_named(self) -> None:
        real = os.replace
        calls = {"n": 0}

        def broken(source, target):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise OSError(5, "Input/output error")
            return real(source, target)

        with fixture_tree() as root:
            with mock.patch("os.replace", side_effect=broken):
                code, out, err = run("0.1.1", "--root", root)
            self.assertEqual(4, code, out + err)
            self.assertIn("could not restore 2 file(s)", out)
            self.assertEqual([], temporaries(root))

    def test_a_file_changed_since_the_plan_is_not_overwritten(self) -> None:
        with fixture_tree() as root:
            plan = release_bump.plan(root, "0.1.1")
            self.assertEqual([], plan.problems)
            append(root, "docs/FAQ.md", "An edit made after the plan.\n")
            before = snapshot(root)
            ok, lines = release_bump.apply(root, plan.changes)
            self.assertFalse(ok)
            self.assertTrue(any("docs/FAQ.md changed since the plan" in line for line in lines), lines)
            self.assertEqual(before, snapshot(root))
            self.assertEqual([], temporaries(root))


class ParityTests(unittest.TestCase):
    """The pins the tool moves are exactly the ones check.sh step 4 checks."""

    def test_file_set_matches_check_sh_step_4(self) -> None:
        with open(os.path.join(ROOT, "scripts", "check.sh"), encoding="utf-8") as handle:
            check_sh = handle.read()
        self.assertIn('EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv"}', check_sh)
        self.assertEqual({".git", "__pycache__", "node_modules", ".venv"}, set(release_bump.EXCLUDED_DIRS))
        self.assertIn('EXCLUDED_PREFIXES = ("tests/fixtures/", "LICENSES/")', check_sh)
        self.assertEqual(("tests/fixtures/", "LICENSES/"), release_bump.EXCLUDED_PREFIXES)
        self.assertIn('pinned_files = tree((".md", ".mdc", ".json", ".toml"), extra_excluded=("CHANGELOG.md",))',
                      check_sh)
        self.assertEqual((".md", ".mdc", ".json", ".toml"), release_bump.PIN_SUFFIXES)
        self.assertIn('PIN_RE = re.compile(r"\\bv\\d+\\.\\d+\\.\\d+\\b")', check_sh)
        self.assertEqual(r"\bv\d+\.\d+\.\d+\b", release_bump.PIN_RE.pattern)
        self.assertIn("CHANGELOG.md", release_bump.RECORD_FILES)

    def test_manifest_list_is_the_gate_s_own(self) -> None:
        self.assertEqual(check_manifests.MANIFESTS, release_bump.MANIFESTS)
        self.assertEqual(check_manifests.TEMPLATE, release_bump.TEMPLATE)

    def test_the_real_tree_is_consistent_at_its_version(self) -> None:
        version = json.loads(read(ROOT, "plugin.json"))["version"]
        plan = release_bump.plan(ROOT, version, verify_only=True)
        self.assertEqual([], plan.problems)
        self.assertEqual({}, plan.changes)

    def test_not_a_desk_tool(self) -> None:
        # gen_tools_doc treats a script that defines build_parser() and main() as a desk tool.
        self.assertFalse(hasattr(release_bump, "build_parser"))


class ScriptsIndexTests(unittest.TestCase):
    """``scripts/README.md`` names every script in ``scripts/``; release_bump.py is a repository gate there."""

    def setUp(self) -> None:
        self.readme = read(ROOT, "scripts/README.md")

    def test_listed_in_the_repository_gates_table(self) -> None:
        section = self.readme.split("## Repository gates", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.split("\n") if line.startswith("| `release_bump.py` |")]
        self.assertEqual(1, len(rows), "scripts/README.md, Repository gates: one row for release_bump.py")
        for needle in ("every file or none", "already tagged", "CHANGELOG.md", "docs/RELEASE.md", "--check"):
            self.assertIn(needle, rows[0])

    def test_every_script_is_named(self) -> None:
        scripts = sorted(name for name in os.listdir(os.path.join(ROOT, "scripts")) if name.endswith((".py", ".sh")))
        self.assertEqual([], [name for name in scripts if "`%s`" % name not in self.readme])


if __name__ == "__main__":
    unittest.main()
