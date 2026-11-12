"""Pin the safety properties of .github/workflows/release.yml with standard-library text checks.

The release workflow is the one place in this repository that holds write access: it creates the
release tag on `main` and publishes the GitHub Release, after the same gate as docs/RELEASE.md step 5.
The standard library has no YAML parser, so these tests read the file as text and pin what matters:
it runs only when someone with write access dispatches it by hand, only on `main`, with write access
scoped to its one job; the version input reaches the shell only through `env:` (never interpolated
into a script, which would let the input inject shell code); and the gate runs before the tag.
"""
from __future__ import annotations

import os
import re
import unittest
from typing import List, Tuple

import tests._path  # noqa: F401
from tests.gates._trees import ROOT

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "release.yml")


def _text() -> str:
    with open(WORKFLOW, encoding="utf-8") as handle:
        return handle.read()


def _lines() -> List[str]:
    return [line for line in _text().splitlines() if not line.lstrip().startswith("#")]


def _top_level_block(key: str) -> List[str]:
    """The lines of a top-level mapping key, up to the next top-level key."""
    lines = _lines()
    start = lines.index(key + ":")
    block = []
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        block.append(line)
    return block


def _run_blocks() -> List[Tuple[int, str]]:
    """Every `run:` script, inline or block form, with its line number."""
    lines = _text().splitlines()
    blocks: List[Tuple[int, str]] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[index])
        if not match:
            index += 1
            continue
        indent = len(match.group(1))
        rest = match.group(2)
        if rest not in ("|", ">", "|-", ">-"):
            blocks.append((index + 1, rest))
            index += 1
            continue
        body = []
        index += 1
        while index < len(lines) and (not lines[index].strip() or len(lines[index]) - len(lines[index].lstrip()) > indent):
            body.append(lines[index])
            index += 1
        blocks.append((index, "\n".join(body)))
    return blocks


class ReleaseWorkflowTest(unittest.TestCase):
    def test_the_only_trigger_is_a_manual_dispatch(self) -> None:
        triggers = [line.strip() for line in _top_level_block("on") if re.match(r"^  [a-z_]+:", line)]
        self.assertEqual(triggers, ["workflow_dispatch:"])

    def test_the_inputs_are_the_version_and_the_prerelease_switch(self) -> None:
        text = _text()
        self.assertRegex(text, r"\n      version:\n(?:        .*\n)*?        required: true\n")
        self.assertRegex(text, r"\n      prerelease:\n(?:        .*\n)*?        type: boolean\n")
        self.assertRegex(text, r"\n      prerelease:\n(?:        .*\n)*?        default: true\n")

    def test_the_workflow_default_is_read_only(self) -> None:
        block = [line.strip() for line in _top_level_block("permissions") if line.strip()]
        self.assertEqual(block, ["contents: read"])

    def test_write_access_is_scoped_to_the_release_job_on_main(self) -> None:
        text = _text()
        self.assertEqual(text.count("contents: write"), 1)
        job = text[text.index("\njobs:\n"):]
        self.assertIn("    if: github.ref == 'refs/heads/main'\n", job)
        self.assertIn("    permissions:\n      contents: write\n", job)

    def test_runs_never_overlap_and_are_never_cancelled(self) -> None:
        block = [line.strip() for line in _top_level_block("concurrency") if line.strip()]
        self.assertIn("cancel-in-progress: false", block)

    def test_no_expression_is_interpolated_into_a_script(self) -> None:
        blocks = _run_blocks()
        self.assertGreater(len(blocks), 5)
        for line, body in blocks:
            with self.subTest(line=line):
                self.assertNotIn("${{", body, "pass values to a run script through env:, never ${{ }}")

    def test_the_version_input_reaches_the_shell_only_through_env(self) -> None:
        text = _text()
        self.assertIn("VERSION: ${{ inputs.version }}", text)
        self.assertNotIn("github.event.inputs", text)

    def test_no_release_tag_is_written_literally(self) -> None:
        self.assertIsNone(re.search(r"\bv\d+\.\d+\.\d+\b", _text()))

    def test_the_gate_runs_before_the_tag_and_the_release_after_it(self) -> None:
        text = _text()
        order = [
            "plugin.json",
            "git ls-remote",
            "bash scripts/check.sh",
            "scripts/release_bump.py \"$VERSION\" --check",
            "git tag -a \"v$VERSION\" \"$GITHUB_SHA\"",
            "git push origin \"refs/tags/v$VERSION\"",
            "python3 scripts/check_grok_template.py",
            "gh release create \"v$VERSION\" --verify-tag",
        ]
        positions = []
        for needle in order:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
            positions.append(text.find(needle))
        self.assertEqual(positions, sorted(positions))

    def test_the_checkout_has_full_history_for_the_template_check(self) -> None:
        self.assertIn("fetch-depth: 0", _text())


if __name__ == "__main__":
    unittest.main()
