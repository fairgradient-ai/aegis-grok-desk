"""Negative and positive fixtures for scripts/check_manifests.py and scripts/check_grok_template.py.

Each case copies the good fixture tree, breaks the contract in exactly one way and asserts the
checker fails naming that file and that defect. A guard nobody has watched fail is not a guard.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from typing import Any, Callable, List

# isort: off
import tests._path  # noqa: F401
import check_grok_template
import check_manifests
from aegis_desk import routines
from tests.gates._trees import fixture_tree, read, write
# isort: on


def edit_json(root: str, rel: str, mutate: Callable[[Any], None]) -> None:
    path = os.path.join(root, rel)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    mutate(data)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def joined(errors: List[str]) -> str:
    return "\n".join(errors)


class ManifestContractTests(unittest.TestCase):
    def assert_fails(self, result: dict, *needles: str) -> None:
        self.assertFalse(result["ok"], joined(result["errors"]))
        text = joined(result["errors"])
        for needle in needles:
            self.assertIn(needle, text)

    def test_good_tree_passes(self) -> None:
        with fixture_tree() as root:
            result = check_manifests.run(root)
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertEqual("0.1.0", result["version"])
        self.assertEqual(8, result["checked"]["manifests"])

    def test_prerelease_version_is_rejected(self) -> None:
        with fixture_tree("prerelease_version") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, "plugin.json", "0.1.0-dev", "not a plain X.Y.Z release version")
        self.assertIsNone(result["version"])

    def test_hooks_key_in_grok_manifest_is_rejected(self) -> None:
        with fixture_tree("hooks_key") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, ".grok-plugin/plugin.json", "must not declare 'hooks'")

    def test_hooks_key_in_root_and_claude_manifests_is_rejected(self) -> None:
        for rel in ("plugin.json", ".claude-plugin/plugin.json"):
            with fixture_tree() as root:
                edit_json(root, rel, lambda d: d.update(hooks="./hooks/hooks.json"))
                result = check_manifests.run(root)
            self.assert_fails(result, rel, "must not declare 'hooks'")

    def test_paragraph_drift_in_agents_md_is_rejected(self) -> None:
        with fixture_tree("paragraph_drift") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, "AGENTS.md", "verbatim")

    def test_paragraph_drift_in_gemini_md_is_rejected(self) -> None:
        with fixture_tree() as root:
            write(root, "GEMINI.md", "# Gemini\n\nThis desk never places orders.\n")
            result = check_manifests.run(root)
        self.assert_fails(result, "GEMINI.md", "verbatim")

    def test_version_mismatch_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".claude-plugin/plugin.json", lambda d: d.update(version="9.9.9"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".claude-plugin/plugin.json", "9.9.9", "does not match")

    def test_nested_marketplace_versions_are_checked(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".claude-plugin/marketplace.json", lambda d: d["metadata"].update(version="9.9.9"))
            edit_json(root, ".grok-plugin/marketplace.json", lambda d: d["plugins"][0].update(version="8.8.8"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".claude-plugin/marketplace.json: version '9.9.9'",
                          ".grok-plugin/marketplace.json: plugins[0].version '8.8.8'")

    def test_stale_marketplace_refs_are_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".grok-plugin/marketplace.json", lambda d: d["plugins"][0]["source"].update(ref="v0.0.9"))
            edit_json(root, ".agents/plugins/marketplace.json", lambda d: d["plugins"][0]["source"].update(ref="main"))
            edit_json(root, ".claude-plugin/marketplace.json", lambda d: d["plugins"][0]["source"].pop("ref"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".grok-plugin/marketplace.json: plugins[0].source.ref must pin v0.1.0",
                          ".agents/plugins/marketplace.json: plugins[0].source.ref must pin v0.1.0",
                          ".claude-plugin/marketplace.json: plugins[0].source.ref must pin v0.1.0")

    def test_marketplace_root_source_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".grok-plugin/marketplace.json",
                      lambda d: d["plugins"][0].update(source={"type": "local", "path": "./"}))
            result = check_manifests.run(root)
        self.assert_fails(result, ".grok-plugin/marketplace.json", "pinned remote URL form")

    def test_wrong_repository_url_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".agents/plugins/marketplace.json",
                      lambda d: d["plugins"][0]["source"].update(url="https://github.com/other/repo.git"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".agents/plugins/marketplace.json", "source.url must be")

    def test_wrong_name_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".grok-plugin/marketplace.json", lambda d: d["plugins"][0].update(name="aegis"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".grok-plugin/marketplace.json: plugins[0].name 'aegis'")

    def test_codex_entry_needs_policy_and_category(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".agents/plugins/marketplace.json",
                      lambda d: (d["plugins"][0].pop("policy"), d["plugins"][0].pop("category")))
            result = check_manifests.run(root)
        self.assert_fails(result, "policy is required", "category is required")

    def test_component_paths_in_grok_manifest_are_rejected(self) -> None:
        with fixture_tree("grok_skills_key") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, ".grok-plugin/plugin.json: must not declare 'skills'",
                          ".grok-plugin/plugin.json: must not declare 'agents'",
                          "auto-discover skills/ and agents/; declaring them double-loads or shadows")

    def test_component_paths_in_root_and_claude_manifests_are_rejected(self) -> None:
        for rel, key in (("plugin.json", "skills"), (".claude-plugin/plugin.json", "agents"),
                         ("plugin.json", "commands")):
            with fixture_tree() as root:
                edit_json(root, rel, lambda d, key=key: d.update({key: f"./{key}/"}))
                result = check_manifests.run(root)
            self.assert_fails(result, f"{rel}: must not declare '{key}'")

    def test_cursor_manifest_must_declare_component_paths(self) -> None:
        for key in ("skills", "agents", "rules"):
            with fixture_tree() as root:
                edit_json(root, ".cursor-plugin/plugin.json", lambda d, key=key: d.pop(key))
                result = check_manifests.run(root)
            self.assert_fails(result, f".cursor-plugin/plugin.json: must declare '{key}'")

    def test_cursor_hooks_declaration_is_required(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".cursor-plugin/plugin.json", lambda d: d.pop("hooks"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".cursor-plugin/plugin.json: hooks must be './hooks/cursor-hooks.json'")

    def test_cursor_hooks_file_needs_version_one(self) -> None:
        with fixture_tree() as root:
            write(root, "hooks/cursor-hooks.json", '{"version": 2, "hooks": {}}\n')
            result = check_manifests.run(root)
        self.assert_fails(result, 'hooks/cursor-hooks.json: must declare "version": 1')

    def test_hooks_json_needs_hooks_object(self) -> None:
        with fixture_tree() as root:
            write(root, "hooks/hooks.json", '{"hooks": []}\n')
            result = check_manifests.run(root)
        self.assert_fails(result, "hooks/hooks.json", "top-level 'hooks' object")

    def test_missing_component_path_is_rejected(self) -> None:
        with fixture_tree() as root:
            shutil.rmtree(os.path.join(root, "rules"))
            result = check_manifests.run(root)
        self.assert_fails(result, ".cursor-plugin/plugin.json: rules './rules/' does not exist")

    def test_path_escaping_repository_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".cursor-plugin/plugin.json", lambda d: d.update(skills="../skills/"))
            result = check_manifests.run(root)
        self.assert_fails(result, "resolves outside the repository")

    def test_invalid_json_is_reported_with_position(self) -> None:
        with fixture_tree() as root:
            write(root, ".claude-plugin/marketplace.json", '{"name": "aegis-desk",\n')
            result = check_manifests.run(root)
        self.assert_fails(result, ".claude-plugin/marketplace.json: invalid JSON at line")

    def test_missing_manifest_is_reported(self) -> None:
        with fixture_tree() as root:
            os.remove(os.path.join(root, "gemini-extension.json"))
            result = check_manifests.run(root)
        self.assert_fails(result, "gemini-extension.json: manifest is missing")

    def test_template_release_must_match_version(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d["source"].update(release="0.2.0"))
            result = check_manifests.run(root)
        self.assert_fails(result, "template/grok-bot.json: source.release '0.2.0'")

    def test_routines_manifest_needs_schema_version(self) -> None:
        with fixture_tree() as root:
            write(root, "routines/manifest.toml", "# nothing declared\n")
            result = check_manifests.run(root)
        self.assert_fails(result, "routines/manifest.toml", "schema_version")

    def test_desk_rule_without_boundary_paragraph_is_rejected(self) -> None:
        with fixture_tree() as root:
            write(root, "rules/aegis-desk.mdc", "---\nalwaysApply: true\n---\n\n# Rule\n\nOnly routing here.\n")
            result = check_manifests.run(root)
        self.assert_fails(result, "rules/aegis-desk.mdc: no paragraph starts 'This desk never places'")

    def test_cli_json_mode(self) -> None:
        with fixture_tree() as root:
            proc = subprocess.run([sys.executable, check_manifests.__file__, root, "--json"],
                                  capture_output=True, text=True, check=False)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("0.1.0", payload["version"])


GATE_COMMAND = 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/broker_write_gate.py"'
CURSOR_GATE_COMMAND = 'python3 "${CURSOR_PLUGIN_ROOT}/hooks/broker_write_gate.py" --runtime cursor'


class HookWiringTests(unittest.TestCase):
    """The gate's registration (decision R-8): hooks/hooks.json for Claude Code and Grok Build, hooks/cursor-hooks.json
    for Cursor, and the gate script both run. Each bad fixture breaks exactly one rule."""

    def assert_fails(self, result: dict, *needles: str) -> None:
        self.assertFalse(result["ok"], joined(result["errors"]))
        text = joined(result["errors"])
        for needle in needles:
            self.assertIn(needle, text)

    def run_case(self, case: str) -> dict:
        with fixture_tree(case) as root:
            return check_manifests.run(root)

    def test_good_tree_wires_the_gate(self) -> None:
        with fixture_tree() as root:
            result = check_manifests.run(root)
            self.assertTrue(result["ok"], joined(result["errors"]))
            self.assertEqual("hooks/broker_write_gate.py", result["checked"]["hook_gate"])
            hooks = json.loads(read(root, "hooks/hooks.json"))
        self.assertEqual(["Bash|PowerShell|Monitor|run_terminal_command", ".+__.+"],
                         [group["matcher"] for group in hooks["hooks"]["PreToolUse"]])

    def test_the_repository_wires_the_gate(self) -> None:
        result = check_manifests.run(tests._path._ROOT)
        self.assertEqual([], [error for error in result["errors"] if "hooks/" in error])

    def test_an_empty_hook_set_is_rejected(self) -> None:
        self.assert_fails(self.run_case("hooks_json_empty"),
                          "hooks/hooks.json: must register the gate under PreToolUse")

    def test_cursor_events_in_hooks_json_are_rejected(self) -> None:
        self.assert_fails(self.run_case("hooks_json_cursor_event"),
                          "hooks/hooks.json: event 'beforeShellExecution' is not allowed; only PreToolUse")

    def test_the_two_matchers_are_exact(self) -> None:
        self.assert_fails(self.run_case("hooks_json_wrong_matcher"),
                          "hooks/hooks.json: PreToolUse must hold exactly two groups",
                          "'Bash|PowerShell|Monitor|run_terminal_command' and '.+__.+'", "found ['Bash', '.+__.+']")

    def test_exec_form_is_rejected(self) -> None:
        self.assert_fails(self.run_case("hooks_json_exec_form"),
                          "hooks/hooks.json: PreToolUse[0].hooks[0] must not declare 'args'",
                          "hooks/hooks.json: PreToolUse[0].hooks[0].command must be exactly %r" % GATE_COMMAND)

    def test_an_unquoted_plugin_root_is_rejected(self) -> None:
        self.assert_fails(self.run_case("hooks_json_unquoted_root"),
                          "hooks/hooks.json: PreToolUse[1].hooks[0].command must be exactly %r" % GATE_COMMAND,
                          "shell form, plugin root quoted")

    def test_the_gate_script_must_exist(self) -> None:
        self.assert_fails(self.run_case("hooks_gate_missing"),
                          "hooks/broker_write_gate.py: missing; hooks/hooks.json and hooks/cursor-hooks.json run it")

    def test_cursor_entries_fail_closed(self) -> None:
        self.assert_fails(self.run_case("cursor_hooks_not_fail_closed"),
                          "hooks/cursor-hooks.json: beforeMCPExecution[0].failClosed must be true")

    def test_cursor_entries_run_the_cursor_runtime(self) -> None:
        self.assert_fails(self.run_case("cursor_hooks_without_runtime"),
                          "hooks/cursor-hooks.json: beforeShellExecution[0].command must be exactly %r"
                          % CURSOR_GATE_COMMAND, "--runtime cursor")

    def test_unknown_cursor_events_are_rejected(self) -> None:
        self.assert_fails(self.run_case("cursor_hooks_unknown_event"),
                          "hooks/cursor-hooks.json: event 'preToolUse' is not allowed (only beforeShellExecution and "
                          "beforeMCPExecution)")

    def test_cursor_entries_are_flat(self) -> None:
        self.assert_fails(self.run_case("cursor_hooks_nested"),
                          "hooks/cursor-hooks.json: beforeShellExecution[0] must be a flat entry (no nested 'hooks')")

    def test_cursor_covers_shell_and_mcp(self) -> None:
        self.assert_fails(self.run_case("cursor_hooks_missing_mcp"),
                          "hooks/cursor-hooks.json: must register the gate for beforeMCPExecution")

    def test_handler_type_timeout_and_counts(self) -> None:
        def mutate(change: Callable[[Any], None]) -> dict:
            with fixture_tree() as root:
                edit_json(root, "hooks/hooks.json", change)
                return check_manifests.run(root)

        self.assert_fails(mutate(lambda d: d["hooks"]["PreToolUse"][0]["hooks"][0].update(type="http")),
                          "hooks/hooks.json: PreToolUse[0].hooks[0].type must be 'command'")
        self.assert_fails(mutate(lambda d: d["hooks"]["PreToolUse"][1]["hooks"][0].update(timeout="10")),
                          "hooks/hooks.json: PreToolUse[1].hooks[0].timeout must be a positive whole number of seconds")
        self.assert_fails(mutate(lambda d: d["hooks"]["PreToolUse"][0]["hooks"].append(
            {"type": "command", "command": GATE_COMMAND})),
            "hooks/hooks.json: PreToolUse[0] must hold exactly one handler (found 2)")
        self.assert_fails(mutate(lambda d: d["hooks"]["PreToolUse"].append(dict(d["hooks"]["PreToolUse"][1]))),
                          "hooks/hooks.json: PreToolUse must hold exactly two groups", "3 group(s)")
        self.assert_fails(mutate(lambda d: d["hooks"].update(PreToolUse={"matcher": ".+__.+"})),
                          "hooks/hooks.json: must register the gate under PreToolUse")
        with fixture_tree() as root:
            edit_json(root, "hooks/cursor-hooks.json",
                      lambda d: d["hooks"]["beforeShellExecution"][0].update(timeout=0))
            result = check_manifests.run(root)
        self.assert_fails(result, "hooks/cursor-hooks.json: beforeShellExecution[0].timeout must be a positive whole "
                                  "number of seconds")

    def test_a_cursor_matcher_is_rejected(self) -> None:
        self.assert_fails(self.run_case("cursor_hooks_matcher"),
                          "hooks/cursor-hooks.json: beforeShellExecution[0] has key 'matcher', which is not allowed "
                          "(only command, timeout and failClosed)")

    def test_an_unknown_handler_key_is_rejected(self) -> None:
        self.assert_fails(self.run_case("hooks_json_async"),
                          "hooks/hooks.json: PreToolUse[1].hooks[0] has key 'async', which is not allowed (only type, "
                          "command and timeout)")

    def test_an_unknown_group_key_is_rejected(self) -> None:
        self.assert_fails(self.run_case("hooks_json_group_key"),
                          "hooks/hooks.json: PreToolUse[0] has key 'enabled', which is not allowed (only matcher and "
                          "hooks)")

    def test_unknown_top_level_keys_are_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "hooks/hooks.json", lambda d: d.update(disableAllHooks=True))
            edit_json(root, "hooks/cursor-hooks.json", lambda d: d.update(enabled=False))
            result = check_manifests.run(root)
        self.assert_fails(result, "hooks/hooks.json: top-level key 'disableAllHooks' is not allowed (only description "
                                  "and hooks)",
                          "hooks/cursor-hooks.json: top-level key 'enabled' is not allowed (only version and hooks)")

    def test_exec_form_args_is_reported_once(self) -> None:
        errors = self.run_case("hooks_json_exec_form")["errors"]
        self.assertEqual(1, sum("'args'" in error for error in errors), errors)

    def test_the_landed_files_match_the_exact_wiring(self) -> None:
        with open(os.path.join(tests._path._ROOT, "hooks", "cursor-hooks.json"), encoding="utf-8") as handle:
            cursor = json.load(handle)
        self.assertEqual(CURSOR_GATE_COMMAND, check_manifests.CURSOR_GATE_COMMAND)
        self.assertEqual(GATE_COMMAND, check_manifests.GATE_COMMAND)
        for event in check_manifests.CURSOR_GATE_EVENTS:
            self.assertEqual(CURSOR_GATE_COMMAND, cursor["hooks"][event][0]["command"])


COUNT_RE = re.compile(r"(?i)\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|eighteen|"
                      r"twenty(?:[- ][a-z]+)?)\s+(?:roles?|skills?|agents?|seats?|bots?)\b")


def landed(rel: str) -> Any:
    with open(os.path.join(tests._path._ROOT, rel), encoding="utf-8") as handle:
        return json.load(handle)


class LandedManifestTests(unittest.TestCase):
    """The repository's own manifests: the Cursor rule path (PR 3 decision P14) and descriptions without the counts
    nothing checks (decision R-11); names, versions, refs and paths are the contract tests' business above."""

    def test_cursor_reads_the_desk_rule_file(self) -> None:
        cursor = landed(".cursor-plugin/plugin.json")
        self.assertEqual("./rules/aegis-desk.mdc", cursor["rules"])
        self.assertTrue(os.path.isfile(os.path.join(tests._path._ROOT, "rules", "aegis-desk.mdc")))

    def test_a_rule_file_path_is_checked_like_a_directory(self) -> None:
        with fixture_tree() as root:
            edit_json(root, ".cursor-plugin/plugin.json", lambda d: d.update(rules="./rules/aegis-desk.mdc"))
            self.assertTrue(check_manifests.run(root)["ok"])
            edit_json(root, ".cursor-plugin/plugin.json", lambda d: d.update(rules="./rules/absent.mdc"))
            result = check_manifests.run(root)
        self.assertFalse(result["ok"])
        self.assertIn(".cursor-plugin/plugin.json: rules './rules/absent.mdc' does not exist", joined(result["errors"]))

    def test_descriptions_claim_no_counts_and_keep_the_boundary(self) -> None:
        for rel in ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json", ".grok-plugin/plugin.json",
                    ".cursor-plugin/plugin.json"):
            description = landed(rel)["description"]
            with self.subTest(manifest=rel):
                self.assertIsNone(COUNT_RE.search(description), description)
                self.assertIsNone(re.search(r"\bv\d+\.\d+\.\d+\b", description))
        for rel in ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json"):
            with self.subTest(manifest=rel):
                description = landed(rel)["description"]
                self.assertIn("never places, modifies or cancels an order", description)
                self.assertIn("Stage 1", description)

    def test_the_count_pattern_catches_the_old_claims(self) -> None:
        for claim in ("Eight roles and twenty-one skills", "eight roles with full system prompts and twenty-one skills",
                      "twenty-one skills and the desk rule", "3 agents", "15 skills"):
            self.assertIsNotNone(COUNT_RE.search(claim), claim)


STUB_TOOL = '''"""Gate fixture tool: only its parser matters to the manifest check (read by AST, never imported)."""
from __future__ import annotations

import argparse

ACTIONS = ("list", "show")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stub")
    parser.add_argument("--post", action="store_true", help="record the run")
    parser.add_argument("--summary", action="store_true", help="always print the pre-close line")
    parser.add_argument("--until", default=None, metavar="HH:MM", help="a value-taking flag")
    parser.add_argument("--fixture-set", metavar="NAME", default=None, help="recorded fixture set")
    return parser
'''

POSITIONAL_TOOL = '''"""Gate fixture tool with positionals: choices from a module-level tuple and from an inline list."""
from __future__ import annotations

import argparse
from typing import Tuple

ACTIONS: Tuple[str, ...] = ("list", "show")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stub")
    parser.add_argument("action", nargs="?", default=None, choices=ACTIONS, help="what to do")
    parser.add_argument("mode", nargs="?", default=None, choices=["week", "day"], help="which view")
    return parser
'''

ROUTINE_MANIFEST = '''# Gate test routine manifest: one routine on the good tree's desk-lead agent.
schema_version = 1

[profiles.lean]
description = "one run per weekday"

[[routine]]
id = "book-check"
owner = "desk-lead"
stage = 1
profiles = ["lean"]
et = { lean = ["10:05"] }
days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
trading_days_only = true
chain = ["book_check --post"]
post = ["book_check"]
posts = "transitions"
quiet_rule = "Posts only a verdict change or a standing UNAVAILABLE."
approval_boundary = "read-only; writes only under state/ and journal/ of the desk root"
prompt = "Run python3 /workspace/aegis-grok-desk/scripts/routine.py book-check --slot {slot} and post its output verbatim."
'''

SECOND_ROUTINE = '''
[[routine]]
id = "eod"
owner = "desk-lead"
stage = 1
profiles = ["lean"]
et = { lean = ["16:35"] }
days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
trading_days_only = true
chain = ["book_check --post --summary"]
depends_on = { routine = "book-check", min_gap_min = 20 }
post = ["book_check"]
posts = "always"
quiet_rule = "Always one message."
approval_boundary = "read-only; writes only under state/ and journal/ of the desk root"
prompt = "Run python3 /workspace/aegis-grok-desk/scripts/routine.py eod --slot {slot} and post its output verbatim."
'''

REAL_AUTOREVIEW = os.path.join(tests._path._ROOT, "routines", "autoreview.toml")
LANDED_STEP_TOOLS = ("assignment_watch", "book", "book_check", "csv_recon", "expiry_checklist")


def routine_tree(root: str, manifest: str = ROUTINE_MANIFEST, autoreview: bool = True) -> None:
    """Turn a good-tree copy into one whose manifest declares routines, with the tool and texts they need."""
    write(root, "routines/manifest.toml", manifest)
    write(root, "scripts/book_check.py", STUB_TOOL)
    if autoreview:
        with open(REAL_AUTOREVIEW, encoding="utf-8") as handle:
            write(root, "routines/autoreview.toml", handle.read())


class RoutinesManifestTests(unittest.TestCase):
    """check_routines_manifest: the full parse (aegis_desk.routines), owners, tool parsers by AST, Auto-review."""

    def run_on(self, manifest: str = ROUTINE_MANIFEST, autoreview: bool = True,
               extra: Callable[[str], None] = lambda root: None) -> dict:
        with fixture_tree() as root:
            routine_tree(root, manifest, autoreview)
            extra(root)
            return check_manifests.run(root)

    def assert_fails(self, result: dict, *needles: str) -> None:
        self.assertFalse(result["ok"], joined(result["errors"]))
        text = joined(result["errors"])
        for needle in needles:
            self.assertIn(needle, text)

    def mutated(self, old: str, new: str, text: str = ROUTINE_MANIFEST) -> str:
        self.assertEqual(1, text.count(old), old)
        return text.replace(old, new)

    def test_routine_tree_passes(self) -> None:
        result = self.run_on()
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertEqual(1, result["checked"]["routines"])
        result = self.run_on(ROUTINE_MANIFEST + SECOND_ROUTINE)
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertEqual(2, result["checked"]["routines"])

    def test_skeleton_manifest_needs_no_autoreview(self) -> None:
        with fixture_tree() as root:
            self.assertFalse(os.path.exists(os.path.join(root, "routines", "autoreview.toml")))
            result = check_manifests.run(root)
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertEqual(0, result["checked"]["routines"])

    def test_structural_rules_reach_the_gate(self) -> None:
        two = ROUTINE_MANIFEST + SECOND_ROUTINE
        cases = (
            ("duplicate id", self.mutated('id = "eod"', 'id = "book-check"', two), "duplicate routine id 'book-check'"),
            ("bad id", self.mutated('id = "book-check"', 'id = "Book_Check"'), "routine id must match"),
            ("unknown profile", self.mutated('profiles = ["lean"]\net = { lean = ["10:05"] }',
                                             'profiles = ["turbo"]\net = { turbo = ["10:05"] }'),
             "profile 'turbo' is not declared under [profiles]"),
            ("bad time", self.mutated('et = { lean = ["10:05"] }', 'et = { lean = ["10:5"] }'),
             "et.lean slot '10:5' is not HH:MM"),
            ("bad day", self.mutated('"Fri"]', '"Fry"]', ROUTINE_MANIFEST), "day 'Fry' is not one of"),
            ("delay window", self.mutated('et = { lean = ["10:05"] }', 'et = { lean = ["09:35"] }'),
             "slot 09:35 falls in the delay window 08:00-10:00 ET on a weekday"),
            ("owned flag", self.mutated('chain = ["book_check --post"]', 'chain = ["book_check --post --json"]'),
             "--json is set by routine.py, never by the manifest"),
            ("forbidden flag", self.mutated('chain = ["book_check --post"]',
                                            'chain = ["book_check --post --allow-dirty"]'),
             "--allow-dirty is never allowed in a routine chain"),
            ("ack config", self.mutated('chain = ["book_check --post"]', 'chain = ["book_check --post", "ack config"]'),
             "step 'ack config': ack is not a step tool"),
            ("unknown dependency", self.mutated('routine = "book-check", min_gap_min', 'routine = "bookcheck", min_gap_min',
                                                two),
             "depends_on.routine 'bookcheck' is not a routine of this manifest"),
            ("min_gap 10", self.mutated('min_gap_min = 20', 'min_gap_min = 10', two),
             "depends_on.min_gap_min must be an integer of at least 20 (found 10)"),
            ("gap violation", self.mutated('et = { lean = ["16:35"] }', 'et = { lean = ["10:15"] }', two),
             "routine eod: slot 10:15 is 10 min after book-check's latest slot 10:05 (depends_on.min_gap_min 20)"),
            ("post not in chain", self.mutated('post = ["book_check"]\nposts = "transitions"',
                                               'post = ["csv_recon"]\nposts = "fired"'),
             "post tool 'csv_recon' is not a step of its chains"),
            ("transitions without book_check", self.mutated('chain = ["book_check --post"]\npost = ["book_check"]',
                                                            'chain = ["book_check --post", "expiry_checklist --week"]\n'
                                                            'post = ["expiry_checklist"]'),
             "posts 'transitions' requires post = [\"book_check\"]"),
            ("unknown slot_chain key", self.mutated('chain = ["book_check --post"]',
                                                    'chain = ["book_check --post"]\nslot_chain = { "10:06" = '
                                                    '["book_check --post --summary"] }'),
             "slot_chain key '10:06' is not a slot of the routine"),
            ("prompt mismatch", self.mutated("routine.py book-check --slot", "routine.py book-check --allow-dirty --slot"),
             "routine book-check: prompt must be exactly"),
        )
        for name, manifest, needle in cases:
            with self.subTest(name):
                self.assert_fails(self.run_on(manifest), "routines/manifest.toml: ", needle)

    def test_unknown_owner_fixture(self) -> None:
        with fixture_tree("routine_unknown_owner") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, "routines/manifest.toml: routine book-check: owner 'risk-bot' has no agents/risk-bot.md")
        self.assertEqual(1, len(result["errors"]), joined(result["errors"]))

    def test_unknown_flag_fixture(self) -> None:
        with fixture_tree("routine_unknown_flag") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, "routines/manifest.toml: routine book-check: step 'book_check --post --nope': "
                                  "scripts/book_check.py does not accept --nope")
        self.assertEqual(1, len(result["errors"]), joined(result["errors"]))

    def test_autoreview_too_long_fixture(self) -> None:
        with fixture_tree("autoreview_too_long") as root:
            result = check_manifests.run(root)
        self.assert_fails(result, "routines/autoreview.toml: [allow_routines] renders to 1080 characters (limit 1000)")
        self.assertEqual(1, len(result["errors"]), joined(result["errors"]))

    def test_chain_tool_must_exist_in_the_tree(self) -> None:
        manifest = self.mutated('chain = ["book_check --post"]', 'chain = ["book_check --post", "csv_recon"]')
        self.assert_fails(self.run_on(manifest),
                          "routines/manifest.toml: routine book-check: step 'csv_recon': scripts/csv_recon.py does not exist")

    def test_flag_values_by_ast(self) -> None:
        accepted = ("book_check --post --until 10:00", "book_check --post --until=10:00", "book_check --summary --post")
        for chain in accepted:
            result = self.run_on(self.mutated('chain = ["book_check --post"]', 'chain = ["%s"]' % chain))
            self.assertTrue(result["ok"], (chain, joined(result["errors"])))
        refused = (
            ("book_check --post --until", "scripts/book_check.py: --until needs a value"),
            ("book_check --post --until --summary", "scripts/book_check.py: --until needs a value"),
            ("book_check --post --summary=yes", "scripts/book_check.py: --summary takes no value"),
            ("book_check --post extra", "scripts/book_check.py does not take a positional argument 'extra'"),
            ("book_check --post -x", "scripts/book_check.py does not accept -x"),
        )
        for chain, needle in refused:
            with self.subTest(chain):
                self.assert_fails(self.run_on(self.mutated('chain = ["book_check --post"]', 'chain = ["%s"]' % chain)),
                                  "step '%s': %s" % (chain, needle))

    def test_positionals_and_their_choices_by_ast(self) -> None:
        def with_tool(root: str) -> None:
            write(root, "scripts/assignment_watch.py", POSITIONAL_TOOL)

        for chain in ("assignment_watch list", "assignment_watch show week", "assignment_watch"):
            manifest = self.mutated('chain = ["book_check --post"]', 'chain = ["book_check --post", "%s"]' % chain)
            result = self.run_on(manifest, extra=with_tool)
            self.assertTrue(result["ok"], (chain, joined(result["errors"])))
        refused = (
            ("assignment_watch nope", "positional 'nope' is not one of list, show (scripts/assignment_watch.py action)"),
            ("assignment_watch list month", "positional 'month' is not one of week, day (scripts/assignment_watch.py mode)"),
            ("assignment_watch list week extra", "scripts/assignment_watch.py does not take a positional argument 'extra'"),
        )
        for chain, needle in refused:
            with self.subTest(chain):
                manifest = self.mutated('chain = ["book_check --post"]', 'chain = ["book_check --post", "%s"]' % chain)
                self.assert_fails(self.run_on(manifest, extra=with_tool), needle)

    def test_unparsable_tool_is_reported(self) -> None:
        def broken(root: str) -> None:
            write(root, "scripts/book_check.py", "def broken(:\n")

        self.assert_fails(self.run_on(extra=broken), "routines/manifest.toml: routine book-check: step 'book_check --post': "
                                                     "scripts/book_check.py cannot be read as Python")

    def test_autoreview_is_required_once_a_routine_is_declared(self) -> None:
        self.assert_fails(self.run_on(autoreview=False),
                          "routines/autoreview.toml: missing; required once routines/manifest.toml declares a routine")

    def test_autoreview_structure_reaches_the_gate(self) -> None:
        def short_limit(root: str) -> None:
            path = os.path.join(root, "routines", "autoreview.toml")
            write(root, "routines/autoreview.toml", read(root, "routines/autoreview.toml").replace(
                "limit_chars = 1000", "limit_chars = 999"))
            self.assertTrue(os.path.isfile(path))

        self.assert_fails(self.run_on(extra=short_limit), "routines/autoreview.toml: limit_chars must be 1000 (found 999)")

    def test_invalid_toml_is_one_error(self) -> None:
        result = self.run_on(ROUTINE_MANIFEST + "\n[[routine]\n")
        self.assert_fails(result, "routines/manifest.toml: not valid TOML")

    def test_parser_spec_of_the_landed_tools(self) -> None:
        scripts = os.path.join(tests._path._ROOT, "scripts")
        spec = check_manifests.read_parser_spec(os.path.join(scripts, "book_check.py"))
        self.assertEqual({"--post": False, "--summary": False, "--watch-alive": False, "--next-run-utc": True,
                          "--routine": True, "--scheduled-et": True, "--fixture-set": True}, spec.flags)
        self.assertEqual((), spec.positionals)
        self.assertEqual({"--week": False, "--tomorrow": False, "--final-call": False, "--fixture-set": True},
                         check_manifests.read_parser_spec(os.path.join(scripts, "expiry_checklist.py")).flags)
        ack = check_manifests.read_parser_spec(os.path.join(scripts, "ack.py"))
        self.assertEqual(("list", "show", "ack", "snooze", "acted", "config", "connectors"), ack.positionals[0].choices)
        self.assertEqual("signal_id", ack.positionals[1].name)
        self.assertIsNone(ack.positionals[1].choices)
        for tool in LANDED_STEP_TOOLS:
            flags = check_manifests.read_parser_spec(os.path.join(scripts, tool + ".py")).flags
            self.assertEqual(tool in routines.FIXTURE_SET_TOOLS, "--fixture-set" in flags, tool)

    def test_real_manifest_steps_of_the_landed_tools_are_accepted(self) -> None:
        # The full real-tree check also needs desk_doctor, eod_summary and trigger_levels (PR 2d builders).
        scripts = os.path.join(tests._path._ROOT, "scripts")
        manifest = routines.load_manifest()
        checked = 0
        for routine in manifest.routines:
            for steps in routine.chains():
                for step in steps:
                    tool = routines.step_tool(step)
                    if tool not in LANDED_STEP_TOOLS:
                        continue
                    spec = check_manifests.read_parser_spec(os.path.join(scripts, tool + ".py"))
                    self.assertEqual([], check_manifests.check_step(spec, "scripts/%s.py" % tool,
                                                                    routines.step_tokens(step)), step)
                    checked += 1
        self.assertEqual(12, checked)


class TemplateContractTests(unittest.TestCase):
    def run_template(self, root: str) -> dict:
        return check_grok_template.run(root)

    def assert_fails(self, result: dict, *needles: str) -> None:
        self.assertFalse(result["ok"], joined(result["errors"]))
        text = joined(result["errors"])
        for needle in needles:
            self.assertIn(needle, text)

    def test_good_tree_passes_and_skips_tag_comparison_out_loud(self) -> None:
        with fixture_tree() as root:
            result = self.run_template(root)
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertEqual("unpublished", result["status"])
        self.assertEqual(1, result["skills_verified"])
        self.assertTrue(any(line.startswith("skip:") and "not a git checkout" in line for line in result["skips"]),
                        result["skips"])

    def test_stale_hash_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d["skills"].update({"aegis-desk-charter": "0" * 64}))
            result = self.run_template(root)
        self.assert_fails(result, "skills.aegis-desk-charter sha256 does not match skills/aegis-desk-charter/SKILL.md")

    def test_malformed_hash_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d["skills"].update({"aegis-desk-charter": "abc"}))
            result = self.run_template(root)
        self.assert_fails(result, "64-character lowercase hex digest")

    def test_missing_skill_file_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d["skills"].update({"aegis-ghost": "0" * 64}))
            result = self.run_template(root)
        self.assert_fails(result, "skills.aegis-ghost points at a missing file skills/aegis-ghost/SKILL.md")

    def test_object_entry_with_wrong_path_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json",
                      lambda d: d["skills"].update({"aegis-desk-charter": {"path": "skills/other/SKILL.md", "sha256": "0" * 64}}))
            result = self.run_template(root)
        self.assert_fails(result, "path must be skills/aegis-desk-charter/SKILL.md")

    def test_unknown_template_skill_name_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(templateSkillNames=["aegis-desk-charter", "aegis-nope"]))
            result = self.run_template(root)
        self.assert_fails(result, "templateSkillNames contains unknown skills: aegis-nope")

    def test_duplicate_template_skill_names_are_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(templateSkillNames=["aegis-desk-charter"] * 2))
            result = self.run_template(root)
        self.assert_fails(result, "duplicate templateSkillNames")

    def test_release_mismatch_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d["source"].update(release="0.0.9"))
            result = self.run_template(root)
        self.assert_fails(result, "source.release '0.0.9' does not match plugin.json version '0.1.0'")

    def test_release_with_v_prefix_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d["source"].update(release="v0.1.0"))
            result = self.run_template(root)
        self.assert_fails(result, "source.release 'v0.1.0' must be a plain X.Y.Z version")

    def test_unknown_status_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(status="draft"))
            result = self.run_template(root)
        self.assert_fails(result, "status 'draft' must be one of")

    def test_unpublished_template_must_not_carry_url(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(publicShareUrl="https://x.ai/bot/abc"))
            result = self.run_template(root)
        self.assert_fails(result, "unpublished template must not carry a publicShareUrl")

    def test_published_template_needs_url_in_docs(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(status="published"))
            result = self.run_template(root)
            self.assert_fails(result, "published template needs an https publicShareUrl")
            edit_json(root, "template/grok-bot.json", lambda d: d.update(publicShareUrl="https://x.ai/bot/abc"))
            result = self.run_template(root)
            self.assert_fails(result, "README.md: published template URL https://x.ai/bot/abc is missing",
                              "docs/FAQ.md: published template URL https://x.ai/bot/abc is missing")
            write(root, "README.md", read(root, "README.md") + "\nStart: https://x.ai/bot/abc\n")
            write(root, "docs/FAQ.md", read(root, "docs/FAQ.md") + "\nTemplate: https://x.ai/bot/abc\n")
            result = self.run_template(root)
        self.assertTrue(result["ok"], joined(result["errors"]))

    def test_description_must_state_boundary_and_release(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(description="Aegis Desk Lead (release 0.0.9): watches positions."))
            result = self.run_template(root)
        self.assert_fails(result, "description must state that the desk never places, modifies or cancels orders",
                          "description names release 0.0.9")

    def test_wrong_bot_name_is_rejected(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(name="Desk Lead"))
            result = self.run_template(root)
        self.assert_fails(result, "name must be 'Aegis Desk Lead'")

    def test_ready_template_must_claim_every_skill_in_the_tree(self) -> None:
        skill = "---\nname: aegis-extra\ndescription: x\nlicense: MIT\n---\n\n# Extra\n"
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(status="ready-to-publish"))
            self.assertTrue(self.run_template(root)["ok"])  # the fixture claims its one skill
            write(root, "skills/aegis-extra/SKILL.md", skill)
            write(root, "skills/aegis-role-desk-lead/SKILL.md", skill.replace("aegis-extra", "aegis-role-desk-lead"))
            result = self.run_template(root)
            self.assert_fails(result, "skills must claim skills/aegis-extra/SKILL.md",
                              "roleSkills must claim skills/aegis-role-desk-lead/SKILL.md")
            self.assertNotIn("skills must claim skills/aegis-role-desk-lead", joined(result["errors"]))
            # An unpublished template may still be incomplete: the scaffold passes while skills are written.
            edit_json(root, "template/grok-bot.json", lambda d: d.update(status="unpublished"))
            self.assertTrue(self.run_template(root)["ok"], joined(self.run_template(root)["errors"]))

    def test_ready_template_must_export_a_skill(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(templateSkillNames=[]))
            self.assertTrue(self.run_template(root)["ok"])  # unpublished: allowed
            edit_json(root, "template/grok-bot.json", lambda d: d.update(status="ready-to-publish"))
            result = self.run_template(root)
        self.assert_fails(result, "a ready-to-publish template must export at least one skill in templateSkillNames")

    def test_role_skills_table_is_verified(self) -> None:
        with fixture_tree() as root:
            edit_json(root, "template/grok-bot.json", lambda d: d.update(roleSkills={"aegis-role-desk-lead": "0" * 64}))
            result = self.run_template(root)
        self.assert_fails(result, "roleSkills.aegis-role-desk-lead points at a missing file")


@unittest.skipUnless(shutil.which("git"), "git is not available")
class TemplateTagComparisonTests(unittest.TestCase):
    """The tag comparison runs when the release tag exists and fails on drifted bytes."""

    @staticmethod
    def git(root: str, *args: str) -> None:
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "gate", "GIT_AUTHOR_EMAIL": "gate@test",
                    "GIT_COMMITTER_NAME": "gate", "GIT_COMMITTER_EMAIL": "gate@test"})
        subprocess.run(["git", "-C", root, "-c", "commit.gpgsign=false", *args],
                       check=True, capture_output=True, env=env)

    def init_repo(self, root: str, tag: str) -> None:
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "fixture")
        self.git(root, "tag", "-a", tag, "-m", tag)

    def test_tag_absent_skips_out_loud(self) -> None:
        with fixture_tree() as root:
            self.git(root, "init", "-q")
            result = check_grok_template.run(root)
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertTrue(any("no local tag v0.1.0" in line for line in result["skips"]), result["skips"])

    def test_matching_tag_passes_without_skip(self) -> None:
        with fixture_tree() as root:
            self.init_repo(root, "v0.1.0")
            result = check_grok_template.run(root)
        self.assertTrue(result["ok"], joined(result["errors"]))
        self.assertEqual([], result["skips"])

    def test_drifted_bytes_fail_by_path(self) -> None:
        with fixture_tree() as root:
            self.init_repo(root, "v0.1.0")
            rel = "skills/aegis-desk-charter/SKILL.md"
            write(root, rel, read(root, rel) + "\nDrift after the tag.\n")
            digest = check_grok_template.sha256(os.path.join(root, rel))
            edit_json(root, "template/grok-bot.json", lambda d: d["skills"].update({"aegis-desk-charter": digest}))
            result = check_grok_template.run(root)
        self.assertFalse(result["ok"])
        self.assertIn(f"template/grok-bot.json: {rel} does not match tag v0.1.0", joined(result["errors"]))


if __name__ == "__main__":
    unittest.main()
