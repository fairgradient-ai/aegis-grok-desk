#!/usr/bin/env python3
"""Generate one role skill per agent, and the role-skill index blocks. Stdlib only; no network.

For every ``agents/<seat>.md``, sorted by file name, the generator renders ``skills/aegis-role-<seat>/`` with three
files:

* ``SKILL.md``: frontmatter ``name: aegis-role-<seat>``, the agent's ``description`` verbatim, ``license: MIT`` and a
  ``metadata`` block (``version`` from ``plugin.json``, ``author: FairGradient``, ``category: role``,
  ``generated_from: agents/<seat>.md`` and ``source_sha256``, the SHA-256 of the agent file's bytes); then the title
  line ``# <agent title>: role skill``, the generated-from line and the agent's ``## System prompt`` section verbatim
  (up to the next ``## `` heading outside a code fence, or the end of the file), with one final newline;
* ``LICENSE``: the bytes of the root ``LICENSE``;
* ``ATTRIBUTION.md``: ``origin: new`` and the generated-from sentence.

It also renders the index blocks between the ``<!-- role-skills:start -->`` and ``<!-- role-skills:end -->`` lines
of ``skills/README.md`` (a table of role skills, the agent each comes from and its seat) and ``SETUP.md`` (one line
naming every role skill). The marker lines are written by hand once; the generator only replaces what lies between
them.

``--check`` renders in memory and fails (exit 1, one ``error:`` line per problem) on a generated file that is missing
or differs, an index block that differs, a file without exactly one marker pair, a ``skills/aegis-role-*``
directory whose agent is gone, and a file in a role-skill directory that the generator does not write. The default
mode writes the files that differ; it refuses (exit 1, nothing written) the structural problems: a marker pair, an
orphaned directory or a stray file is the owner's call, never deleted here. Paths come from this script's own
location, so ``check.sh`` step 10 checks the tree it runs in; the tests call :func:`run` with another root.

Usage: ``python3 scripts/gen_role_skills.py [--check]``. Exit 0 written or current, 1 drift or error, 2 usage.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = "agents"
SKILLS_DIR = "skills"
README_REL = "skills/README.md"
SETUP_REL = "SETUP.md"
LICENSE_REL = "LICENSE"
PLUGIN_REL = "plugin.json"
ROLE_PREFIX = "aegis-role-"
GENERATED_FILES = ("ATTRIBUTION.md", "LICENSE", "SKILL.md")
AUTHOR = "FairGradient"
CATEGORY = "role"
START_MARKER = "<!-- role-skills:start -->"
END_MARKER = "<!-- role-skills:end -->"
SYSTEM_PROMPT_HEADING = "## System prompt"
REGENERATE = "run python3 scripts/gen_role_skills.py"
FRONTMATTER_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s+(.*?))?\s*$")
FENCE_RE = re.compile(r"^(```|~~~)")


class GenError(Exception):
    """An agent file the generator cannot read (frontmatter, a field, the System prompt section)."""


@dataclasses.dataclass(frozen=True)
class Agent:
    """What a role skill is rendered from: ``agents/<stem>.md``."""

    stem: str
    rel: str
    title: str
    description: str
    seat: str
    system_prompt: str
    sha256: str

    @property
    def skill(self) -> str:
        return ROLE_PREFIX + self.stem


def frontmatter(text: str, rel: str) -> Tuple[Dict[str, str], str]:
    """``(top-level scalar fields, body)``; list items and nested maps are skipped (the generator needs scalars)."""
    if not text.startswith("---\n"):
        raise GenError("%s: missing frontmatter" % rel)
    end = text.find("\n---\n", 4)
    if end < 0:
        raise GenError("%s: unterminated frontmatter" % rel)
    fields: Dict[str, str] = {}
    for line in text[4:end].split("\n"):
        match = FRONTMATTER_LINE_RE.match(line)
        if match and match.group(2):
            fields[match.group(1)] = match.group(2)
    return fields, text[end + 5:]


def system_prompt(body: str, rel: str) -> str:
    """The ``## System prompt`` section's text: after its heading line, up to the next ``## `` heading outside a code
    fence, without leading or trailing blank lines."""
    lines = body.split("\n")
    try:
        start = lines.index(SYSTEM_PROMPT_HEADING)
    except ValueError:
        raise GenError("%s: no '%s' section" % (rel, SYSTEM_PROMPT_HEADING)) from None
    section: List[str] = []
    fence: Optional[str] = None
    for line in lines[start + 1:]:
        opening = FENCE_RE.match(line)
        if fence is None and line.startswith("## "):
            break
        if opening and (fence is None or opening.group(1) == fence):
            fence = opening.group(1) if fence is None else None
        section.append(line)
    text = "\n".join(section).strip("\n")
    if not text.strip():
        raise GenError("%s: the '%s' section is empty" % (rel, SYSTEM_PROMPT_HEADING))
    return text


def read_agent(root: str, rel: str) -> Agent:
    """The agent at ``root/rel`` (``agents/<stem>.md``); ``GenError`` when a field the skill needs is missing."""
    with open(os.path.join(root, rel), "rb") as handle:
        raw = handle.read()
    text = raw.decode("utf-8")
    fields, body = frontmatter(text, rel)
    for key in ("title", "description", "seat"):
        if not fields.get(key):
            raise GenError("%s: frontmatter has no '%s'" % (rel, key))
    stem = os.path.basename(rel)[:-3]
    return Agent(stem, rel, fields["title"], fields["description"], fields["seat"], system_prompt(body, rel),
                 hashlib.sha256(raw).hexdigest())


def agent_rels(root: str) -> List[str]:
    directory = os.path.join(root, AGENTS_DIR)
    if not os.path.isdir(directory):
        return []
    return ["%s/%s" % (AGENTS_DIR, name) for name in sorted(os.listdir(directory))
            if name.endswith(".md") and os.path.isfile(os.path.join(directory, name))]


def release_version(root: str) -> str:
    with open(os.path.join(root, PLUGIN_REL), encoding="utf-8") as handle:
        value = json.load(handle).get("version")
    if not isinstance(value, str) or not value:
        raise GenError("%s: no version" % PLUGIN_REL)
    return value


def render_skill(agent: Agent, release: str) -> str:
    """``skills/aegis-role-<seat>/SKILL.md``."""
    return "\n".join([
        "---",
        "name: %s" % agent.skill,
        "description: %s" % agent.description,
        "license: MIT",
        "metadata:",
        '  version: "%s"' % release,
        "  author: %s" % AUTHOR,
        "  category: %s" % CATEGORY,
        "  generated_from: %s" % agent.rel,
        "  source_sha256: %s" % agent.sha256,
        "---",
        "",
        "# %s: role skill" % agent.title,
        "",
        "Generated from `%s` by `scripts/gen_role_skills.py`; edit the agent and run the generator." % agent.rel,
        "",
        agent.system_prompt,
    ]) + "\n"


def render_attribution(agent: Agent) -> str:
    """``skills/aegis-role-<seat>/ATTRIBUTION.md``."""
    return ("# Attribution\n\norigin: new\n\nGenerated from %s by scripts/gen_role_skills.py (FairGradient, MIT); no "
            "text was copied from elsewhere.\n" % agent.rel)


def readme_block(agents: Sequence[Agent]) -> str:
    rows = ["| Role skill | Generated from | Seat |", "| --- | --- | --- |"]
    rows += ["| [%s](%s/SKILL.md) | `%s` | %s |" % (agent.skill, agent.skill, agent.rel, agent.seat) for agent in agents]
    return "\n".join(rows)


def setup_block(agents: Sequence[Agent]) -> str:
    return ("Role skills (generated from the agents' System prompts by `scripts/gen_role_skills.py`): %s"
            % ", ".join("`%s`" % agent.skill for agent in agents))


def splice(text: str, block: str) -> Optional[str]:
    """``text`` with the lines between its one marker pair replaced by ``block``; ``None`` without exactly one
    ``START_MARKER`` line followed by one ``END_MARKER`` line."""
    lines = text.split("\n")
    starts = [index for index, line in enumerate(lines) if line == START_MARKER]
    ends = [index for index, line in enumerate(lines) if line == END_MARKER]
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        return None
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        return None
    return "\n".join(lines[:starts[0] + 1] + block.split("\n") + lines[ends[0]:])


def plan(root: str) -> Tuple[Dict[str, Tuple[bytes, str]], List[str]]:
    """``(expected files, structural problems)``. Each expected file maps a root-relative path to its bytes and the
    agent it comes from (``""`` for an index file)."""
    release = release_version(root)
    agents = [read_agent(root, rel) for rel in agent_rels(root)]
    with open(os.path.join(root, LICENSE_REL), "rb") as handle:
        license_bytes = handle.read()
    expected: Dict[str, Tuple[bytes, str]] = {}
    for agent in agents:
        base = "%s/%s" % (SKILLS_DIR, agent.skill)
        expected[base + "/SKILL.md"] = (render_skill(agent, release).encode("utf-8"), agent.rel)
        expected[base + "/LICENSE"] = (license_bytes, agent.rel)
        expected[base + "/ATTRIBUTION.md"] = (render_attribution(agent).encode("utf-8"), agent.rel)
    problems: List[str] = []
    for rel, block in ((README_REL, readme_block(agents)), (SETUP_REL, setup_block(agents))):
        with open(os.path.join(root, rel), encoding="utf-8") as handle:
            spliced = splice(handle.read(), block)
        if spliced is None:
            problems.append("%s: needs exactly one %s line followed by one %s line" % (rel, START_MARKER, END_MARKER))
        else:
            expected[rel] = (spliced.encode("utf-8"), "")
    stems = {agent.stem for agent in agents}
    skills_dir = os.path.join(root, SKILLS_DIR)
    for name in sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else []:
        directory = os.path.join(skills_dir, name)
        if not name.startswith(ROLE_PREFIX) or not os.path.isdir(directory):
            continue
        stem = name[len(ROLE_PREFIX):]
        if stem not in stems:
            problems.append("%s/%s: no %s/%s.md generates it; remove the directory" % (SKILLS_DIR, name, AGENTS_DIR,
                                                                                        stem))
            continue
        for entry in sorted(os.listdir(directory)):
            if entry not in GENERATED_FILES:
                problems.append("%s/%s/%s: not a generated file; remove it" % (SKILLS_DIR, name, entry))
    return expected, problems


def current(root: str, rel: str) -> Optional[bytes]:
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()


def summary(expected: Dict[str, Tuple[bytes, str]]) -> Tuple[int, str]:
    skills = sorted({rel.split("/")[1] for rel, (_data, source) in expected.items() if source})
    return len(skills), ", ".join(skills)


def run(root: Optional[str] = None, check: bool = False) -> Tuple[int, List[str]]:
    """``(exit code, lines)``: ``--check`` compares, the default mode writes what differs."""
    root = ROOT if root is None else root
    try:
        expected, problems = plan(root)
    except (GenError, OSError, ValueError, UnicodeDecodeError) as exc:
        return 1, ["error: %s" % exc]
    if check:
        for rel, (data, source) in expected.items():
            found = current(root, rel)
            if not source:
                if found != data:
                    problems.append("%s: the role-skills index block differs from its rendering" % rel)
            elif found is None:
                problems.append("%s: missing (generated from %s)" % (rel, source))
            elif found != data:
                problems.append("%s: differs from its rendering from %s" % (rel, source))
        if problems:
            return 1, ["error: %s" % problem for problem in problems] + [
                "%d problem(s); %s" % (len(problems), REGENERATE)]
        count, names = summary(expected)
        return 0, ["ok: %d role skill(s) match agents/ (%s)" % (count, names)]
    if problems:
        return 1, ["error: %s" % problem for problem in problems] + [
            "%d problem(s); nothing written" % len(problems)]
    lines = []
    for rel, (data, _source) in expected.items():
        if current(root, rel) == data:
            continue
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        lines.append("wrote %s" % rel)
    count, _names = summary(expected)
    lines.append("ok: %d role skill(s) generated from agents/ (%d file(s) written)" % (count, len(lines)))
    return 0, lines


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gen_role_skills.py", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="render in memory and fail on drift instead of writing the role skills")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    status, lines = run(ROOT, check=args.check)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
