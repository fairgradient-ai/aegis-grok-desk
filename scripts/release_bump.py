#!/usr/bin/env python3
"""Move the release version everywhere it is written, in one pass, or nothing. Stdlib Python only; no network.

``python3 scripts/release_bump.py <next-version> [--check] [--root ROOT] [--json]`` is the first change after a
release is tagged (``CONTRIBUTING.md``, Releasing): it moves the version from the one in ``plugin.json`` to
``<next-version>`` everywhere the gates compare it, so the manifests, the pins and the template hashes move together
and the tagged release is never edited in place.

What moves
----------
* ``version`` in every manifest of ``check_manifests.MANIFESTS`` (each entry and nested ``plugins[]`` entry, and the
  Claude marketplace's ``metadata.version``), and every marketplace ``source.ref`` ``v<version>``.
* ``template/grok-bot.json``: ``source.release`` (written without a ``v``), every ``release X.Y.Z`` phrase in its
  strings (the description quotes it), and the sha256 of every claimed skill whose bytes this bump changes.
* Every ``vX.Y.Z`` pin that ``check.sh`` step 4 checks: the same files (``*.md``, ``*.mdc``, ``*.json``,
  ``*.toml``; not ``.git``, ``__pycache__``, ``node_modules``, ``.venv``, ``tests/fixtures/``, ``LICENSES/``, the
  root ``CHANGELOG.md``) and the same pattern.
* When the tree carries ``scripts/gen_role_skills.py`` (``check.sh`` step 10 runs only then): every role skill,
  rendered with that generator's own functions from the bumped agent file and the new version, so its
  ``metadata.version``, its ``source_sha256`` and its copy of the System prompt agree with ``--check``.
* When the tree carries ``scripts/gen_tools_doc.py`` (step 9): every ``- tool version: X`` line of ``docs/TOOLS.md``
  (a tool's version is the release version).

What never moves
----------------
* ``CHANGELOG.md`` and the release record ``docs/RELEASE.md`` (``RECORD_FILES``): history is never rewritten. The
  record names its release without the leading ``v``; a ``vX.Y.Z`` there is refused, because the bump would leave it
  and step 4 would then fail.
* A plain ``X.Y.Z`` in prose (``release 0.1.0`` in a README status line, a quoted gate output): it may be history or
  current text, so the tool lists each one under ``review`` (file, line) and leaves it for a person. A skill's own
  ``metadata`` ``version:`` line is not listed.

Refusals (nothing is written)
-----------------------------
* ``<next-version>`` is not plain ``X.Y.Z``: three ASCII numbers without leading zeros, no ``v``, no suffix (exit 2).
* ``<next-version>`` is not greater than the current version (numeric order: 0.1.10 > 0.1.9). With ``--check`` the
  current version itself is accepted: the run then verifies that nothing is left to move.
* The tag ``refs/tags/v<next-version>`` exists. Tags are read as files, never through a subprocess: the loose ref
  and ``packed-refs`` of the repository's common directory. A worktree's ``.git`` is a file (``gitdir: <path>``,
  relative to the root when not absolute) whose directory names the common directory in ``commondir``. A ``.git``
  that cannot be read, points nowhere, or stores its refs as ``reftable`` refuses the run rather than guess: every
  path is looked up with ``os.stat``/``os.lstat`` and only "no such file" counts as absent, so a directory a user
  who is not root cannot search (``EACCES``) refuses instead of reading as "no tag". A tree
  without ``.git`` is said out loud (``tag: not a git checkout``) and the run goes on; the remote's tags are never
  read. A missing tag for the current version is a note.
* The tree is not consistently at its current version: a pin naming another tag, a manifest version, a marketplace
  ref or the template release out of step, a template hash that does not match its file, a role skill or
  ``docs/TOOLS.md`` that differs from its generator, a file that is not UTF-8, a planned file that is a symbolic
  link, a JSON file whose version cannot be moved without changing another value. Every problem is reported at
  once.

Writing
-------
Every changed file is staged as a temporary file in its own directory (written, ``fsync``'d, given the target's
mode); only when every file is staged are they moved into place with ``os.replace``. A failure while staging
replaces nothing; a failure while replacing restores every file already replaced from its original bytes and names
any it could not. Just before staging, every target is re-read and must still hold the bytes the plan was made from.
Text is edited in place (formatting and line endings kept); each JSON file is parsed again afterwards and must equal
the original with exactly the planned values moved.

Output and exit codes
---------------------
Prose: one header line, the tag line, ``note:`` lines, one ``change: <file>: <what>`` line per file, ``review:``
lines, then the outcome. ``--json`` prints one object: ``ok``, ``mode`` (``write``/``check``), ``root``,
``current``, ``next``, ``tag`` (``name``, ``checkout``, ``exists``, ``refs``), ``changes`` (``file``, ``what``),
``review`` (``file``, ``line``, ``text``), ``notes``, ``problems``, ``written``.

| Exit | Meaning |
| --- | --- |
| 0 | every file written; with ``--check``, nothing to change |
| 1 | ``--check``: files would change (listed); nothing written |
| 2 | usage: bad arguments or a version that is not plain ``X.Y.Z`` |
| 3 | refused (above); nothing written |
| 4 | a write failed; every replaced file was restored, or the ones that could not be are named |

This is a maintainer script, not a desk tool: it has no ``--schema`` and no ``build_parser`` (``gen_tools_doc.py``
lists every script that defines ``build_parser`` and ``main`` as a tool).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

import check_manifests
import gen_role_skills

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFESTS = check_manifests.MANIFESTS
TEMPLATE = check_manifests.TEMPLATE
PLUGIN = check_manifests.ROOT_MANIFEST
REF_PINNED = tuple(check_manifests.URL_PINNED_MARKETPLACES) + (check_manifests.CLAUDE_MARKETPLACE,)
# check.sh step 4, exactly: the files it reads and the pattern it matches.
PIN_SUFFIXES = (".md", ".mdc", ".json", ".toml")
EXCLUDED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv"})
EXCLUDED_PREFIXES = ("tests/fixtures/", "LICENSES/")
STEP4_EXEMPT = ("CHANGELOG.md",)
PIN_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
# History: never written. CHANGELOG.md is exempt from step 4; the release record is not, so it names no tag.
RECORD_FILES = ("CHANGELOG.md", "docs/RELEASE.md")
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
RELEASE_PHRASE_RE = re.compile(r"\brelease ([0-9]+\.[0-9]+\.[0-9]+)\b")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ROLE_GENERATOR = "scripts/gen_role_skills.py"
TOOLS_GENERATOR = "scripts/gen_tools_doc.py"
TOOLS_DOC = "docs/TOOLS.md"
TOOL_VERSION_PREFIX = "- tool version: "
REVIEW_SUFFIXES = (".md", ".mdc", ".toml")
TEMP_MARK = ".release-bump-"

EXIT_OK = 0
EXIT_WOULD_CHANGE = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_WRITE_FAILED = 4


class RefsError(Exception):
    """The repository's tags cannot be read as files; the run refuses rather than guess."""


class TagLookup(NamedTuple):
    checkout: bool
    exists: bool
    refs: Optional[str]


@dataclasses.dataclass
class Change:
    """One file the bump rewrites: the bytes the plan was made from, the new text and what moves in it."""

    original: bytes
    text: str
    what: str


@dataclasses.dataclass
class Plan:
    current: Optional[str]
    target: str
    changes: Dict[str, Change]
    problems: List[str]
    review: List[Dict[str, Any]]


def version_key(version: str) -> Tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


# ---------------------------------------------------------------------------------------------------------------------
# Tags, read as files


def _read_small(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise RefsError("%s: %s" % (path, exc)) from None


def _mode(path: str, follow: bool = True) -> Optional[int]:
    """``st_mode`` of ``path`` (of the link itself when not ``follow``), ``None`` only when it does not exist.

    ``os.path.exists``, ``isdir`` and ``isfile`` answer False on any error, so for a user who is not root a directory
    without search permission would hide a tag. Here only ``FileNotFoundError`` means absent; any other error (a
    permission refused, a path component that is not a directory) raises ``RefsError``."""
    try:
        return (os.stat(path) if follow else os.lstat(path)).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RefsError("%s cannot be read: %s" % (path, exc.strerror or exc)) from None


def _directory(path: str, what: str) -> None:
    mode = _mode(path)
    if mode is None or not stat.S_ISDIR(mode):
        raise RefsError("%s, which is not a directory" % what)


def git_directories(root: str) -> Optional[Tuple[str, str]]:
    """``(git dir, common dir)`` of the checkout at ``root``; ``None`` when ``root`` has no ``.git``."""
    dotgit = os.path.join(root, ".git")
    mode = _mode(dotgit)
    if mode is None:
        if _mode(dotgit, follow=False) is not None:
            raise RefsError("%s is a symbolic link to nothing" % dotgit)
        return None
    if stat.S_ISDIR(mode):
        gitdir = dotgit
    elif stat.S_ISREG(mode):
        text = _read_small(dotgit).strip()
        if not text.startswith("gitdir:") or not text[len("gitdir:"):].strip():
            raise RefsError("%s is a file without a 'gitdir: <path>' line" % dotgit)
        target = text[len("gitdir:"):].strip().splitlines()[0].strip()
        gitdir = os.path.normpath(target if os.path.isabs(target) else os.path.join(root, target))
        _directory(gitdir, "%s points at %s" % (dotgit, gitdir))
    else:
        raise RefsError("%s is neither a directory nor a readable file" % dotgit)
    common = gitdir
    pointer = os.path.join(gitdir, "commondir")
    pointer_mode = _mode(pointer)
    if pointer_mode is not None:
        if not stat.S_ISREG(pointer_mode):
            raise RefsError("%s is not a regular file" % pointer)
        value = _read_small(pointer).strip()
        if not value:
            raise RefsError("%s is empty" % pointer)
        common = os.path.normpath(value if os.path.isabs(value) else os.path.join(gitdir, value))
        _directory(common, "%s names %s" % (pointer, common))
    return gitdir, common


def tag_lookup(root: str, tag: str) -> TagLookup:
    """Whether ``refs/tags/<tag>`` exists in the checkout at ``root`` (``RefsError`` when that cannot be read)."""
    directories = git_directories(root)
    if directories is None:
        return TagLookup(False, False, None)
    gitdir, common = directories
    reftable = _mode(os.path.join(common, "reftable"))
    if reftable is not None and stat.S_ISDIR(reftable):
        raise RefsError("%s stores its refs in reftable format, which this tool does not read" % common)
    ref = "refs/tags/" + tag
    for base in (common, gitdir):
        if _mode(os.path.join(base, *ref.split("/")), follow=False) is not None:
            return TagLookup(True, True, common)
    packed = os.path.join(common, "packed-refs")
    if _mode(packed) is not None:
        for line in _read_small(packed).splitlines():
            if not line.strip() or line.startswith(("#", "^")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip() == ref:
                return TagLookup(True, True, common)
    return TagLookup(True, False, common)


# ---------------------------------------------------------------------------------------------------------------------
# Planning


def pinned_files(root: str) -> List[str]:
    """The files ``check.sh`` step 4 reads, as root-relative paths with ``/``, in its order."""
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames
                             if d not in EXCLUDED_DIRS and not (rel_dir + d + "/").startswith(EXCLUDED_PREFIXES))
        for name in sorted(filenames):
            rel = rel_dir + name
            if rel.endswith(PIN_SUFFIXES) and not rel.startswith(EXCLUDED_PREFIXES) and rel not in STEP4_EXEMPT:
                found.append(rel)
    return found


def map_strings(value: Any, function: Callable[[str], str]) -> Any:
    """``value`` with ``function`` applied to every string in it, keys included."""
    if isinstance(value, str):
        return function(value)
    if isinstance(value, list):
        return [map_strings(item, function) for item in value]
    if isinstance(value, dict):
        return {function(key): map_strings(item, function) for key, item in value.items()}
    return value


def diff_paths(old: Any, new: Any, path: str = "") -> List[str]:
    """The dotted paths whose values differ between two parsed JSON documents."""
    if isinstance(old, dict) and isinstance(new, dict):
        found: List[str] = []
        for key in sorted(set(old) | set(new)):
            sub = key if not path else path + "." + key
            if key not in old or key not in new:
                found.append(sub)
            else:
                found.extend(diff_paths(old[key], new[key], sub))
        return found
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        found = []
        for index, (left, right) in enumerate(zip(old, new)):
            found.extend(diff_paths(left, right, "%s[%d]" % (path, index)))
        return found
    return [] if old == new else [path or "(document)"]


def field_re(key: str, value: str) -> "re.Pattern[str]":
    """``"key": "value"`` as written in a JSON file, whatever the spacing around the colon."""
    return re.compile(r'("%s"\s*:\s*")%s(")' % (re.escape(key), re.escape(value)))


def string_values(value: Any, path: str = "") -> List[Tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        return [item for index, element in enumerate(value)
                for item in string_values(element, "%s[%d]" % (path, index))]
    if isinstance(value, dict):
        return [item for key, element in value.items()
                for item in string_values(element, key if not path else path + "." + key)]
    return []


class Planner:
    """Builds a :class:`Plan`: reads the tree once, edits texts in memory, reports every problem it finds."""

    def __init__(self, root: str, target: str) -> None:
        self.root = os.path.abspath(root)
        self.target = target
        self.current: Optional[str] = None
        self.problems: List[str] = []
        self.originals: Dict[str, bytes] = {}
        self.texts: Dict[str, str] = {}
        self.labels: Dict[str, List[str]] = {}
        self.review: List[Dict[str, Any]] = []

    # -- reading -------------------------------------------------------------------------------------------------
    def load(self, rel: str) -> Optional[str]:
        """The file's text as last edited in this plan (``None`` and a problem when it cannot be read)."""
        if rel in self.texts:
            return self.texts[rel]
        path = os.path.join(self.root, rel)
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            self.problems.append("%s: cannot be read: %s" % (rel, exc))
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.problems.append("%s: not valid UTF-8 (%s)" % (rel, exc))
            return None
        self.originals[rel] = raw
        self.texts[rel] = text
        return text

    def original_text(self, rel: str) -> str:
        return self.originals[rel].decode("utf-8")

    def edit(self, rel: str, text: str, label: Optional[str] = None) -> None:
        if text != self.texts[rel]:
            self.texts[rel] = text
            if label:
                self.labels.setdefault(rel, []).append(label)

    # -- the steps -----------------------------------------------------------------------------------------------
    def read_current(self) -> bool:
        text = self.load(PLUGIN)
        if text is None:
            return False
        try:
            document = json.loads(text)
        except ValueError as exc:
            self.problems.append("%s: cannot read the version: %s" % (PLUGIN, exc))
            return False
        version = document.get("version") if isinstance(document, dict) else None
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            self.problems.append("%s: version %r is not a plain X.Y.Z release version" % (PLUGIN, version))
            return False
        self.current = version
        return True

    def move_pins(self) -> None:
        """Step 4's pins: every token must name the current release; each moves to the next one."""
        expected = "v" + self.current
        for rel in pinned_files(self.root):
            text = self.load(rel)
            if text is None:
                continue
            tokens = [(text.count("\n", 0, match.start()) + 1, match.group(0)) for match in PIN_RE.finditer(text)]
            if rel in RECORD_FILES:
                for token in sorted({token for _line, token in tokens}):
                    self.problems.append(
                        "%s: names %s; this release record is never rewritten by a bump, so check.sh step 4 would "
                        "fail after it; name the release there without the leading v (%s)" % (rel, token, token[1:]))
                continue
            for line, token in tokens:
                if token != expected:
                    self.problems.append("%s:%d: names %s, but %s declares %s; fix the pin before the bump"
                                         % (rel, line, token, PLUGIN, expected))
            count = sum(1 for _line, token in tokens if token == expected)
            if count and self.target != self.current:
                moved = PIN_RE.sub(lambda m: "v" + self.target if m.group(0) == expected else m.group(0), text)
                self.edit(rel, moved, "%d tag pin(s)" % count)
                if rel.endswith(".json") and rel not in MANIFESTS and rel != TEMPLATE:
                    self.verify_moved_json(rel)

    def verify_moved_json(self, rel: str) -> None:
        """A JSON file outside the manifests and the template: parsed again, only its pins moved."""
        try:
            original = json.loads(self.original_text(rel))
        except ValueError:
            return  # check.sh step 4 reads it as text too; there is no JSON to keep
        self.verify_json(rel, map_strings(original, self.move_pins_in))

    def move_pins_in(self, value: str) -> str:
        expected = "v" + self.current
        return PIN_RE.sub(lambda m: "v" + self.target if m.group(0) == expected else m.group(0), value)

    def parsed_original(self, rel: str) -> Optional[Any]:
        try:
            return json.loads(self.original_text(rel))
        except ValueError as exc:
            self.problems.append("%s: invalid JSON: %s" % (rel, exc))
            return None

    def verify_json(self, rel: str, expected: Any) -> None:
        try:
            got = json.loads(self.texts[rel])
        except ValueError as exc:
            self.problems.append("%s: the edit would break the JSON (%s); edit it by hand" % (rel, exc))
            return
        if got != expected:
            self.problems.append("%s: the version cannot be moved without changing another value (%s); edit it by "
                                 "hand" % (rel, ", ".join(diff_paths(expected, got)) or "layout"))

    def move_manifests(self) -> None:
        for rel in MANIFESTS:
            if self.load(rel) is None:
                continue
            original = self.parsed_original(rel)
            if original is None:
                continue
            if not isinstance(original, dict):
                self.problems.append("%s: manifest must be a JSON object" % rel)
                continue
            for prefix, entry in check_manifests.entries(original):
                if "version" in entry and entry["version"] != self.current:
                    self.problems.append("%s: %sversion %r does not match %s version '%s'"
                                         % (rel, prefix, entry["version"], PLUGIN, self.current))
                metadata = entry.get("metadata")
                if isinstance(metadata, dict) and "version" in metadata and metadata["version"] != self.current:
                    self.problems.append("%s: %smetadata.version %r does not match %s version '%s'"
                                         % (rel, prefix, metadata["version"], PLUGIN, self.current))
                if prefix and rel in REF_PINNED:
                    source = entry.get("source")
                    ref = source.get("ref") if isinstance(source, dict) else None
                    if ref != "v" + self.current:
                        self.problems.append("%s: %ssource.ref %r does not pin v%s" % (rel, prefix, ref, self.current))
            if self.target == self.current:
                continue
            expected = map_strings(original, self.move_pins_in)
            for _prefix, entry in check_manifests.entries(expected):
                if entry.get("version") == self.current:
                    entry["version"] = self.target
                metadata = entry.get("metadata")
                if isinstance(metadata, dict) and metadata.get("version") == self.current:
                    metadata["version"] = self.target
            self.edit(rel, field_re("version", self.current).sub(r"\g<1>%s\g<2>" % self.target, self.texts[rel]))
            self.verify_json(rel, expected)

    def move_role_skills(self) -> None:
        """Every role skill, rendered by the generator's own functions from the bumped agent and the new version."""
        if not os.path.isfile(os.path.join(self.root, ROLE_GENERATOR)):
            return
        for agent_rel in gen_role_skills.agent_rels(self.root):
            try:
                agent = gen_role_skills.read_agent(self.root, agent_rel)
            except (gen_role_skills.GenError, OSError, UnicodeDecodeError) as exc:
                self.problems.append("%s: cannot render its role skill: %s" % (agent_rel, exc))
                continue
            role_rel = "%s/%s/SKILL.md" % (gen_role_skills.SKILLS_DIR, agent.skill)
            current = self.load(role_rel) if os.path.isfile(os.path.join(self.root, role_rel)) else None
            if current is None:
                self.problems.append("%s: missing; run python3 %s before the bump" % (role_rel, ROLE_GENERATOR))
                continue
            if self.original_text(role_rel) != gen_role_skills.render_skill(agent, self.current):
                self.problems.append("%s: differs from its rendering from %s; run python3 %s before the bump"
                                     % (role_rel, agent_rel, ROLE_GENERATOR))
                continue
            if self.target == self.current:
                continue
            bumped = self.texts.get(agent_rel)
            if bumped is not None and bumped != self.original_text(agent_rel):
                fields, body = gen_role_skills.frontmatter(bumped, agent_rel)
                agent = dataclasses.replace(
                    agent, title=fields.get("title", agent.title),
                    description=fields.get("description", agent.description),
                    seat=fields.get("seat", agent.seat), system_prompt=gen_role_skills.system_prompt(body, agent_rel),
                    sha256=hashlib.sha256(bumped.encode("utf-8")).hexdigest())
            self.texts[role_rel] = gen_role_skills.render_skill(agent, self.target)
            self.labels[role_rel] = ["rendered by %s at %s" % (ROLE_GENERATOR, self.target)]

    def move_tool_versions(self) -> None:
        if not (os.path.isfile(os.path.join(self.root, TOOLS_GENERATOR))
                and os.path.isfile(os.path.join(self.root, TOOLS_DOC))):
            return
        text = self.load(TOOLS_DOC)
        if text is None:
            return
        lines = text.split("\n")
        count = 0
        for index, line in enumerate(lines):
            if not line.startswith(TOOL_VERSION_PREFIX):
                continue
            value = line[len(TOOL_VERSION_PREFIX):].rstrip("\r")
            if value != self.current:
                self.problems.append("%s:%d: tool version %s, but %s declares %s; run python3 %s before the bump"
                                     % (TOOLS_DOC, index + 1, value, PLUGIN, self.current, TOOLS_GENERATOR))
                continue
            lines[index] = TOOL_VERSION_PREFIX + self.target + line[len(TOOL_VERSION_PREFIX) + len(value):]
            count += 1
        if count and self.target != self.current:
            self.edit(TOOLS_DOC, "\n".join(lines), "%d tool version line(s)" % count)

    def move_template(self) -> None:
        """``source.release``, the ``release X.Y.Z`` phrases and the hashes of the claimed skills that change."""
        if self.load(TEMPLATE) is None:
            return
        original = self.parsed_original(TEMPLATE)
        if not isinstance(original, dict):
            if original is not None:
                self.problems.append("%s: must be a JSON object" % TEMPLATE)
            return
        source = original.get("source")
        release = source.get("release") if isinstance(source, dict) else None
        if release != self.current:
            self.problems.append("%s: source.release %r does not match %s version '%s'"
                                 % (TEMPLATE, release, PLUGIN, self.current))
        for path, value in string_values(original):
            for phrase in RELEASE_PHRASE_RE.findall(value):
                if phrase != self.current:
                    self.problems.append("%s: %s names release %s, but %s declares %s"
                                         % (TEMPLATE, path, phrase, PLUGIN, self.current))
        claims = self.template_claims(original)
        if self.target == self.current:
            return
        labels: List[str] = []
        expected = map_strings(original, self.move_pins_in)
        text = self.texts[TEMPLATE]
        if isinstance(expected.get("source"), dict) and expected["source"].get("release") == self.current:
            expected["source"]["release"] = self.target
            text = field_re("release", self.current).sub(r"\g<1>%s\g<2>" % self.target, text)
            labels.append("source.release")
        phrase_re = re.compile(r"\brelease %s\b" % re.escape(self.current))
        if phrase_re.search(text):
            labels.append("description release" if phrase_re.search(str(expected.get("description", "")))
                          else "release phrase")
            text = phrase_re.sub("release " + self.target, text)
            expected = map_strings(expected, lambda value: phrase_re.sub("release " + self.target, value))
        for table, name, digest, rel in claims:
            if rel not in self.texts or self.texts[rel] == self.original_text(rel):
                continue
            new_digest = hashlib.sha256(self.texts[rel].encode("utf-8")).hexdigest()
            if text.count(digest) != 1:
                self.problems.append("%s: %s.%s: its sha256 appears %d times; edit the template by hand"
                                     % (TEMPLATE, table, name, text.count(digest)))
                continue
            text = text.replace(digest, new_digest)
            entry = expected[table][name]
            if isinstance(entry, dict):
                entry["sha256"] = new_digest
            else:
                expected[table][name] = new_digest
            labels.append("%s.%s sha256" % (table, name))
        self.edit(TEMPLATE, text)
        self.labels[TEMPLATE] = labels
        self.verify_json(TEMPLATE, expected)

    def template_claims(self, template: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
        """``(table, name, sha256, path)`` for every claimed skill whose hash matches its current file."""
        claims: List[Tuple[str, str, str, str]] = []
        for table in ("skills", "roleSkills"):
            entries = template.get(table)
            if not isinstance(entries, dict):
                continue
            for name, value in entries.items():
                rel = "skills/%s/SKILL.md" % name
                digest = value.get("sha256") if isinstance(value, dict) else value
                if isinstance(value, dict) and value.get("path", rel) != rel:
                    self.problems.append("%s: %s.%s path must be %s" % (TEMPLATE, table, name, rel))
                    continue
                if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                    self.problems.append("%s: %s.%s sha256 must be a 64-character lowercase hex digest"
                                         % (TEMPLATE, table, name))
                    continue
                if not os.path.isfile(os.path.join(self.root, rel)):
                    self.problems.append("%s: %s.%s points at a missing file %s" % (TEMPLATE, table, name, rel))
                    continue
                if rel not in self.originals and self.load(rel) is None:
                    continue
                if hashlib.sha256(self.originals[rel]).hexdigest() != digest:
                    self.problems.append("%s: %s.%s sha256 does not match %s; the bump would re-pin bytes nobody "
                                         "reviewed (check_grok_template.py names the same problem)"
                                         % (TEMPLATE, table, name, rel))
                    continue
                claims.append((table, name, digest, rel))
        return claims

    def collect_review(self) -> None:
        """Plain mentions of the current version in prose: left as they are, listed for a person."""
        plain = re.compile(r"(?<![\w.])%s(?!\w|\.\d)" % re.escape(self.current))
        metadata = re.compile(r"""^\s*version:\s*["']?%s["']?\s*$""" % re.escape(self.current))
        tools_doc_moved = TOOLS_DOC in self.labels
        for rel in pinned_files(self.root):
            if rel in RECORD_FILES or not rel.endswith(REVIEW_SUFFIXES) or rel not in self.originals:
                continue
            for number, line in enumerate(self.original_text(rel).split("\n"), 1):
                if not plain.search(line) or metadata.match(line):
                    continue
                if tools_doc_moved and rel == TOOLS_DOC and line.startswith(TOOL_VERSION_PREFIX):
                    continue
                self.review.append({"file": rel, "line": number, "text": line.strip()})

    def check_targets(self, changes: Dict[str, Change]) -> None:
        for rel in sorted(changes):
            path = os.path.join(self.root, rel)
            if os.path.islink(path):
                self.problems.append("%s: is a symbolic link; the bump writes regular files only" % rel)
            elif not os.path.isfile(path):
                self.problems.append("%s: is not a regular file" % rel)

    def run(self, verify_only: bool) -> Plan:
        if not self.read_current():
            return Plan(None, self.target, {}, self.problems, [])
        if verify_only and self.target != self.current:
            self.problems.append("%s is not the current version %s" % (self.target, self.current))
        self.move_pins()
        self.move_manifests()
        self.move_role_skills()
        self.move_tool_versions()
        self.move_template()
        changes: Dict[str, Change] = {}
        for rel in sorted(self.texts):
            if self.texts[rel] == self.original_text(rel):
                continue
            what = self.labels.get(rel)
            if rel.endswith(".json") and rel != TEMPLATE:
                try:
                    what = diff_paths(json.loads(self.original_text(rel)), json.loads(self.texts[rel])) or what
                except ValueError:
                    pass
            changes[rel] = Change(self.originals[rel], self.texts[rel], ", ".join(what or ["edited"]))
        self.check_targets(changes)
        if self.target != self.current:
            self.collect_review()
        return Plan(self.current, self.target, changes, self.problems, self.review)


def plan(root: str, target: str, verify_only: bool = False) -> Plan:
    """What a bump of the tree at ``root`` to ``target`` would change, and every problem that refuses it.

    ``verify_only`` asks that ``target`` be the current version (the ``--check`` verification). Ordering and tags are
    :func:`main`'s; this function reads the tree and writes nothing."""
    return Planner(root, target).run(verify_only)


# ---------------------------------------------------------------------------------------------------------------------
# Writing: stage everything, then replace everything, restoring on failure


def _fsync_directory(directory: str) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _stage(path: str, data: bytes, mode: int) -> str:
    """Write ``data`` to a new temporary file beside ``path``; return its path."""
    descriptor, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + TEMP_MARK, suffix=".tmp",
                                             dir=os.path.dirname(path))
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(temporary, mode)
    except BaseException:
        _unlink_quietly(temporary)
        raise
    return temporary


def _restore(root: str, rels: Sequence[str], changes: Dict[str, Change], modes: Dict[str, int]) -> List[str]:
    """Put back the original bytes of ``rels``; return the ones that could not be restored."""
    failed: List[str] = []
    for rel in rels:
        path = os.path.join(root, rel)
        temporary = None
        try:
            temporary = _stage(path, changes[rel].original, modes[rel])
            os.replace(temporary, path)
        except OSError:
            if temporary is not None:
                _unlink_quietly(temporary)
            failed.append(rel)
    return failed


def apply(root: str, changes: Dict[str, Change]) -> Tuple[bool, List[str]]:
    """Write every change or none (module notes, "Writing"); ``(ok, lines)``."""
    root = os.path.abspath(root)
    order = sorted(changes)
    modes: Dict[str, int] = {}
    for rel in order:
        path = os.path.join(root, rel)
        try:
            if os.path.islink(path):
                raise OSError("is a symbolic link")
            with open(path, "rb") as handle:
                current = handle.read()
            modes[rel] = stat.S_IMODE(os.stat(path).st_mode)
        except OSError as exc:
            return False, ["%s cannot be read again (%s); nothing written" % (rel, exc)]
        if current != changes[rel].original:
            return False, ["%s changed since the plan; nothing written" % rel]
    staged: List[Tuple[str, str]] = []
    for rel in order:
        try:
            staged.append((rel, _stage(os.path.join(root, rel), changes[rel].text.encode("utf-8"), modes[rel])))
        except OSError as exc:
            for _rel, temporary in staged:
                _unlink_quietly(temporary)
            return False, ["could not stage %s: %s; nothing written" % (rel, exc)]
    replaced: List[str] = []
    for index, (rel, temporary) in enumerate(staged):
        try:
            os.replace(temporary, os.path.join(root, rel))
        except OSError as exc:
            for _rel, left in staged[index:]:
                _unlink_quietly(left)
            lines = ["could not replace %s: %s" % (rel, exc)]
            failed = _restore(root, replaced, changes, modes)
            if failed:
                lines.append("could not restore %d file(s): %s; restore them from the last commit"
                             % (len(failed), ", ".join(failed)))
            else:
                lines.append("restored %d file(s); the tree is as it was" % len(replaced))
            return False, lines
        replaced.append(rel)
    for directory in sorted({os.path.dirname(os.path.join(root, rel)) for rel in replaced}):
        _fsync_directory(directory)
    return True, []


# ---------------------------------------------------------------------------------------------------------------------
# Command line


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_bump.py",
        description="Move the release version in plugin.json, every mirrored manifest, the marketplace refs, the "
                    "template and every documented pin, together, or nothing.")
    parser.add_argument("version", help="the next release, plain X.Y.Z (no v, no suffix)")
    parser.add_argument("--check", action="store_true",
                        help="print what would change and write nothing; exit 1 when anything would change")
    parser.add_argument("--root", default=ROOT, help="repository root (default: the checkout this script is in)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of prose")
    return parser


def _emit(report: Dict[str, Any], lines: List[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("\n".join(lines))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    target = args.version
    if not VERSION_RE.fullmatch(target):
        parser.error("%r is not a plain X.Y.Z version (three numbers without leading zeros; no v, no suffix)" % target)
    root = os.path.abspath(args.root)
    mode = "check" if args.check else "write"
    report: Dict[str, Any] = {"ok": False, "mode": mode, "root": root, "current": None, "next": target, "tag": None,
                              "changes": [], "review": [], "notes": [], "problems": [], "written": False}
    lines: List[str] = []

    def refuse(problems: List[str]) -> int:
        report["problems"] = problems
        lines.extend("refused: %s" % problem for problem in problems)
        lines.append("refused: nothing written")
        _emit(report, lines, args.json)
        return EXIT_REFUSED

    probe = Planner(root, target)
    if not probe.read_current():
        return refuse(probe.problems)
    current = probe.current
    report["current"] = current
    lines.append("release_bump: %s -> %s in %s%s" % (current, target, root, " (--check: nothing is written)"
                                                     if args.check else ""))
    verify_only = args.check and target == current
    if version_key(target) < version_key(current) or (target == current and not args.check):
        return refuse(["%s is not greater than the current version %s" % (target, current)])

    problems: List[str] = []
    if verify_only:
        lines.append("tag: not checked (the version does not move)")
    else:
        tag = "v" + target
        try:
            lookup = tag_lookup(root, tag)
        except RefsError as exc:
            problems.append("cannot read the tags of %s: %s; release_bump reads .git as files and refuses rather than "
                            "guess" % (root, exc))
            lookup = None
        if lookup is not None:
            report["tag"] = {"name": tag, "checkout": lookup.checkout, "exists": lookup.exists, "refs": lookup.refs}
            if not lookup.checkout:
                lines.append("tag: not a git checkout; no local tag %s can exist here (tags on the remote are not "
                             "read)" % tag)
            elif lookup.exists:
                problems.append("tag %s already exists (%s); a tagged release is never edited in place: pick the "
                                "next version" % (tag, lookup.refs))
            else:
                lines.append("tag: %s is not a local tag (refs read from %s; tags on the remote are not read)"
                             % (tag, lookup.refs))
                try:
                    if not tag_lookup(root, "v" + current).exists:
                        report["notes"].append("v%s has no local tag; a bump now skips releasing %s"
                                               % (current, current))
                except RefsError:
                    pass
    result = plan(root, target, verify_only=verify_only)
    problems.extend(result.problems)
    report["changes"] = [{"file": rel, "what": change.what} for rel, change in sorted(result.changes.items())]
    report["review"] = result.review
    lines.extend("note: %s" % note for note in report["notes"])
    if problems:
        return refuse(problems)
    lines.extend("change: %s: %s" % (entry["file"], entry["what"]) for entry in report["changes"])
    lines.extend("review: %s:%d: names %s without the leading v; the bump leaves it as it is"
                 % (entry["file"], entry["line"], current) for entry in result.review)
    if not result.changes:
        report["ok"] = True
        lines.append("nothing to change: every pin already names %s" % current)
        _emit(report, lines, args.json)
        return EXIT_OK
    if args.check:
        lines.append("would change %d file(s); nothing written (--check)" % len(result.changes))
        _emit(report, lines, args.json)
        return EXIT_WOULD_CHANGE
    ok, failure = apply(root, result.changes)
    if not ok:
        report["problems"] = failure
        lines.extend("failed: %s" % line for line in failure)
        _emit(report, lines, args.json)
        return EXIT_WRITE_FAILED
    report["ok"] = True
    report["written"] = True
    lines.append("written: %d file(s); next: bash scripts/check.sh, then commit the bump on its own"
                 % len(result.changes))
    _emit(report, lines, args.json)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
