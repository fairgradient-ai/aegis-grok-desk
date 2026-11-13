"""Run scripts/check.sh against the real tree and against the gate fixture trees.

The structural lint and the Python floor live inside check.sh as stdlib heredocs, so they are
exercised the only way they can be: by running the script. The real tree must pass end to end;
each bad fixture must fail at the structural stage, naming the file and the defect.

check.sh exports AEGIS_CHECK_SH_NESTED=1 before it runs the unit test suite, and the real-tree
test skips itself under that marker; without the skip the suite would run check.sh, which runs
the suite, which runs check.sh. Fixture-tree runs carry no such recursion (a fixture's own
tests are one smoke test) and therefore always run, including inside check.sh.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import unittest
from typing import Tuple

import tests._path  # noqa: F401
from tests.gates._trees import CHECK_SH, ROOT, bad_cases, fixture_tree, read, write

NESTED = os.environ.get("AEGIS_CHECK_SH_NESTED") == "1"
EXPECTED_OK_LINES = (
    "ok: structure -",
    "ok: python floor -",
    "ok: 8 manifests agree on aegis-desk 0.1.0",
    "ok: AST gate over",
    "skip: not a git checkout; cannot compare template/grok-bot.json to tag v0.1.0",
    "ok: Grok Bot template is unpublished",
    "ok: unit tests -",
    "ok: 3 command journeys passed",
    "skip: scripts/gen_tools_doc.py not present",
    "skip: scripts/gen_role_skills.py not present",
    "ok: all repository gates passed",
)
ADAPTED_NOTICE = "Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), commit 75a4c79; see ATTRIBUTION.md."
# A generator stub for a fixture tree: check.sh steps 9 and 10 run the tree's own generator with --check.
GENERATOR_STUB = ('"""A stand-in generator for the gate test."""\nfrom __future__ import annotations\n\nimport sys\n\n'
                  'print(%r)\nsys.exit(%d)\n')


def write_charter(root: str, text: str) -> None:
    """Rewrite the fixture tree's one skill and re-pin its sha256 in template/grok-bot.json (the template check)."""
    write(root, "skills/aegis-desk-charter/SKILL.md", text)
    template = json.loads(read(root, "template/grok-bot.json"))
    template["skills"]["aegis-desk-charter"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    write(root, "template/grok-bot.json", json.dumps(template, indent=2) + "\n")


def run_check(root: str) -> Tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(["bash", CHECK_SH, root], capture_output=True, text=True, env=env,
                          timeout=900, check=False)
    return proc.returncode, proc.stdout + proc.stderr


class CheckShTests(unittest.TestCase):
    @unittest.skipIf(NESTED, "already running inside scripts/check.sh")
    def test_real_tree_passes(self) -> None:
        code, output = run_check(ROOT)
        self.assertEqual(0, code, output)
        for line in ("ok: structure -", "ok: python floor -", "ok: unit tests -",
                     "ok: docs/TOOLS.md matches the --schema of", "role skill(s) match agents/ (",
                     "ok: all repository gates passed"):
            self.assertIn(line, output)
        for line in ("skip: scripts/gen_tools_doc.py not present", "skip: scripts/gen_role_skills.py not present"):
            self.assertNotIn(line, output)

    def test_good_fixture_passes_every_step(self) -> None:
        with fixture_tree() as root:
            code, output = run_check(root)
        self.assertEqual(0, code, output)
        for line in EXPECTED_OK_LINES:
            self.assertIn(line, output)
        self.assertIn("1 allow-listed counter-example(s)", output)
        self.assertIn("ok: unit tests - 1 ran (floor 1)", output)

    def assert_fails(self, case: str, *needles: str) -> str:
        with fixture_tree(case) as root:
            code, output = run_check(root)
        self.assertEqual(1, code, f"{case}: expected exit 1, got {code}\n{output}")
        for needle in needles:
            self.assertIn(needle, output, f"{case}: expected {needle!r} in\n{output}")
        self.assertTrue("problem(s)" in output or "error:" in output, output)
        self.assertNotIn("ok: all repository gates passed", output)
        return output

    def test_writes_to_broker_true_fails(self) -> None:
        output = self.assert_fails("writes_true", "agents/desk-lead.md: writes_to_broker must be exactly the string 'false' (found 'true')")
        self.assertIn("zero agents may write to a broker; 1 declare otherwise", output)

    def test_writes_to_broker_quoted_false_fails(self) -> None:
        self.assert_fails("writes_quoted", "writes_to_broker must be exactly the string 'false' (found '\"false\"')")

    def test_missing_model_fails(self) -> None:
        self.assert_fails("missing_model", "agents/desk-lead.md: model must be 'inherit' (found None)")

    def test_boundary_sentence_missing_fails(self) -> None:
        self.assert_fails("boundary_missing", "agents/desk-lead.md: body must contain the boundary sentence verbatim")

    def test_unprefixed_skill_directory_fails(self) -> None:
        self.assert_fails("unprefixed_skill", "skills/desk-plain: skill directory must start with 'aegis-'")

    def test_skill_without_license_fails(self) -> None:
        self.assert_fails("skill_without_license", "skills/aegis-desk-charter: missing LICENSE")

    def test_emoji_fails(self) -> None:
        self.assert_fails("emoji", "docs/FAQ.md: contains emoji")

    def test_broken_link_fails(self) -> None:
        self.assert_fails("broken_link", "README.md: broken link -> docs/NOPE.md")

    def test_stale_version_pin_fails(self) -> None:
        self.assert_fails("stale_pin", "docs/FAQ.md: names release tag v9.9.9, but plugin.json declares v0.1.0")

    def test_missing_script_mention_fails(self) -> None:
        self.assert_fails("missing_script_mention", "SETUP.md: names scripts/missing_tool.py, which does not exist")

    def test_prerelease_version_fails_in_the_structural_stage(self) -> None:
        self.assert_fails("prerelease_version", "plugin.json: version '0.1.0-dev' is not a plain X.Y.Z release version")

    def test_python_310_syntax_fails_the_floor(self) -> None:
        # The 3.9-grammar parser reports the match statement at the block's last line; the
        # file-qualified prefix and the gate's own wording are the contract, not the line number
        # and not the parser's detail, which differs by interpreter: a 3.10+ interpreter parsing
        # with feature_version=(3, 9) says "Pattern matching is only supported in Python 3.10",
        # a real Python 3.9 (the floor interpreter) says "invalid syntax".
        output = self.assert_fails("py310_syntax", "scripts/aegis_desk/floor_violation.py:",
                                   "not valid Python 3.9 syntax")
        self.assertTrue("Pattern matching is only supported in Python 3.10" in output or "invalid syntax" in output,
                        output)

    def test_missing_future_import_fails_the_floor(self) -> None:
        self.assert_fails("missing_future_import",
                          "scripts/aegis_desk/floor_violation.py: must begin with 'from __future__ import annotations'")

    def test_runtime_union_fails_the_floor(self) -> None:
        self.assert_fails("runtime_union", "scripts/aegis_desk/floor_violation.py:6: runtime 'X | Y' union in isinstance()")

    def test_sub_gate_failures_stop_the_run(self) -> None:
        for case, needle in (("hooks_key", ".grok-plugin/plugin.json: must not declare 'hooks'"),
                             ("grok_skills_key", ".grok-plugin/plugin.json: must not declare 'skills'"),
                             ("paragraph_drift", "AGENTS.md: must repeat the desk-rule paragraph"),
                             ("request_post", "alpaca_client.py:8: [request-method]"),
                             ("request_data_attribute", "alpaca_client.py:9: [request-mutation]"),
                             ("request_subclass", "alpaca_client.py:7: [request-subclass]"),
                             ("xmlrpc_import", "quote_cache.py:4: [network-import] 'xmlrpc'"),
                             ("prose_curl", "docs/FAQ.md:3: [curl-write]"),
                             ("prose_agents_md", "AGENTS.md:5: [curl-write]")):
            self.assert_fails(case, needle)

    def test_duplicate_frontmatter_key_fails(self) -> None:
        # The hand parser would otherwise keep the last value, so `writes_to_broker: true` on
        # line 2 followed by `false` later would pass as a non-writer.
        self.assert_fails("duplicate_frontmatter_key",
                          "agents/desk-lead.md: frontmatter key 'writes_to_broker' is declared twice")

    def test_boundary_sentence_inside_an_html_comment_does_not_count(self) -> None:
        self.assert_fails("boundary_in_comment", "agents/desk-lead.md: body must contain the boundary sentence verbatim")

    def test_boundary_sentence_inside_a_code_fence_does_not_count(self) -> None:
        with fixture_tree() as root:
            agent = read(root, "agents/desk-lead.md")
            head, prompt = agent.split("## System prompt", 1)
            write(root, "agents/desk-lead.md", head + "## System prompt\n\nRoute, never compute.\n\n```text\n"
                  + prompt.strip() + "\n```\n")
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("agents/desk-lead.md: body must contain the boundary sentence verbatim", output)

    def test_adapted_skill_without_the_in_body_notice_fails(self) -> None:
        self.assert_fails("adapted_skill_without_notice",
                          "skills/aegis-desk-charter/SKILL.md: ATTRIBUTION.md says origin: adapted, so the body must "
                          "carry the notice line " + repr(ADAPTED_NOTICE))

    def test_adapted_skill_notice_must_be_visible(self) -> None:
        for wrap in ("<!-- %s -->", "```text\n%s\n```"):
            with self.subTest(wrap=wrap):
                with fixture_tree("adapted_skill_without_notice") as root:
                    skill = read(root, "skills/aegis-desk-charter/SKILL.md")
                    write_charter(root, skill + "\n" + wrap % ADAPTED_NOTICE + "\n")
                    code, output = run_check(root)
                self.assertEqual(1, code, output)
                self.assertIn("skills/aegis-desk-charter/SKILL.md: ATTRIBUTION.md says origin: adapted", output)

    def test_adapted_skill_with_the_notice_passes(self) -> None:
        with fixture_tree("adapted_skill_without_notice") as root:
            skill = read(root, "skills/aegis-desk-charter/SKILL.md")
            write_charter(root, skill + "\n" + ADAPTED_NOTICE + "\n")
            code, output = run_check(root)
        self.assertEqual(0, code, output)
        self.assertIn("ok: all repository gates passed", output)

    def test_agents_subdirectory_fails(self) -> None:
        self.assert_fails("agents_subdirectory", "agents/sub: agents/ may hold only agent .md files at its top level")

    def test_stray_file_under_agents_fails(self) -> None:
        with fixture_tree() as root:
            write(root, "agents/notes.txt", "writes_to_broker: true\n")
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("agents/notes.txt: agents/ may hold only agent .md files", output)

    def test_at_least_one_agent_and_one_skill_are_required(self) -> None:
        with fixture_tree() as root:
            os.remove(os.path.join(root, "agents", "desk-lead.md"))
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("agents/: at least one agent role is required", output)
        self.assertNotIn("0 agent(s), zero broker writers", output)
        with fixture_tree() as root:
            shutil.rmtree(os.path.join(root, "skills"))
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("skills/: at least one aegis- skill is required", output)

    def test_emoji_outside_the_original_blocks_fail(self) -> None:
        # U+2B50 white medium star, U+23F0 alarm clock, U+1F197 OK button, U+1F0CF playing
        # card and a regional-indicator flag, one file each so every code point is attributable.
        output = self.assert_fails("emoji_wide", "docs/EMOJI_2B50.md: contains emoji")
        for name in ("EMOJI_23F0", "EMOJI_1F197", "EMOJI_1F0CF", "EMOJI_FLAG"):
            self.assertIn(f"docs/{name}.md: contains emoji", output)

    def test_phantom_script_in_docs_fails_and_forward_references_pass(self) -> None:
        self.assert_fails("phantom_script_in_docs", "docs/TOOLS_NOTE.md: names scripts/does_not_exist.py, which does not exist")
        with fixture_tree() as root:
            write(root, "docs/TOOLS_NOTE.md", "# Tools note\n\nLater run `python3 scripts/release_bump.py 0.2.0` "
                  "(not yet in the tree; it lands with the release tooling).\n")
            write(root, "CLAUDE.md", "# Fixture\n\nRun `python3 scripts/phantom.py`.\n")
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("CLAUDE.md: names scripts/phantom.py, which does not exist", output)
        self.assertNotIn("release_bump.py", output)

    def test_empty_suite_fails_whatever_the_floor(self) -> None:
        # Before Python 3.12 `unittest discover` exits 0 on "Ran 0 tests", and a floor of 0
        # would accept it; the gate refuses zero discovered tests on every interpreter.
        self.assert_fails("empty_suite", "error: no tests were discovered under tests/")

    def test_flag_unions_pass_and_type_unions_fail_the_floor(self) -> None:
        # The good tree carries scripts/aegis_desk/flags.py (re/os/fcntl flag unions and a
        # flag-union default), which test_good_fixture_passes_every_step proves the floor accepts.
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "tests", "fixtures", "gates", "good",
                                                    "scripts", "aegis_desk", "flags.py")))
        with fixture_tree() as root:
            write(root, "scripts/aegis_desk/unions.py",
                  "from __future__ import annotations\n\nfrom decimal import Decimal\n\nNumber = int | float\n\n\n"
                  "def f(x: object, kind: object = Decimal | None) -> object:\n    return kind\n")
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("scripts/aegis_desk/unions.py:5: runtime 'X | Y' union in a module-level alias", output)
        self.assertIn("scripts/aegis_desk/unions.py:8: runtime 'X | Y' union in a default value", output)

    def test_test_count_floor_is_enforced(self) -> None:
        self.assert_fails("floor_too_high", "error: 1 test(s) ran but scripts/test_count_floor.txt requires at least 5")

    def test_floor_file_must_hold_an_integer(self) -> None:
        with fixture_tree() as root:
            write(root, "scripts/test_count_floor.txt", "many\n")
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("scripts/test_count_floor.txt must contain one non-negative integer", output)

    def test_failing_suite_fails_the_gate(self) -> None:
        with fixture_tree() as root:
            write(root, "tests/smoke/test_broken.py",
                  "from __future__ import annotations\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                  "    def test_fails(self) -> None:\n        self.fail('deliberate')\n")
            code, output = run_check(root)
        self.assertEqual(1, code, output)
        self.assertIn("error: the unit test suite failed", output)

    def test_steps_9_and_10_run_the_tree_s_own_generators_and_stop_the_gate_on_drift(self) -> None:
        for script, other in (("gen_tools_doc.py", "gen_role_skills.py"), ("gen_role_skills.py", "gen_tools_doc.py")):
            with self.subTest(generator=script):
                with fixture_tree() as root:
                    write(root, "scripts/" + script, GENERATOR_STUB % ("error: stub drift in " + script, 1))
                    code, output = run_check(root)
                self.assertEqual(1, code, output)
                self.assertIn("error: stub drift in " + script, output)
                self.assertNotIn("ok: all repository gates passed", output)
                if script == "gen_role_skills.py":
                    self.assertIn("skip: scripts/%s not present" % other, output, "step 9 ran first and skipped")

    def test_step_10_runs_the_role_skill_check_when_the_generator_exists(self) -> None:
        with fixture_tree() as root:
            write(root, "scripts/gen_role_skills.py", GENERATOR_STUB % ("ok: stub role skills checked", 0))
            code, output = run_check(root)
        self.assertEqual(0, code, output)
        self.assertIn("ok: stub role skills checked", output)
        self.assertNotIn("skip: scripts/gen_role_skills.py not present", output)
        self.assertIn("skip: scripts/gen_tools_doc.py not present", output)
        self.assertIn("ok: all repository gates passed", output)
        self.assertLess(output.index("skip: scripts/gen_tools_doc.py not present"),
                        output.index("ok: stub role skills checked"), "step 10 runs after step 9")

    def test_every_bad_fixture_is_exercised(self) -> None:
        with open(__file__, encoding="utf-8") as handle:
            source = handle.read()
        for sibling in ("test_check_manifests.py", "test_check_no_write_paths.py"):
            with open(os.path.join(os.path.dirname(__file__), sibling), encoding="utf-8") as handle:
                source += handle.read()
        for case in bad_cases():
            self.assertIn(f'"{case}"', source, f"bad fixture {case} is not used by any gate test")


if __name__ == "__main__":
    unittest.main()
