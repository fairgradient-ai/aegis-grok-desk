"""``scripts/gen_tools_doc.py``: ``docs/TOOLS.md`` generated from every desk tool's ``--schema`` answer and parser.

The generator imports the 23 tools by literal import statements (never a computed import), asks each for its
``--schema`` in-process, reads each parser's flags from ``build_parser(release)._actions`` (never ``format_help()``,
whose wrapping depends on the terminal) and renders one Markdown file: the generated-by line, the shared envelope
and exit-code table once, then one ``## <tool>`` section per tool, sorted by name, with the script path, tool
version, description, network access, a flags table (a pipe inside a cell written as ``\\|``), one line per
top-level payload key (``json.dumps(value, sort_keys=True)``) and desk_init's ``input`` key. ``--check`` renders in
memory and fails with a bounded unified diff when the committed file differs or is missing, and also when a
``scripts/*.py`` file defines ``build_parser`` and ``main`` but is not in ``TOOL_MODULES``. The tests patch the
module's path constants (``DOC_PATH``, ``SCRIPTS_DIR``) and never write into the repository."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import contextlib
import io
import json
import os
import re
import tempfile
import types
import unittest
from typing import List, Optional, Sequence, Tuple
from unittest import mock

from aegis_desk import contract

import check_no_write_paths
import gen_tools_doc as gen

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
DOC = os.path.join(ROOT, "docs", "TOOLS.md")
EXPECTED_TOOLS = (
    "ack", "alert_lint", "assignment_watch", "book", "book_check", "calendar_check", "csv_recon", "desk_doctor",
    "desk_init", "eod_summary", "exit_check", "expiry_checklist", "heartbeat", "market_clock", "opening_bell",
    "positions_check", "quote_cache", "quote_qc", "routine", "routine_audit", "rules_adopt", "rules_diff",
    "trigger_levels",
)
# check.sh step 3 and step 4, as regular expressions over the rendered text.
SCRIPT_RE = re.compile(r"\bscripts/((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_]+\.py)\b")
PIN_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")


def run_main(argv: Sequence[str]) -> Tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gen.main(list(argv))
    return code, out.getvalue() + err.getvalue()


def section(text: str, tool: str) -> str:
    """The ``## <tool>`` section of a rendered document, up to the next ``## `` heading."""
    start = text.index("\n## %s\n" % tool)
    end = text.find("\n## ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def tools_on_disk() -> List[str]:
    found = []
    for name in sorted(os.listdir(SCRIPTS)):
        if name.endswith(".py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as handle:
                source = handle.read()
            if "def build_parser(" in source and "def main(" in source:
                found.append(name[:-3])
    return found


class RenderCache:
    text: Optional[str] = None

    @classmethod
    def get(cls) -> str:
        if cls.text is None:
            cls.text = gen.render()
        return cls.text


class ToolListTest(unittest.TestCase):
    def test_the_tool_list_is_every_script_with_a_parser_and_a_main(self) -> None:
        names = tuple(module.__name__ for module in gen.TOOL_MODULES)
        self.assertEqual(EXPECTED_TOOLS, names, "TOOL_MODULES is sorted by name")
        self.assertEqual(list(EXPECTED_TOOLS), tools_on_disk())
        self.assertEqual([], gen.unlisted_tools())

    def test_the_tools_come_from_literal_imports_only(self) -> None:
        with open(os.path.join(SCRIPTS, "gen_tools_doc.py"), encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        for name in EXPECTED_TOOLS:
            self.assertIn(name, imported)
        self.assertNotIn("importlib", source)
        self.assertNotIn("__import__", source)
        self.assertNotIn("format_help", source)
        self.assertNotIn("def build_parser(", source, "the generator is not a tool: --check would list it")

    def test_an_unlisted_tool_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-gen-tools-") as scripts:
            with open(os.path.join(scripts, "new_tool.py"), "w", encoding="utf-8") as handle:
                handle.write('"""A new tool."""\nfrom __future__ import annotations\n\n\ndef build_parser(version):\n'
                             '    return None\n\n\ndef main(argv=None):\n    return 0\n')
            with open(os.path.join(scripts, "helper.py"), "w", encoding="utf-8") as handle:
                handle.write('"""Not a tool: no parser."""\nfrom __future__ import annotations\n\n\ndef main(argv=None):\n'
                             '    return 0\n')
            # the scan reads the module's own top-level functions by AST, so neither spacing nor a docstring fools it
            with open(os.path.join(scripts, "spaced_tool.py"), "w", encoding="utf-8") as handle:
                handle.write('"""Another new tool."""\nfrom __future__ import annotations\n\n\ndef build_parser (v):\n'
                             '    return None\n\n\ndef main (argv=None):\n    return 0\n')
            with open(os.path.join(scripts, "notes.py"), "w", encoding="utf-8") as handle:
                handle.write('"""Not a tool: def build' + '_parser(v) and def main(argv) only in text."""\n'
                             'from __future__ import annotations\n\n\ndef helper():\n'
                             '    def build_parser(v):\n        return None\n    return build_parser\n')
            with mock.patch.object(gen, "SCRIPTS_DIR", scripts):
                self.assertEqual(["scripts/new_tool.py", "scripts/spaced_tool.py"], gen.unlisted_tools())
                code, output = run_main(["--check"])
        self.assertEqual(1, code, output)
        self.assertIn("error: scripts/spaced_tool.py defines build_parser() and main()", output)
        self.assertNotIn("notes.py", output)
        self.assertIn("error: scripts/new_tool.py defines build_parser() and main() but is missing from TOOL_MODULES",
                      output)
        self.assertNotIn("helper.py", output)


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = RenderCache.get()

    def test_layout_header_shared_tables_and_one_section_per_tool(self) -> None:
        lines = self.text.split("\n")
        self.assertEqual(gen.TITLE, lines[0])
        self.assertEqual("", lines[1])
        self.assertEqual(gen.HEADER, lines[2])
        self.assertEqual("Generated by scripts/gen_tools_doc.py from each tool's --schema; do not edit by hand.", gen.HEADER)
        self.assertTrue(self.text.endswith("\n") and not self.text.endswith("\n\n"))
        self.assertEqual(1, self.text.count("| Exit code | Meaning |"))
        for code, meaning in contract.EXIT_CODES.items():
            self.assertEqual(1, self.text.count("| %d | %s |" % (code, meaning)), code)
        headings = [line[3:] for line in lines if line.startswith("## ")]
        self.assertEqual(list(EXPECTED_TOOLS), headings)
        self.assertEqual(sorted(headings), headings)

    def test_each_section_names_its_script_version_description_and_network_access(self) -> None:
        for module in gen.TOOL_MODULES:
            name = module.__name__
            with self.subTest(tool=name):
                schema = gen.tool_schema(module)
                body = section(self.text, name)
                self.assertIn("- script: `scripts/%s.py`\n" % name, body)
                self.assertIn("- tool version: %s\n" % schema["tool_version"], body)
                self.assertIn("- mode: %s\n" % schema["mode"], body)
                self.assertIn("- description: %s\n" % gen.fold(schema["description"]), body)
                self.assertIn("- network access: %s\n" % gen.fold(schema["network_access"]), body)
                self.assertIn("| Flag | Value | Help |", body)

    def test_every_payload_key_is_one_line_of_sorted_json(self) -> None:
        for module in gen.TOOL_MODULES:
            name = module.__name__
            payload = gen.tool_schema(module)["envelope"]["payload"]
            body = section(self.text, name)
            for key, value in payload.items():
                with self.subTest(tool=name, key=key):
                    self.assertIn("\n- `%s`: `%s`\n" % (key, json.dumps(value, sort_keys=True)), body)

    def test_flags_come_from_the_parser_with_pipes_escaped(self) -> None:
        book_check = section(self.text, "book_check")
        self.assertIn("| `--next-run-utc` | `ISO-8601-UTC` | the routine's next run: after the session's close, one "
                      "observation is enough |", book_check)
        self.assertIn("| `--post` | none | record the run: rule state, signal and alert journals, desk state, heartbeat |",
                      book_check)
        self.assertIn("| `-h`, `--help` | none | show this help message and exit |", book_check)
        ack = section(self.text, "ack")
        self.assertIn("| `--until` | `ISO-8601-UTC\\|HH:MM ET` |", ack)
        self.assertIn("| `action` (positional) | `list \\| show \\| ack \\| snooze \\| acted \\| config \\| connectors` "
                      "(optional) | list \\| show \\| ack \\| snooze \\| acted \\| config \\| connectors (required except "
                      "with --schema) |", ack)
        quote_cache = section(self.text, "quote_cache")
        self.assertIn("| `symbols` (positional) | `SYMBOL` (zero or more) |", quote_cache)
        heartbeat = section(self.text, "heartbeat")
        self.assertIn("| `--status` | `ok \\| error \\| usage \\| fired \\| unavailable` |", heartbeat)
        for line in self.text.split("\n"):
            if line.startswith("| "):
                self.assertTrue(line.endswith(" |"), line)
                self.assertNotRegex(line.replace("\\|", ""), r"\| \|", "an empty cell: %s" % line)

    def test_desk_init_carries_its_input_key_and_no_other_tool_does(self) -> None:
        schema = gen.tool_schema(gen.desk_init)
        body = section(self.text, "desk_init")
        self.assertIn("Input (`--schema` key `input`):", body)
        for key, value in schema["input"].items():
            self.assertIn("\n- `%s`: `%s`\n" % (key, json.dumps(value, sort_keys=True)), body)
        self.assertEqual(1, self.text.count("Input (`--schema` key `input`):"))

    def test_the_output_is_deterministic_and_independent_of_the_terminal(self) -> None:
        for columns in ("40", "300"):
            with mock.patch.dict(os.environ, {"COLUMNS": columns}):
                self.assertEqual(self.text, gen.render())

    def test_the_rendered_text_passes_the_repository_gates(self) -> None:
        for number, line in enumerate(self.text.split("\n"), 1):
            for rule, pattern, _message in check_no_write_paths.PROSE_RULES:
                self.assertIsNone(pattern.search(line), (rule, number, line))
            for script in SCRIPT_RE.findall(line):
                self.assertTrue(os.path.isfile(os.path.join(SCRIPTS, script)), (number, script))
            for token in PIN_RE.findall(line):
                self.assertEqual("v0.1.0", token, (number, line))
            self.assertNotIn("](", line, "no markdown link to resolve")
        self.assertTrue(all(ord(ch) < 0x2300 for ch in self.text), "no emoji or pictograph")

    def test_cell_escapes_pipes_and_folds_newlines(self) -> None:
        self.assertEqual("a \\| b c", gen.cell("a | b\nc"))
        self.assertEqual("a | b c", gen.fold("a | b\n c"))
        self.assertEqual("plain", gen.cell("plain"))

    def test_a_tool_whose_schema_fails_is_an_error(self) -> None:
        def main(argv: Sequence[str]) -> int:
            return 1

        broken = types.ModuleType("broken")
        broken.main = main  # type: ignore[attr-defined]
        with self.assertRaises(gen.GenError) as caught:
            gen.tool_schema(broken)
        self.assertIn("broken", str(caught.exception))


class CommittedDocTest(unittest.TestCase):
    def test_the_committed_doc_is_the_rendered_doc(self) -> None:
        with open(DOC, encoding="utf-8") as handle:
            self.assertEqual(RenderCache.get(), handle.read(), "run python3 scripts/gen_tools_doc.py")

    def test_check_passes_on_the_committed_doc(self) -> None:
        code, output = run_main(["--check"])
        self.assertEqual(0, code, output)
        self.assertEqual("ok: docs/TOOLS.md matches the --schema of 23 tools\n", output)


class CheckAndWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-gen-tools-doc-")
        self.doc = os.path.join(self._tmp.name, "docs", "TOOLS.md")
        os.makedirs(os.path.dirname(self.doc))
        patcher = mock.patch.object(gen, "DOC_PATH", self.doc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_check_fails_on_drift_with_a_bounded_diff(self) -> None:
        text = RenderCache.get().replace("- tool version: 0.1.0", "- tool version: 0.0.9", 1) + "stray\n" * 100
        with open(self.doc, "w", encoding="utf-8") as handle:
            handle.write(text)
        code, output = run_main(["--check"])
        self.assertEqual(1, code, output)
        lines = output.rstrip("\n").split("\n")
        self.assertEqual("error: docs/TOOLS.md is out of date; run python3 scripts/gen_tools_doc.py", lines[0])
        self.assertEqual(gen.DIFF_LINES, len(lines) - 1, "the first 40 lines of the unified diff")
        self.assertEqual(40, gen.DIFF_LINES)
        self.assertTrue(lines[1].startswith("--- docs/TOOLS.md"), lines[1])
        self.assertTrue(lines[2].startswith("+++ docs/TOOLS.md (rendered)"), lines[2])
        self.assertIn("-- tool version: 0.0.9", output)
        self.assertIn("+- tool version: 0.1.0", output)

    def test_check_fails_when_the_doc_is_missing(self) -> None:
        code, output = run_main(["--check"])
        self.assertEqual(1, code, output)
        self.assertEqual("error: docs/TOOLS.md is missing; run python3 scripts/gen_tools_doc.py\n", output)

    def test_write_mode_writes_the_rendered_doc_then_check_passes(self) -> None:
        code, output = run_main([])
        self.assertEqual(0, code, output)
        self.assertEqual("wrote docs/TOOLS.md (23 tools)\n", output)
        with open(self.doc, encoding="utf-8") as handle:
            self.assertEqual(RenderCache.get(), handle.read())
        self.assertEqual(0, run_main(["--check"])[0])

    def test_usage_errors_exit_2(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            run_main(["--nope"])
        self.assertEqual(2, caught.exception.code)
        self.assertFalse(os.path.exists(self.doc), "a usage error writes nothing")


if __name__ == "__main__":
    unittest.main()
