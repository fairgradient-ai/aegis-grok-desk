"""Negative and positive fixtures for scripts/check_manifests.py and scripts/check_grok_template.py.

Each case copies the good fixture tree, breaks the contract in exactly one way and asserts the
checker fails naming that file and that defect. A guard nobody has watched fail is not a guard.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from typing import Any, Callable, List

# isort: off
import tests._path  # noqa: F401
import check_grok_template
import check_manifests
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
