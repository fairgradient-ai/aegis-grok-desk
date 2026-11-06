"""``scripts/gen_role_skills.py``: one generated role skill per agent, and the two index blocks.

For every ``agents/<seat>.md`` (sorted) the generator renders ``skills/aegis-role-<seat>/`` with ``SKILL.md`` (the
D2-17 frontmatter, the title line, the generated-from line and the agent's ``## System prompt`` section verbatim),
``LICENSE`` (the root LICENSE bytes) and ``ATTRIBUTION.md`` (``origin: new``), plus the index blocks between
``<!-- role-skills:start -->`` and ``<!-- role-skills:end -->`` in ``skills/README.md`` (a table) and ``SETUP.md``
(one line). ``--check`` renders in memory and fails on a differing or missing file, a missing marker pair, a role
skill directory without its agent and a stray file in one; write mode refuses the same structural problems and
writes nothing then. Every write test runs on a temporary copy of the tree's agents, skills, SETUP.md, LICENSE and
plugin.json; nothing in the repository is written."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from typing import List, Sequence, Tuple

import gen_role_skills as gen

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEATS = ("desk-lead", "exit-sentinel", "position-control", "structuring-desk")
BOUNDARY = ("This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules "
            "fired and why; you act in your brokerage account.")
TREE_PARTS = ("agents", "skills", "SETUP.md", "LICENSE", "plugin.json")
# Content-independent anchors (PR 3 decision R-16): the tests insert these lines themselves instead of matching the
# agents' own wording, so a rewrite of a profile regenerates its role skill without breaking a test.
SENTINEL = "Sentinel line the test inserts as the first line of the System prompt section."
HAND_EDIT = "A hand edit the generator did not write."


def read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def read_bytes(root: str, rel: str) -> bytes:
    with open(os.path.join(root, rel), "rb") as handle:
        return handle.read()


def write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def with_sentinel(agent_text: str) -> str:
    """``agent_text`` with :data:`SENTINEL` as the first line of its ``## System prompt`` section (the heading line
    is found by the generator's own constant; the gate requires it in every agent)."""
    heading = gen.SYSTEM_PROMPT_HEADING + "\n"
    if agent_text.count(heading) != 1:
        raise AssertionError("expected exactly one %r line" % heading)
    return agent_text.replace(heading, heading + "\n" + SENTINEL + "\n", 1)


def run_main(argv: Sequence[str]) -> Tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gen.main(list(argv))
    return code, out.getvalue()


def release() -> str:
    with open(os.path.join(ROOT, "plugin.json"), encoding="utf-8") as handle:
        return json.load(handle)["version"]


class RealTreeTest(unittest.TestCase):
    def test_each_committed_skill_is_its_agent_s_rendering(self) -> None:
        # The exact D2-17 golden runs on a synthetic agent (CopyTest), so a content change to a real agent in a later
        # pull request regenerates the skill instead of breaking this test.
        for seat in SEATS:
            with self.subTest(seat=seat):
                agent = gen.read_agent(ROOT, "agents/%s.md" % seat)
                self.assertEqual(gen.render_skill(agent, release()), read(ROOT, "skills/aegis-role-%s/SKILL.md" % seat))
                self.assertEqual(hashlib.sha256(read_bytes(ROOT, "agents/%s.md" % seat)).hexdigest(), agent.sha256)

    def test_every_agent_has_its_three_files_and_nothing_else(self) -> None:
        roles = sorted(name for name in os.listdir(os.path.join(ROOT, "skills")) if name.startswith("aegis-role-"))
        self.assertEqual(["aegis-role-%s" % seat for seat in SEATS], roles)
        self.assertEqual(sorted(SEATS), sorted(name[:-3] for name in os.listdir(os.path.join(ROOT, "agents"))))
        for seat in SEATS:
            directory = os.path.join(ROOT, "skills", "aegis-role-" + seat)
            with self.subTest(seat=seat):
                self.assertEqual(["ATTRIBUTION.md", "LICENSE", "SKILL.md"], sorted(os.listdir(directory)))
                self.assertEqual(read_bytes(ROOT, "LICENSE"), read_bytes(directory, "LICENSE"))
                self.assertEqual(read_bytes(ROOT, "skills/aegis-desk-charter/LICENSE"), read_bytes(directory, "LICENSE"))
                self.assertEqual(
                    "# Attribution\n\norigin: new\n\nGenerated from agents/%s.md by scripts/gen_role_skills.py "
                    "(FairGradient, MIT); no text was copied from elsewhere.\n" % seat,
                    read(directory, "ATTRIBUTION.md"))
                skill = read(directory, "SKILL.md")
                self.assertIn(BOUNDARY, skill)
                self.assertIn("  source_sha256: %s\n" % hashlib.sha256(read_bytes(ROOT, "agents/%s.md" % seat)).hexdigest(),
                              skill)

    def test_the_skills_meet_the_structure_gate(self) -> None:
        for seat in SEATS:
            name = "aegis-role-" + seat
            text = read(ROOT, "skills/%s/SKILL.md" % name)
            with self.subTest(skill=name):
                self.assertTrue(text.startswith("---\n"))
                end = text.index("\n---\n", 4)
                block, body = text[4:end], text[end + 5:]
                fields = dict(re.match(r"^([a-z_]+):(?: (.*))?$", line).groups() for line in block.split("\n")
                              if not line.startswith("  "))
                self.assertEqual(name, fields["name"])
                self.assertEqual("MIT", fields["license"])
                self.assertLessEqual(len(fields["description"]), 1024)
                self.assertIsNone(fields["metadata"])
                nested = dict(line.strip().split(": ", 1) for line in block.split("\n") if line.startswith("  "))
                self.assertEqual({"version": '"%s"' % release(), "author": "FairGradient", "category": "role",
                                  "generated_from": "agents/%s.md" % seat},
                                 {key: nested[key] for key in ("version", "author", "category", "generated_from")})
                self.assertRegex(nested["source_sha256"], r"^[0-9a-f]{64}$")
                self.assertLessEqual(body.count("\n"), 320)
                self.assertNotRegex(text, r"\bv\d+\.\d+\.\d+\b")

    def test_the_index_blocks_are_the_d2_17_lines(self) -> None:
        readme = read(ROOT, "skills/README.md")
        self.assertIn(
            "<!-- role-skills:start -->\n"
            "| Role skill | Generated from | Seat |\n"
            "| --- | --- | --- |\n"
            "| [aegis-role-desk-lead](aegis-role-desk-lead/SKILL.md) | `agents/desk-lead.md` | floor |\n"
            "| [aegis-role-exit-sentinel](aegis-role-exit-sentinel/SKILL.md) | `agents/exit-sentinel.md` | floor |\n"
            "| [aegis-role-position-control](aegis-role-position-control/SKILL.md) | `agents/position-control.md` | floor |\n"
            "| [aegis-role-structuring-desk](aegis-role-structuring-desk/SKILL.md) | `agents/structuring-desk.md` | floor |\n"
            "<!-- role-skills:end -->\n", readme)
        self.assertLess(readme.index("[aegis-desk-charter]"), readme.index("<!-- role-skills:start -->"),
                        "after the main table")
        setup = read(ROOT, "SETUP.md")
        self.assertIn(
            "<!-- role-skills:start -->\n"
            "Role skills (generated from the agents' System prompts by `scripts/gen_role_skills.py`): "
            "`aegis-role-desk-lead`, `aegis-role-exit-sentinel`, `aegis-role-position-control`, `aegis-role-structuring-desk`\n"
            "<!-- role-skills:end -->\n", setup)
        section_5 = setup.split("## 5. Install the skills", 1)[1].split("\n## ", 1)[0]
        self.assertIn("<!-- role-skills:start -->", section_5)
        for text in (readme, setup):
            self.assertEqual((1, 1), (text.count(gen.START_MARKER), text.count(gen.END_MARKER)))

    def test_check_passes_on_the_committed_tree(self) -> None:
        self.assertEqual((0, ["ok: 4 role skill(s) match agents/ (aegis-role-desk-lead, aegis-role-exit-sentinel, "
                              "aegis-role-position-control, aegis-role-structuring-desk)"]), gen.run(ROOT, check=True))
        code, output = run_main(["--check"])
        self.assertEqual(0, code, output)

    def test_usage_errors_exit_2(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            run_main(["--nope"])
        self.assertEqual(2, caught.exception.code)


class CopyTest(unittest.TestCase):
    """A temporary copy of the parts of the tree the generator reads and writes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-gen-role-skills-")
        self.root = os.path.join(self._tmp.name, "tree")
        os.makedirs(self.root)
        for part in TREE_PARTS:
            source = os.path.join(ROOT, part)
            if os.path.isdir(source):
                shutil.copytree(source, os.path.join(self.root, part))
            else:
                shutil.copyfile(source, os.path.join(self.root, part))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def generated(self, root: str) -> List[Tuple[str, bytes]]:
        found = []
        for seat in SEATS:
            for name in gen.GENERATED_FILES:
                rel = "skills/aegis-role-%s/%s" % (seat, name)
                found.append((rel, read_bytes(root, rel)))
        return found

    def test_the_rendering_is_exactly_d2_17(self) -> None:
        agent_text = ("---\nname: golden-seat\ntitle: Aegis Golden Seat\ndescription: Keeps the golden file: one line, "
                      "verbatim, with a colon: like this. Placeholder.\nmodel: inherit\nseat: off-floor\nskills:\n"
                      "  - aegis-desk-charter\nwrites_to_broker: false\n---\n\n# Aegis Golden Seat\n\n## Bot profile\n\n"
                      "- **Name:** Aegis Golden Seat\n\n## System prompt\n\nFirst paragraph. %s\n\nSecond paragraph.\n\n"
                      "## After\n\nnot in the skill\n" % BOUNDARY)
        write(self.root, "agents/golden-seat.md", agent_text)
        digest = hashlib.sha256(agent_text.encode("utf-8")).hexdigest()
        expected = (
            "---\n"
            "name: aegis-role-golden-seat\n"
            "description: Keeps the golden file: one line, verbatim, with a colon: like this. Placeholder.\n"
            "license: MIT\n"
            "metadata:\n"
            "  version: \"%s\"\n"
            "  author: FairGradient\n"
            "  category: role\n"
            "  generated_from: agents/golden-seat.md\n"
            "  source_sha256: %s\n"
            "---\n"
            "\n"
            "# Aegis Golden Seat: role skill\n"
            "\n"
            "Generated from `agents/golden-seat.md` by `scripts/gen_role_skills.py`; edit the agent and run the generator.\n"
            "\n"
            "First paragraph. %s\n"
            "\n"
            "Second paragraph.\n" % (release(), digest, BOUNDARY))
        self.assertEqual(expected, gen.render_skill(gen.read_agent(self.root, "agents/golden-seat.md"), release()))
        self.assertEqual(0, gen.run(self.root, check=False)[0])
        self.assertEqual(expected, read(self.root, "skills/aegis-role-golden-seat/SKILL.md"))
        self.assertIn("| [aegis-role-golden-seat](aegis-role-golden-seat/SKILL.md) | `agents/golden-seat.md` | off-floor |\n",
                      read(self.root, "skills/README.md"))
        self.assertIn("`aegis-role-exit-sentinel`, `aegis-role-golden-seat`, `aegis-role-position-control`, "
                      "`aegis-role-structuring-desk`\n",
                      read(self.root, "SETUP.md"), "sorted by agent file name")

    def test_write_on_a_tree_without_role_skills_reproduces_the_committed_ones(self) -> None:
        for seat in SEATS:
            shutil.rmtree(os.path.join(self.root, "skills", "aegis-role-" + seat))
        for rel in ("skills/README.md", "SETUP.md"):
            text = read(self.root, rel)
            start, end = text.index(gen.START_MARKER), text.index(gen.END_MARKER)
            write(self.root, rel, text[:start + len(gen.START_MARKER)] + "\n" + text[end:])
        code, lines = gen.run(self.root, check=True)
        self.assertEqual(1, code)
        self.assertIn("error: skills/aegis-role-desk-lead/SKILL.md: missing (generated from agents/desk-lead.md)", lines)
        self.assertIn("error: skills/README.md: the role-skills index block differs from its rendering", lines)
        self.assertIn("error: SETUP.md: the role-skills index block differs from its rendering", lines)
        self.assertEqual("14 problem(s); run python3 scripts/gen_role_skills.py", lines[-1])
        code, lines = gen.run(self.root, check=False)
        self.assertEqual(0, code, lines)
        self.assertIn("wrote skills/aegis-role-exit-sentinel/LICENSE", lines)
        self.assertEqual("ok: 4 role skill(s) generated from agents/ (14 file(s) written)", lines[-1])
        self.assertEqual(self.generated(ROOT), self.generated(self.root))
        for rel in ("skills/README.md", "SETUP.md"):
            self.assertEqual(read(ROOT, rel), read(self.root, rel))
        self.assertEqual(0, gen.run(self.root, check=True)[0])
        code, lines = gen.run(self.root, check=False)
        self.assertEqual((0, ["ok: 4 role skill(s) generated from agents/ (0 file(s) written)"]), (code, lines))

    def test_a_hand_edit_of_a_generated_file_is_drift(self) -> None:
        rel = "skills/aegis-role-exit-sentinel/SKILL.md"
        # The anchor is a line the test itself adds, so the agents' wording can change freely (R-16).
        write(self.root, rel, read(self.root, rel) + HAND_EDIT + "\n")
        code, lines = gen.run(self.root, check=True)
        self.assertEqual((1, ["error: %s: differs from its rendering from agents/exit-sentinel.md" % rel,
                              "1 problem(s); run python3 scripts/gen_role_skills.py"]), (code, lines))

    def test_an_agent_edit_changes_the_skill_and_its_source_hash(self) -> None:
        rel = "agents/position-control.md"
        write(self.root, rel, with_sentinel(read(self.root, rel)))
        code, lines = gen.run(self.root, check=True)
        self.assertEqual(1, code)
        self.assertIn("error: skills/aegis-role-position-control/SKILL.md: differs from its rendering from "
                      "agents/position-control.md", lines)
        self.assertEqual(0, gen.run(self.root, check=False)[0])
        skill = read(self.root, "skills/aegis-role-position-control/SKILL.md")
        self.assertIn(SENTINEL, skill)
        self.assertIn("source_sha256: %s" % hashlib.sha256(read_bytes(self.root, rel)).hexdigest(), skill)

    def test_a_missing_marker_pair_fails_both_modes_and_nothing_is_written(self) -> None:
        text = read(self.root, "skills/README.md")
        write(self.root, "skills/README.md", text.replace(gen.END_MARKER + "\n", "", 1))
        rel = "skills/aegis-role-desk-lead/SKILL.md"
        write(self.root, rel, "stale\n")
        message = ("error: skills/README.md: needs exactly one <!-- role-skills:start --> line followed by one "
                   "<!-- role-skills:end --> line")
        for check in (True, False):
            code, lines = gen.run(self.root, check=check)
            self.assertEqual(1, code)
            self.assertIn(message, lines)
        self.assertEqual("stale\n", read(self.root, rel), "write mode refused: nothing was written")
        write(self.root, "SETUP.md", read(self.root, "SETUP.md") + gen.START_MARKER + "\n")
        self.assertIn("error: SETUP.md: needs exactly one <!-- role-skills:start --> line followed by one "
                      "<!-- role-skills:end --> line", gen.run(self.root, check=True)[1])

    def test_a_role_skill_without_its_agent_and_a_stray_file_fail(self) -> None:
        write(self.root, "skills/aegis-role-ghost/SKILL.md", "---\nname: aegis-role-ghost\n---\n")
        write(self.root, "skills/aegis-role-desk-lead/notes.md", "hand notes\n")
        for check in (True, False):
            code, lines = gen.run(self.root, check=check)
            self.assertEqual(1, code)
            self.assertIn("error: skills/aegis-role-ghost: no agents/ghost.md generates it; remove the directory", lines)
            self.assertIn("error: skills/aegis-role-desk-lead/notes.md: not a generated file; remove it", lines)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "skills/aegis-role-ghost/SKILL.md")), "nothing removed")

    def test_an_agent_without_a_system_prompt_section_is_an_error(self) -> None:
        rel = "agents/exit-sentinel.md"
        write(self.root, rel, read(self.root, rel).replace("## System prompt", "## Prompt", 1))
        code, lines = gen.run(self.root, check=True)
        self.assertEqual((1, ["error: agents/exit-sentinel.md: no '## System prompt' section"]), (code, lines))

    def test_the_system_prompt_ends_at_the_next_section_but_not_inside_a_code_fence(self) -> None:
        rel = "agents/desk-lead.md"
        text = with_sentinel(read(self.root, rel)).rstrip("\n")
        write(self.root, rel, text + "\n\n```text\n## not a heading\n```\n\n### kept\n\n## Notes\n\nnot in the skill\n")
        prompt = gen.read_agent(self.root, rel).system_prompt
        self.assertTrue(prompt.endswith("```text\n## not a heading\n```\n\n### kept"), prompt)
        self.assertNotIn("not in the skill", prompt)
        self.assertTrue(prompt.startswith(SENTINEL + "\n"), prompt)


if __name__ == "__main__":
    unittest.main()
