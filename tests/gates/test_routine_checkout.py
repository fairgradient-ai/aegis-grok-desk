"""``scripts/routine.py`` run the way a Grok routine runs it: a fresh interpreter on the checkout (a copy here, which
is no git checkout, so the pin check refuses it). The pin check runs before any step tool is imported, so a modified
step module's code never runs on a refused checkout; and the routine neither writes bytecode into the checkout nor
loads a planted ``.pyc`` in place of the checkout's source (PR 2d review, lens 2). The copies live under ``TMPDIR``;
nothing under the repository or ``tests/fixtures`` is written."""

from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import List, Tuple

# isort: off
import tests._path  # noqa: F401
from tests.gates._trees import ROOT
# isort: on

FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
EOD_NOW = "2026-11-03T21:35:00Z"
REFUSED = "the desk checkout is not the pinned release"


def environment() -> dict:
    """What the interpreter needs and nothing that stops bytecode: the routine itself must not write any."""
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


class CopiedCheckout(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-routine-checkout-")
        self.addCleanup(self._tmp.cleanup)
        base = os.path.realpath(self._tmp.name)
        self.checkout = os.path.join(base, "checkout")
        os.makedirs(self.checkout)
        shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(self.checkout, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, "routines"), os.path.join(self.checkout, "routines"))
        shutil.copyfile(os.path.join(ROOT, "plugin.json"), os.path.join(self.checkout, "plugin.json"))
        self.desk = os.path.join(base, "desk")
        shutil.copytree(FIXTURE_DESK, self.desk)
        self.marker = os.path.join(base, "marker.txt")

    def plant(self, rel: str) -> None:
        """Append to the copied module a line that leaves the marker file when the module's code runs."""
        with open(os.path.join(self.checkout, *rel.split("/")), "a", encoding="utf-8") as handle:
            handle.write("\nopen(%r, 'w', encoding='utf-8').close()\n" % self.marker)

    def routine(self, *extra: str) -> Tuple[int, str]:
        command = [sys.executable, os.path.join(self.checkout, "scripts", "routine.py"), "eod", "--slot", "16:35",
                   "--offline", "--fixture-set", "after_close", "--desk-root", self.desk, "--now", EOD_NOW] + list(extra)
        proc = subprocess.run(command, cwd=self.desk, env=environment(), capture_output=True, text=True, timeout=120,
                              check=False)
        return proc.returncode, proc.stdout + proc.stderr

    def bytecode(self) -> List[str]:
        found: List[str] = []
        for folder, dirs, files in os.walk(self.checkout):
            found.extend(os.path.relpath(os.path.join(folder, name), self.checkout) for name in dirs
                         if name == "__pycache__")
            found.extend(os.path.relpath(os.path.join(folder, name), self.checkout) for name in files
                         if name.endswith((".pyc", ".pyo")))
        return found


class PinBeforeStepsTest(CopiedCheckout):
    def test_a_refused_checkout_runs_no_step_module_code_and_writes_no_bytecode(self) -> None:
        self.plant("scripts/aegis_desk/levels.py")  # imported only through the trigger_levels step
        code, out = self.routine()
        self.assertEqual(20, code, out)
        self.assertIn(REFUSED, out)
        self.assertFalse(os.path.exists(self.marker), "a step module's code ran before the pin check refused")
        self.assertEqual([], self.bytecode())
        code, out = self.routine("--allow-dirty")
        self.assertEqual(0, code, out)
        self.assertTrue(os.path.exists(self.marker), "the control: with --allow-dirty the steps run")
        self.assertEqual([], self.bytecode(), "a routine run writes no bytecode into the checkout")


class PlantedBytecodeTest(CopiedCheckout):
    def test_a_planted_unchecked_pyc_is_never_loaded_by_the_routine(self) -> None:
        source = os.path.join(self.checkout, "scripts", "aegis_desk", "version.py")
        modified = os.path.join(os.path.dirname(self.checkout), "version_modified.py")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        with open(modified, "w", encoding="utf-8") as handle:
            handle.write(text + "\nopen(%r, 'w', encoding='utf-8').close()\n" % self.marker)
        cache = os.path.join(os.path.dirname(source), "__pycache__", "version.%s.pyc" % sys.implementation.cache_tag)
        py_compile.compile(modified, cfile=cache, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        # the control: another tool, which keeps Python's default bytecode cache, loads the planted file
        proc = subprocess.run([sys.executable, os.path.join(self.checkout, "scripts", "market_clock.py"), "--schema"],
                              cwd=self.desk, env=environment(), capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(self.marker), "the planted bytecode is live for an ordinary interpreter")
        os.remove(self.marker)
        code, out = self.routine()
        self.assertEqual(20, code, out)
        self.assertIn(REFUSED, out)
        self.assertFalse(os.path.exists(self.marker), "routine.py loaded a .pyc instead of the checkout's source")


if __name__ == "__main__":
    unittest.main()
