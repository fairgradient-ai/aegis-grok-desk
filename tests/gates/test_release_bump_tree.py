"""``scripts/release_bump.py`` on whole trees, proved by the gates themselves.

A copy of this repository (without ``.git``) is bumped to the next patch version; then the copy must pass
``check_manifests.py``, steps 1-4 of ``check.sh`` (the structural lint with the version-pin check, run as the
heredoc check.sh itself carries), ``check_grok_template.py``, and both generators' ``--check`` (steps 9 and 10),
while ``CHANGELOG.md`` and the release record ``docs/RELEASE.md`` keep their bytes. A negative control shows the
pin check has teeth: moving ``plugin.json`` alone fails it. Part of step 7 runs on a bumped copy too: every unit
test module that spells the current version out (a hard-coded release fails there first), always including the
three that once did; the modules that run ``check.sh`` or this module are left out, because they would recurse. The
sentences about the first release keep naming it after a bump. The gate fixture tree, with pins planted, is bumped
and then passes the whole of ``check.sh``. Every copy lives under ``TMPDIR``; nothing in the repository is written.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Tuple

# isort: off
import tests._path  # noqa: F401
from tests.gates._trees import CHECK_SH, GOOD, ROOT
# isort: on

import check_grok_template
import check_manifests
import release_bump

HEREDOC_START = "python3 - <<'PY'"
# Unit-test modules that once compared a tool's output with a hard-coded release; they run on every bumped copy.
VERSION_SENSITIVE = ("tests.tools.test_desk_doctor_tool", "tests.tools.test_gen_tools_doc",
                     "tests.tools.test_opening_bell_tool")
# Modules that run check.sh, the journeys or this module: on a bumped copy they would recurse.
RECURSIVE = ("tests/gates/test_check_sh.py", "tests/gates/test_cli_journeys.py",
             "tests/gates/test_release_bump_tree.py")
# Sentences about the past. The first release is 0.1.0 whatever the current one is, so a bump must leave them.
HISTORY = (("SECURITY.md", "The first release is 0.1.0."), ("docs/aegis-gemini-cli.md", "The first release is 0.1.0."))


def environment() -> Dict[str, str]:
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    for key in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def structural_lint() -> str:
    """The first heredoc of check.sh: steps 1-4 (agents, skills, links/emoji/scripts, version pins)."""
    with open(CHECK_SH, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    start = lines.index(HEREDOC_START)
    end = lines.index("PY", start + 1)
    return "\n".join(lines[start + 1:end]) + "\n"


def run_lint(root: str) -> Tuple[int, str]:
    proc = subprocess.run([sys.executable, "-"], input=structural_lint(), cwd=root, env=environment(),
                          capture_output=True, text=True, timeout=300, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def run_script(root: str, rel: str, *args: str) -> Tuple[int, str]:
    proc = subprocess.run([sys.executable, os.path.join(root, rel)] + list(args), cwd=root, env=environment(),
                          capture_output=True, text=True, timeout=300, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def bump(root: str, version: str, *extra: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = release_bump.main([version, "--root", root] + list(extra))
    if code not in (0, 1):
        raise AssertionError("release_bump exit %d:\n%s" % (code, out.getvalue()))
    return code


def version_sensitive_modules(root: str, version: str) -> List[str]:
    """``VERSION_SENSITIVE`` and every test module under ``root/tests`` whose source names ``version``, dotted."""
    found = set(VERSION_SENSITIVE)
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "tests")):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and rel_dir + "/" + d != "tests/fixtures")
        for name in filenames:
            rel = rel_dir + "/" + name
            if not (name.startswith("test_") and name.endswith(".py")) or rel in RECURSIVE:
                continue
            with open(os.path.join(root, rel), encoding="utf-8") as handle:
                if version in handle.read():
                    found.add(rel[:-3].replace("/", "."))
    return sorted(found)


def run_unittest(root: str, modules: List[str]) -> Tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "unittest"] + modules, cwd=root, env=environment(),
                          capture_output=True, text=True, timeout=900, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def read_text(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def digest(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class RealTreeBumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="aegis-release-tree-")
        cls.base = os.path.realpath(cls._tmp.name)
        with open(os.path.join(ROOT, "plugin.json"), encoding="utf-8") as handle:
            cls.version = json.load(handle)["version"]
        major, minor, patch = (int(part) for part in cls.version.split("."))
        cls.next = "%d.%d.%d" % (major, minor, patch + 1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def copy(self, name: str) -> str:
        root = os.path.join(self.base, name)
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        return root

    def test_bumped_copy_passes_every_version_gate(self) -> None:
        root = self.copy("bumped")
        record = {rel: digest(root, rel) for rel in ("CHANGELOG.md", "docs/RELEASE.md")}
        code, out = run_lint(root)
        self.assertEqual(0, code, out)
        self.assertIn("version pins v%s" % self.version, out)

        self.assertEqual(0, bump(root, self.next))

        self.assertEqual({rel: digest(root, rel) for rel in record}, record)
        result = check_manifests.run(root)
        self.assertEqual([], result["errors"])
        self.assertEqual(self.next, result["version"])
        code, out = run_lint(root)
        self.assertEqual(0, code, out)
        self.assertIn("version pins v%s" % self.next, out)
        template = check_grok_template.run(root)
        self.assertEqual([], template["errors"])
        self.assertEqual(self.next, template["release"])
        for rel in ("scripts/gen_role_skills.py", "scripts/gen_tools_doc.py"):
            code, out = run_script(root, rel, "--check")
            self.assertEqual(0, code, "%s --check on the bumped copy:\n%s" % (rel, out))
        self.assertEqual(0, bump(root, self.next, "--check"))

    def test_moving_plugin_json_alone_fails_the_pin_check(self) -> None:
        root = self.copy("plugin-only")
        path = os.path.join(root, "plugin.json")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace('"version": "%s"' % self.version, '"version": "%s"' % self.next))
        code, out = run_lint(root)
        self.assertEqual(1, code, out)
        self.assertIn("names release tag v%s, but plugin.json declares v%s" % (self.version, self.next), out)
        self.assertNotEqual([], check_manifests.run(root)["errors"])

    def test_unit_tests_that_name_the_release_pass_on_the_bumped_copy(self) -> None:
        root = self.copy("unit")
        modules = version_sensitive_modules(root, self.version)
        self.assertTrue(set(VERSION_SENSITIVE) <= set(modules), modules)
        self.assertEqual(0, bump(root, self.next))
        code, out = run_unittest(root, modules)
        self.assertEqual(0, code, "step 7 on the bumped copy (%d modules):\n%s" % (len(modules), out[-6000:]))
        self.assertRegex(out, r"(?m)^OK\b")

    def test_sentences_about_the_first_release_survive_the_bump(self) -> None:
        root = self.copy("history")
        for rel, sentence in HISTORY:
            self.assertTrue(sentence in read_text(root, rel), "%s does not say %r" % (rel, sentence))
        self.assertEqual(0, bump(root, self.next))
        for rel, sentence in HISTORY:
            text = read_text(root, rel)
            self.assertTrue(sentence in text, "%s no longer says %r after the bump" % (rel, sentence))
            for line in text.split("\n"):
                if "first" in line.lower():
                    self.assertNotIn("v" + self.next, line,
                                     "%s: the bump rewrote a sentence about the first release" % rel)


class FixtureTreeCheckShTests(unittest.TestCase):
    def test_bumped_fixture_tree_passes_the_whole_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-release-good-") as temporary:
            root = os.path.join(os.path.realpath(temporary), "tree")
            shutil.copytree(GOOD, root)
            faq = os.path.join(root, "docs", "FAQ.md")
            with open(faq, "a", encoding="utf-8") as handle:
                handle.write("\nInstall the release tag `v0.1.0`.\n")
            skill = os.path.join(root, "skills", "aegis-desk-charter", "SKILL.md")
            with open(skill, "a", encoding="utf-8") as handle:
                handle.write("\nThis skill ships in the tag v0.1.0.\n")
            template_path = os.path.join(root, "template", "grok-bot.json")
            with open(template_path, encoding="utf-8") as handle:
                template = handle.read()
            old = json.loads(template)["skills"]["aegis-desk-charter"]
            with open(template_path, "w", encoding="utf-8") as handle:
                handle.write(template.replace(old, digest(root, "skills/aegis-desk-charter/SKILL.md")))

            self.assertEqual(0, bump(root, "0.1.1"))

            proc = subprocess.run(["bash", CHECK_SH, root], env=environment(), capture_output=True, text=True,
                                  timeout=900, check=False)
            output = proc.stdout + proc.stderr
            self.assertEqual(0, proc.returncode, output)
            for line in ("version pins v0.1.1", "ok: 8 manifests agree on aegis-desk 0.1.1",
                         "ok: Grok Bot template is unpublished, release 0.1.1", "ok: all repository gates passed"):
                self.assertIn(line, output)


if __name__ == "__main__":
    unittest.main()
