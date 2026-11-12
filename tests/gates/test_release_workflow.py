"""Pin the safety properties of .github/workflows/release.yml with standard-library text checks.

The release workflow is the one place in this repository that holds write access: it creates the
release tag on `main` and publishes the GitHub Release, after the same gate as docs/RELEASE.md step 5.
The standard library has no YAML parser, so these tests read the file as text and pin what matters:

- it runs only when someone with write access dispatches it by hand, and only on `main`;
- a read-only `gate` job, with no credential kept in its checkout, runs the checks; the `release`
  job runs only after it, and is the one place with write access, limited to `contents: write`;
- the only `${{ }}` expressions are three exact `env:` lines, so no input is ever interpolated into
  a script (which would let the input inject shell code);
- the gate steps come before the tag, the template check and the Release after it;
- a re-run reuses a tag already on the gated commit and refuses one on any other commit.
"""
from __future__ import annotations

import os
import re
import unittest
from typing import Dict, List

import tests._path  # noqa: F401
from tests.gates._trees import ROOT

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "release.yml")
ALLOWED_EXPRESSIONS = (
    "VERSION: ${{ inputs.version }}",
    "PRERELEASE: ${{ inputs.prerelease }}",
    "GH_TOKEN: ${{ github.token }}",
)


def _lines() -> List[str]:
    """The workflow's lines with comment-only lines removed."""
    with open(WORKFLOW, encoding="utf-8") as handle:
        return [line for line in handle.read().splitlines() if not line.lstrip().startswith("#")]


def _block(lines: List[str], start: int) -> List[str]:
    """The lines indented deeper than lines[start], up to the next line at its indent or less."""
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return block


def _top(key: str) -> List[str]:
    lines = _lines()
    return _block(lines, lines.index(key + ":"))


def _jobs() -> Dict[str, List[str]]:
    lines = _lines()
    start = lines.index("jobs:")
    jobs: Dict[str, List[str]] = {}
    for offset, line in enumerate(_block(lines, start)):
        match = re.match(r"^  ([a-z_-]+):$", line)
        if match:
            jobs[match.group(1)] = _block(lines, start + 1 + offset)
    return jobs


def _job_key(job: List[str], key: str) -> List[str]:
    """The stripped, non-blank lines under a job's top-level key (an empty list if absent)."""
    for index, line in enumerate(job):
        if line == "    " + key + ":":
            return [entry.strip() for entry in _block(job, index) if entry.strip()]
    return []


def _text() -> str:
    return "\n".join(_lines()) + "\n"


class ReleaseWorkflowTest(unittest.TestCase):
    def test_the_only_trigger_is_a_manual_dispatch(self) -> None:
        triggers = [line.strip() for line in _top("on") if re.match(r"^  [a-z_]+:", line)]
        self.assertEqual(triggers, ["workflow_dispatch:"])

    def test_the_inputs_are_the_version_and_the_prerelease_switch(self) -> None:
        text = _text()
        self.assertRegex(text, r"\n      version:\n(?:        .*\n)*?        required: true\n")
        self.assertRegex(text, r"\n      prerelease:\n(?:        .*\n)*?        type: boolean\n")
        self.assertRegex(text, r"\n      prerelease:\n(?:        .*\n)*?        default: true\n")

    def test_the_workflow_default_is_read_only(self) -> None:
        self.assertEqual([line.strip() for line in _top("permissions") if line.strip()], ["contents: read"])

    def test_runs_never_overlap_and_are_never_cancelled(self) -> None:
        self.assertIn("cancel-in-progress: false", [line.strip() for line in _top("concurrency")])

    def test_there_are_exactly_the_gate_and_release_jobs_both_on_main(self) -> None:
        jobs = _jobs()
        self.assertEqual(sorted(jobs), ["gate", "release"])
        for name, job in jobs.items():
            with self.subTest(job=name):
                self.assertIn("    if: github.ref == 'refs/heads/main'", job)
        self.assertIn("    needs: gate", jobs["release"])

    def test_only_the_release_job_can_write_and_only_contents(self) -> None:
        jobs = _jobs()
        self.assertEqual(_job_key(jobs["gate"], "permissions"), [])
        self.assertEqual(_job_key(jobs["release"], "permissions"), ["contents: write"])
        writes = [line.strip() for line in _lines() if re.search(r":\s*write\b|write-all", line)]
        self.assertEqual(writes, ["contents: write"])

    def test_the_gate_keeps_no_credential(self) -> None:
        gate = "\n".join(_jobs()["gate"])
        self.assertIn("persist-credentials: false", gate)
        self.assertNotIn("git push", gate)
        self.assertNotIn("ls-remote", gate)

    def test_the_only_expressions_are_three_exact_env_lines(self) -> None:
        found = [line.strip() for line in _lines() if "${{" in line]
        self.assertEqual(sorted(found), sorted(ALLOWED_EXPRESSIONS))
        self.assertNotIn("github.event.inputs", _text())

    def test_no_release_tag_is_written_literally(self) -> None:
        self.assertIsNone(re.search(r"\bv\d+\.\d+\.\d+\b", _text()))

    def test_the_gate_runs_before_the_tag_and_the_release_after_it(self) -> None:
        jobs = _jobs()
        gate = "\n".join(jobs["gate"])
        release = "\n".join(jobs["release"])
        gate_order = [
            "- name: Check the version against plugin.json and CHANGELOG.md",
            're.fullmatch(r"\\d+\\.\\d+\\.\\d+", version)',
            'open("plugin.json"',
            'open("CHANGELOG.md"',
            "run: bash scripts/check.sh",
            'run: python3 scripts/release_bump.py "$VERSION" --check',
        ]
        release_order = [
            "git ls-remote --tags origin",
            'git tag -a "v$VERSION" "$GITHUB_SHA"',
            'git push origin "refs/tags/v$VERSION"',
            "python3 scripts/check_grok_template.py",
            'gh release create "v$VERSION" --verify-tag',
        ]
        for body, order in ((gate, gate_order), (release, release_order)):
            positions = []
            for needle in order:
                with self.subTest(needle=needle):
                    self.assertIn(needle, body)
                positions.append(body.find(needle))
            self.assertEqual(positions, sorted(positions))

    def test_a_rerun_reuses_a_tag_on_this_commit_and_refuses_any_other(self) -> None:
        release = "\n".join(_jobs()["release"])
        self.assertIn('peeled=$(printf \'%s\\n\' "$lines" | awk -v ref="refs/tags/v$VERSION^{}"', release)
        self.assertIn("remote=${peeled:-$plain}", release)
        self.assertIn('if [ "$remote" != "$GITHUB_SHA" ]; then', release)
        self.assertLess(release.find('if [ "$remote" != "$GITHUB_SHA" ]'), release.find("git tag -a"))
        self.assertIn('if gh release view "v$VERSION"', release)
        self.assertNotIn("--force", release.replace('git fetch --force origin "refs/tags/v$VERSION:', ""))

    def test_both_checkouts_have_full_history(self) -> None:
        for name, job in _jobs().items():
            with self.subTest(job=name):
                self.assertIn("fetch-depth: 0", "\n".join(job))


if __name__ == "__main__":
    unittest.main()
