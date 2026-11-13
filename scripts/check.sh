#!/usr/bin/env bash
# Aegis Desk repository gate. Stdlib Python only; no network; nothing here can place an order.
#
# Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
# changes, commit 75a4c79 (scripts/check.sh). The checks are Aegis Desk's own.
#
# Usage: bash scripts/check.sh [repo-root]
#   Without an argument the gate checks the repository it lives in. With an argument it checks
#   that tree instead (the unit tests run it against fixture trees under tests/fixtures/gates).
#   The gate scripts always come from this checkout; the tree under test supplies its own
#   agents, skills, manifests, tests, test-count floor and generated documents.
#
# Steps (every step prints an "ok:" line or fails the run with file-qualified error lines):
#   1. agents/*.md         frontmatter (no repeated keys), name == stem, title/description,
#                          model: inherit, seat, skills resolve, writes_to_broker exactly "false"
#                          (zero writers), "## Bot profile" + "## System prompt", the boundary
#                          sentence outside HTML comments and code fences; nothing but top-level
#                          .md files under agents/; at least one agent and one skill
#   2. skills/*/SKILL.md   aegis- prefix, name == directory, description <= 1024, license MIT,
#                          metadata version/author/category, body <= 320 lines, LICENSE and
#                          ATTRIBUTION.md (origin: new|adapted) beside it, an adapted skill's
#                          in-body notice line, indexed in skills/README.md and SETUP.md
#   3. links/emoji/scripts markdown links resolve; no emoji in instruction files (the desk view's
#                          three status symbols only inside a fenced example); every
#                          scripts/<name>.py named in agents, skills, rules, docs, SETUP, README,
#                          CONTRIBUTING, AGENTS, GEMINI, CLAUDE and the routine manifest
#                          (non-comment lines) exists, unless the line marks it "not yet" /
#                          "lands with" a later pull request
#   4. version pins        every vX.Y.Z token in *.md, *.mdc, *.json, *.toml (CHANGELOG.md and
#                          LICENSES/ exempt) equals "v" + plugin.json version
#   5. python floor        every scripts/hooks/tests module parses as Python 3.9 and begins with
#                          "from __future__ import annotations"; no 3.10+ runtime features
#   6. sub-gates           check_manifests.py, check_no_write_paths.py (AST no-write gate and
#                          prose scanner), check_grok_template.py
#   7. unit tests          python3 -m unittest discover -s tests -t . -p 'test_*.py', with the
#                          number of tests run compared against scripts/test_count_floor.txt;
#                          zero discovered tests fail on every interpreter, whatever the floor
#   8. journeys            cli_journeys.py (commands as subprocesses in a scrubbed environment)
#   9. TOOLS.md drift      scripts/gen_tools_doc.py --check when that generator exists
#  10. role skills        scripts/gen_role_skills.py --check when that generator exists
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${1:-$HERE/..}" && pwd)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
# The unit tests run this script against the real tree; the marker stops that one recursion.
export AEGIS_CHECK_SH_NESTED=1

echo "check.sh: repository gate on $ROOT"

# ---------------------------------------------------------------------------------------------
# Steps 1-4: structural lint of the instruction files.
python3 - <<'PY'
import json
import os
import re
import sys
import urllib.parse

errors = []


def err(msg):
    errors.append(msg)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


BOUNDARY = (
    "This desk never places, modifies or cancels orders. An exit alert tells you which of your "
    "adopted rules fired and why; you act in your brokerage account."
)
EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
EXCLUDED_PREFIXES = ("tests/fixtures/", "LICENSES/")
# Emoji and pictographs: Miscellaneous Technical (U+2300-23FF: alarm clock, hourglass, keyboard
# symbols), Miscellaneous Symbols and Dingbats (U+2600-27BF), Miscellaneous Symbols and Arrows
# (U+2B00-2BFF: white medium star), the whole supplementary block U+1F000-1FAFF (playing cards,
# enclosed alphanumerics such as the OK button, regional indicators that form flags, emoticons,
# transport, supplemental symbols) and the emoji variation selector U+FE0F.
EMOJI_RE = re.compile("[⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF️]")
# The Structuring Desk's three status symbols (alert_lint.STATUS_SYMBOLS: green, yellow and red circles) may appear
# only inside a closed fenced block, where a document quotes the desk's own output; any other emoji, or these three
# anywhere else, still fails.
STATUS_SYMBOLS = ("\U0001F7E2", "\U0001F7E1", "\U0001F534")
FENCED_RE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.M | re.S)


def without_quoted_status(text):
    """``text`` with the status symbols removed from its closed fenced blocks only (the emoji check reads the rest)."""
    def strip(match):
        block = match.group(0)
        for symbol in STATUS_SYMBOLS:
            block = block.replace(symbol, "")
        return block
    return FENCED_RE.sub(strip, text)
LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SCRIPT_RE = re.compile(r"\bscripts/((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_]+\.py)\b")
PIN_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
PLAIN_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def tree(suffixes, extra_excluded=()):
    found = []
    for dirpath, dirnames, filenames in os.walk("."):
        rel_dir = os.path.relpath(dirpath, ".").replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in EXCLUDED_DIRS and not (rel_dir + d + "/").startswith(EXCLUDED_PREFIXES)
        )
        for name in sorted(filenames):
            rel = rel_dir + name
            if rel.endswith(suffixes) and not rel.startswith(EXCLUDED_PREFIXES) and rel not in extra_excluded:
                found.append(rel)
    return found


def frontmatter(path):
    """Parse the YAML subset used here: scalars, '- item' lists, inline [a, b] lists and one
    level of nested 'key: value' maps. Top-level scalar values are kept exactly as written so
    that checks such as writes_to_broker == "false" are literal."""
    text = read(path)
    if not text.startswith("---\n"):
        err(f"{path}: missing frontmatter")
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        err(f"{path}: unterminated frontmatter")
        return None, text
    block, body = text[4:end], text[end + 5:]
    data, key = {}, None
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s+(.*?))?\s*$", line)
        if top:
            key, value = top.group(1), top.group(2) or ""
            if key in data:
                err(f"{path}: frontmatter key '{key}' is declared twice; the second value would silently win")
            if value == "":
                data[key] = None
            elif value.startswith("[") and value.endswith("]"):
                data[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
            else:
                data[key] = value
            continue
        item = re.match(r"^\s+-\s+(.*?)\s*$", line)
        if item and key is not None:
            if data.get(key) is None:
                data[key] = []
            if not isinstance(data[key], list):
                err(f"{path}: frontmatter key '{key}' mixes a scalar with list items")
                continue
            data[key].append(item.group(1).strip("\"'"))
            continue
        nested = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if nested and key is not None:
            if data.get(key) is None:
                data[key] = {}
            if not isinstance(data[key], dict):
                err(f"{path}: frontmatter key '{key}' mixes a scalar with a nested map")
                continue
            data[key][nested.group(1)] = nested.group(2).strip("\"'")
            continue
        err(f"{path}: cannot parse frontmatter line {line!r}")
    return data, body


def visible(text):
    """The body as a Bot reads it: HTML comments and fenced code blocks removed, so a boundary
    sentence hidden in a comment nobody sees, or quoted inside a code block, does not count."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"(```|~~~).*?\1", "", text, flags=re.S)


# --- 2. skills --------------------------------------------------------------------------------
# A skill adapted from the reference repository carries this notice in its visible body, so the
# credit travels with the SKILL.md body itself, for example through a Grok Bot template export
# (plan section 12).
ADAPTED_NOTICE = "Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), commit 75a4c79; see ATTRIBUTION.md."
skill_dirs = sorted(d for d in os.listdir("skills") if os.path.isdir(os.path.join("skills", d))) if os.path.isdir("skills") else []
skill_names = set()
for name in skill_dirs:
    directory = os.path.join("skills", name)
    path = os.path.join(directory, "SKILL.md")
    if not name.startswith("aegis-"):
        err(f"{directory}: skill directory must start with 'aegis-'")
    if not os.path.isfile(path):
        err(f"{directory}: missing SKILL.md")
        continue
    skill_names.add(name)
    data, body = frontmatter(path)
    if data is None:
        continue
    if data.get("name") != name:
        err(f"{path}: name {data.get('name')!r} does not match the directory '{name}'")
    description = data.get("description")
    if not isinstance(description, str) or not description:
        err(f"{path}: missing description")
    elif len(description) > 1024:
        err(f"{path}: description is {len(description)} chars (limit 1024)")
    if data.get("license") != "MIT":
        err(f"{path}: license must be MIT")
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        err(f"{path}: missing metadata block")
    else:
        for field in ("version", "author", "category"):
            if not metadata.get(field):
                err(f"{path}: metadata.{field} is missing")
    line_count = body.count("\n")
    if line_count > 320:
        err(f"{path}: body is {line_count} lines (budget 320)")
    for companion in ("LICENSE", "ATTRIBUTION.md"):
        if not os.path.isfile(os.path.join(directory, companion)):
            err(f"{directory}: missing {companion}")
    attribution = os.path.join(directory, "ATTRIBUTION.md")
    if os.path.isfile(attribution):
        text = read(attribution)
        origin = re.search(r"^origin: (new|adapted)$", text, re.M)
        if not origin:
            err(f"{attribution}: needs a line 'origin: new' or 'origin: adapted'")
        elif origin.group(1) == "adapted" and "Galleon Labs" not in text:
            err(f"{attribution}: adapted material must credit Galleon Labs")
        if origin and origin.group(1) == "adapted" and ADAPTED_NOTICE not in visible(body):
            err(f"{path}: ATTRIBUTION.md says origin: adapted, so the body must carry the notice line "
                f"{ADAPTED_NOTICE!r} outside HTML comments and code fences")

if not skill_dirs:
    err("skills/: at least one aegis- skill is required")


# --- 1. agents --------------------------------------------------------------------------------
# Only top-level agents/*.md files are roles; a subdirectory or a stray file would escape the
# lint, so either fails. The zero-writers claim needs at least one agent to be about.
agent_paths = []
if os.path.isdir("agents"):
    for entry in sorted(os.listdir("agents")):
        full = os.path.join("agents", entry)
        if os.path.isdir(full):
            err(f"{full}: agents/ may hold only agent .md files at its top level (a subdirectory would escape the lint)")
        elif not entry.endswith(".md"):
            err(f"{full}: agents/ may hold only agent .md files (found a non-markdown file)")
        else:
            agent_paths.append(full)
else:
    err("agents/: missing")
if not agent_paths:
    err("agents/: at least one agent role is required")
agent_names = []
writers = []
for path in agent_paths:
    stem = os.path.basename(path)[:-3]
    data, body = frontmatter(path)
    if data is None:
        continue
    body = visible(body)
    agent_names.append(stem)
    if data.get("name") != stem:
        err(f"{path}: name {data.get('name')!r} does not match the file stem '{stem}'")
    for field in ("title", "description"):
        if not isinstance(data.get(field), str) or not data.get(field):
            err(f"{path}: missing '{field}'")
    if data.get("model") != "inherit":
        err(f"{path}: model must be 'inherit' (found {data.get('model')!r})")
    if data.get("seat") not in ("floor", "off-floor"):
        err(f"{path}: seat must be 'floor' or 'off-floor' (found {data.get('seat')!r})")
    skills = data.get("skills")
    if not isinstance(skills, list):
        err(f"{path}: skills must be a list")
    else:
        for skill in skills:
            if skill not in skill_names:
                err(f"{path}: references unknown skill '{skill}' (no skills/{skill}/SKILL.md)")
    writes = data.get("writes_to_broker")
    if writes != "false":
        err(f"{path}: writes_to_broker must be exactly the string 'false' (found {writes!r})")
        writers.append(path)
    for heading in ("## Bot profile", "## System prompt"):
        if heading not in body:
            err(f"{path}: body needs a '{heading}' section")
    if BOUNDARY not in body:
        err(f"{path}: body must contain the boundary sentence verbatim: {BOUNDARY!r}")
    elif "## System prompt" in body and BOUNDARY not in body.split("## System prompt", 1)[1]:
        err(f"{path}: the '## System prompt' section must contain the boundary sentence")
if writers:
    err(f"zero agents may write to a broker; {len(writers)} declare otherwise: {', '.join(writers)}")

# --- indexes ------------------------------------------------------------------------------------
setup = read("SETUP.md") if os.path.isfile("SETUP.md") else err("SETUP.md: missing") or ""
index = read("skills/README.md") if os.path.isfile("skills/README.md") else err("skills/README.md: missing") or ""
for name in sorted(skill_names):
    if f"`{name}`" not in setup:
        err(f"SETUP.md: does not name skill `{name}` in backticks")
    if f"[{name}]({name}/SKILL.md)" not in index:
        err(f"skills/README.md: does not link [{name}]({name}/SKILL.md)")
for name in sorted(agent_names):
    if f"agents/{name}.md" not in setup:
        err(f"SETUP.md: does not list agents/{name}.md")

# --- 3. links, emoji, script mentions -------------------------------------------------------------
markdown = tree((".md", ".mdc"))
for rel in markdown:
    text = read(rel)
    for target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = urllib.parse.unquote(target.split("#", 1)[0])
        if not target:
            continue
        resolved = os.path.normpath(target.lstrip("/") if target.startswith("/") else os.path.join(os.path.dirname(rel), target))
        if not os.path.exists(resolved):
            err(f"{rel}: broken link -> {target}")
    if EMOJI_RE.search(without_quoted_status(text)):
        err(f"{rel}: contains emoji")

# Every scripts/<name>.py named in an instruction file must exist: agents, skills, rules, docs,
# SETUP.md, README.md, CONTRIBUTING.md, AGENTS.md, GEMINI.md, CLAUDE.md and the routine
# manifest (non-comment lines). A mention on a line that says the tool "lands with" a later
# pull request or is "not yet" in the tree is a documented forward reference and is skipped;
# the phrase must sit on the same line as the mention.
FORWARD_REFERENCE_RE = re.compile(r"\blands with\b|\bnot yet\b")
mention_sources = [p for p in markdown if p.startswith(("agents/", "skills/", "rules/", "docs/"))
                   or p in ("SETUP.md", "README.md", "CONTRIBUTING.md", "AGENTS.md", "GEMINI.md", "CLAUDE.md")]
if os.path.isfile("routines/manifest.toml"):
    mention_sources.append("routines/manifest.toml")
for rel in mention_sources:
    lines = read(rel).splitlines()
    if rel.endswith(".toml"):
        # Only values a routine would run count; a TOML comment is documentation, not a chain.
        lines = [line for line in lines if not line.lstrip().startswith("#")]
    missing = set()
    for line in lines:
        if FORWARD_REFERENCE_RE.search(line):
            continue
        for script in SCRIPT_RE.findall(line):
            if not os.path.isfile(os.path.join("scripts", script)):
                missing.add(script)
    for script in sorted(missing):
        err(f"{rel}: names scripts/{script}, which does not exist")

# --- 4. version pins ---------------------------------------------------------------------------
version = None
try:
    with open("plugin.json", encoding="utf-8") as handle:
        version = json.load(handle).get("version")
except (OSError, ValueError) as exc:
    err(f"plugin.json: cannot read the version: {exc}")
if version is not None and not (isinstance(version, str) and PLAIN_VERSION_RE.fullmatch(version)):
    err(f"plugin.json: version {version!r} is not a plain X.Y.Z release version")
    version = None
pinned_files = tree((".md", ".mdc", ".json", ".toml"), extra_excluded=("CHANGELOG.md",))
if version is not None:
    expected = f"v{version}"
    for rel in pinned_files:
        for token in sorted(set(PIN_RE.findall(read(rel)))):
            if token != expected:
                err(f"{rel}: names release tag {token}, but plugin.json declares {expected}; "
                    "an instruction file may only name the release it ships in")

if errors:
    print("\n".join(errors))
    print(f"{len(errors)} problem(s)")
    sys.exit(1)
print(
    f"ok: structure - {len(agent_names)} agent(s), zero broker writers; {len(skill_names)} skill(s) "
    f"prefixed aegis-; {len(markdown)} markdown and {len(pinned_files) - len(markdown)} json/toml files "
    f"checked (links, emoji, script mentions, version pins v{version})"
)
PY

# ---------------------------------------------------------------------------------------------
# Step 5: Python 3.9 syntax floor for every module under scripts/, hooks/ and tests/.
python3 - <<'PY'
import ast
import os
import re
import sys

errors = []
notices = []
FUTURE = "from __future__ import annotations"
ROOTS = ("scripts", "hooks", "tests")
EXCLUDED_DIRS = {"__pycache__", ".git", "node_modules", ".venv"}
FIXTURES = "tests/fixtures"
NO_FUTURE_REQUIRED = ("scripts/aegis_desk/_vendor/",)
TYPING_310 = {
    "TypeAlias", "ParamSpec", "Concatenate", "TypeGuard", "Self", "Never", "LiteralString",
    "TypeVarTuple", "Unpack", "assert_never", "reveal_type", "override", "TypeIs", "ReadOnly",
    "assert_type", "dataclass_transform", "Required", "NotRequired",
}
TYPE_NAMES = {"int", "str", "float", "bytes", "bool", "dict", "list", "tuple", "set", "frozenset",
              "type", "object", "complex", "bytearray", "Decimal", "Path"}
PAREN_WITH_RE = re.compile(r"^\s*(?:async\s+)?with\s*\(\s*$")
Match = getattr(ast, "Match", None)


def files():
    found = []
    for top in ROOTS:
        if not os.path.isdir(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            rel_dir = os.path.relpath(dirpath, ".").replace(os.sep, "/")
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS
                                 and not f"{rel_dir}/{d}".startswith(FIXTURES))
            if rel_dir.startswith(FIXTURES):
                continue
            for name in sorted(filenames):
                if name.endswith(".py"):
                    found.append(f"{rel_dir}/{name}")
    return found


# Modules whose attributes are integer flags (re.MULTILINE | re.IGNORECASE, os.O_WRONLY | os.O_APPEND,
# fcntl.LOCK_EX | fcntl.LOCK_NB): a BitOr over them is valid Python 3.9, never a type union.
FLAG_ROOTS = {"re", "os", "fcntl", "stat", "signal", "select", "selectors", "socket", "ssl", "mmap",
              "enum", "errno", "termios", "resource", "zlib", "sys", "locale", "msvcrt", "ast", "inspect"}


def camel_case(name):
    """``Decimal``, ``Path``, ``MyClass``: an upper-case initial followed by a lower-case letter.
    ``LOCK_EX``, ``MULTILINE``, ``A`` and ``RED`` are constants or enum members, never types."""
    return name[:1].isupper() and any(c.islower() for c in name[1:])


def type_like(node):
    """True only for an operand that can be nothing but a type: ``None``, a builtin type name, a
    CamelCase name or attribute (``Decimal``, ``decimal.Decimal``), a ``typing``/``collections``
    attribute, or a subscript of those (``Dict[str, int]``). Attributes rooted at the flag
    modules and ALL_CAPS names are integer flags and never count. A BitOr is a union when at
    least one operand is type-like."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return type_like(node.left) or type_like(node.right)
    if isinstance(node, ast.Constant):
        return node.value is None
    if isinstance(node, ast.Name):
        return node.id in TYPE_NAMES or camel_case(node.id)
    if isinstance(node, ast.Attribute):
        dotted = qualname(node)
        if dotted is None:
            return False
        head = dotted.split(".")[0]
        if head in FLAG_ROOTS:
            return False
        return head in ("typing", "collections") or camel_case(node.attr)
    if isinstance(node, ast.Subscript):
        return type_like(node.value)
    return False


def union_message(rel, node, where):
    errors.append(f"{rel}:{node.lineno}: runtime 'X | Y' union in {where} needs Python 3.10; "
                  "use typing.Union/Optional (annotations are fine under the __future__ import)")


def qualname(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = qualname(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


checked = 0
for rel in files():
    with open(rel, encoding="utf-8") as handle:
        source = handle.read()
    try:
        tree = ast.parse(source, filename=rel, feature_version=(3, 9))
    except SyntaxError as exc:
        errors.append(f"{rel}:{exc.lineno}: not valid Python 3.9 syntax: {exc.msg}")
        continue
    except ValueError:
        if not notices:
            notices.append("notice: this interpreter cannot parse with feature_version=(3, 9); "
                           "syntax checked with the current grammar only")
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            errors.append(f"{rel}:{exc.lineno}: syntax error: {exc.msg}")
            continue
    checked += 1
    if os.path.basename(rel) != "__init__.py" and not rel.startswith(NO_FUTURE_REQUIRED):
        body = tree.body
        index = 1 if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                      and isinstance(body[0].value.value, str)) else 0
        first = body[index] if index < len(body) else None
        if not (isinstance(first, ast.ImportFrom) and first.module == "__future__"
                and any(alias.name == "annotations" for alias in first.names)):
            errors.append(f"{rel}: must begin with '{FUTURE}' (directly after the module docstring)")
    for node in ast.walk(tree):
        if Match is not None and isinstance(node, Match):
            errors.append(f"{rel}:{node.lineno}: 'match' statements need Python 3.10")
        if isinstance(node, ast.Attribute) and node.attr == "UTC" and (
            (isinstance(node.value, ast.Name) and node.value.id == "datetime")
            or (isinstance(node.value, ast.Attribute) and node.value.attr == "datetime")
        ):
            errors.append(f"{rel}:{node.lineno}: datetime.UTC needs Python 3.11; use timezone.utc")
        if isinstance(node, ast.ImportFrom) and node.module == "datetime" and any(a.name == "UTC" for a in node.names):
            errors.append(f"{rel}:{node.lineno}: 'from datetime import UTC' needs Python 3.11; use timezone.utc")
        if isinstance(node, ast.Import) and any(a.name == "tomllib" or a.name.startswith("tomllib.") for a in node.names):
            errors.append(f"{rel}:{node.lineno}: tomllib needs Python 3.11; use the vendored TOML reader")
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "tomllib":
            errors.append(f"{rel}:{node.lineno}: tomllib needs Python 3.11; use the vendored TOML reader")
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name in TYPING_310:
                    errors.append(f"{rel}:{node.lineno}: typing.{alias.name} needs Python 3.10 or later")
        if isinstance(node, ast.Attribute) and node.attr == "pairwise" and qualname(node.value) == "itertools":
            errors.append(f"{rel}:{node.lineno}: itertools.pairwise needs Python 3.10")
        if isinstance(node, ast.Call):
            name = qualname(node.func)
            if name == "zip" and any(k.arg == "strict" for k in node.keywords):
                errors.append(f"{rel}:{node.lineno}: zip(strict=...) needs Python 3.10")
            if name in ("isinstance", "issubclass") and len(node.args) >= 2 and isinstance(node.args[1], ast.BinOp) and isinstance(node.args[1].op, ast.BitOr):
                union_message(rel, node, f"{name}()")
            if name is not None and name.endswith("get_type_hints"):
                errors.append(f"{rel}:{node.lineno}: get_type_hints() evaluates string annotations at "
                              "runtime; 'X | Y' annotations would fail on Python 3.9")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
            for default in defaults:
                if isinstance(default, ast.BinOp) and isinstance(default.op, ast.BitOr) and type_like(default):
                    union_message(rel, default, "a default value")
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.BinOp) and isinstance(statement.value.op, ast.BitOr) and type_like(statement.value):
            union_message(rel, statement, "a module-level alias")
    for number, line in enumerate(source.splitlines(), 1):
        if PAREN_WITH_RE.match(line):
            errors.append(f"{rel}:{number}: parenthesized multi-line 'with' needs Python 3.10")

for line in notices:
    print(line)
if errors:
    print("\n".join(errors))
    print(f"{len(errors)} problem(s)")
    sys.exit(1)
print(f"ok: python floor - {checked} module(s) parse as Python 3.9 and begin with '{FUTURE}'")
PY

# ---------------------------------------------------------------------------------------------
# Step 6: the distribution contract, the no-write gate and the Grok Bot template contract.
python3 "$HERE/check_manifests.py" "$ROOT"
python3 "$HERE/check_no_write_paths.py" "$ROOT"
python3 "$HERE/check_grok_template.py" "$ROOT"

# ---------------------------------------------------------------------------------------------
# Step 7: the unit test suite and the recorded test-count floor (the floor only moves up).
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if ! python3 -m unittest discover -s tests -t . -p 'test_*.py' >"$log" 2>&1; then
  cat "$log"
  if grep -q "NO TESTS RAN" "$log"; then
    echo "error: no tests were discovered under tests/ (python3 -m unittest discover -s tests -t . -p 'test_*.py')"
  else
    echo "error: the unit test suite failed (python3 -m unittest discover -s tests -t . -p 'test_*.py')"
  fi
  exit 1
fi
python3 - "$log" <<'PY'
import re
import sys

FLOOR = "scripts/test_count_floor.txt"
with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
    log = handle.read()
try:
    with open(FLOOR, encoding="utf-8") as handle:
        floor = int(handle.read().strip())
    if floor < 0:
        raise ValueError("negative")
except (OSError, ValueError) as exc:
    print(f"error: {FLOOR} must contain one non-negative integer: {exc}")
    sys.exit(1)
found = re.findall(r"^Ran (\d+) tests?\b", log, re.M)
if not found:
    print(log)
    print("error: could not find 'Ran N tests' in the unittest output")
    sys.exit(1)
ran = int(found[-1])
if ran == 0:
    print(log)
    print("error: no tests were discovered under tests/ (python3 -m unittest discover -s tests -t . -p 'test_*.py'); "
          "an empty suite never passes the gate, whatever the floor says")
    sys.exit(1)
if ran < floor:
    print(log)
    print(f"error: {ran} test(s) ran but {FLOOR} requires at least {floor}; tests were removed or not discovered")
    sys.exit(1)
skipped = re.search(r"skipped=(\d+)", log)
detail = f", {skipped.group(1)} skipped" if skipped else ""
print(f"ok: unit tests - {ran} ran (floor {floor}{detail})")
PY

# ---------------------------------------------------------------------------------------------
# Step 8: command journeys as subprocesses in a scrubbed environment.
python3 "$HERE/cli_journeys.py" --root "$ROOT"

# ---------------------------------------------------------------------------------------------
# Step 9: docs/TOOLS.md is generated from the tools' --schema output; fail on drift once the
# generator exists in the tree under test.
if [ -f scripts/gen_tools_doc.py ]; then
  python3 scripts/gen_tools_doc.py --check
else
  echo "skip: scripts/gen_tools_doc.py not present; docs/TOOLS.md drift check not run"
fi

# ---------------------------------------------------------------------------------------------
# Step 10: the aegis-role-* skills and their index blocks are generated from agents/*.md; fail on
# drift once the generator exists in the tree under test.
if [ -f scripts/gen_role_skills.py ]; then
  python3 scripts/gen_role_skills.py --check
else
  echo "skip: scripts/gen_role_skills.py not present; role-skill drift check not run"
fi

echo "ok: all repository gates passed for $ROOT"
