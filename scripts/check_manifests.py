#!/usr/bin/env python3
"""Validate the plugin distribution manifests of Aegis Desk. Stdlib Python only; no network.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
changes, commit 75a4c79 (scripts/check_manifests.py). The manifest set, the hook rules and
the desk-rule drift check are specific to Aegis Desk.

Contract
--------
Every manifest in this repository describes one distributable: a plugin named ``aegis-desk``
at the version declared by the root ``plugin.json`` (the single source of truth), pointing at
the skills, agents, rules and hooks checked in here.

* ``plugin.json`` ``version`` is a plain ``X.Y.Z`` release version; a pre-release suffix such
  as ``0.1.0-dev`` is rejected, because the manifest version is the release being cut.
* ``version`` in ``.grok-plugin/plugin.json``, ``.claude-plugin/plugin.json``,
  ``.claude-plugin/marketplace.json`` (``metadata.version`` and every ``plugins[].version``),
  ``.cursor-plugin/plugin.json``, ``gemini-extension.json`` and
  ``.grok-plugin/marketplace.json`` ``plugins[].version`` must equal it.
* Marketplace sources are pinned to the release tag ``v<version>``: the Grok and Codex
  marketplaces use the ``{"source": "url", "url": "<repo>.git", "ref": "v<version>"}`` form and
  the Claude marketplace the ``{"source": "github", "repo": "<owner>/<repo>", "ref": ...}`` form.
* Every ``name`` (manifests and nested ``plugins[]`` entries) is ``aegis-desk``; every
  ``repository``/``homepage`` is this repository.
* Claude Code and Grok Build auto-discover ``skills/`` and ``agents/`` (and ``commands/``), so
  ``plugin.json``, ``.grok-plugin/plugin.json`` and ``.claude-plugin/plugin.json`` may not
  declare ``skills``, ``agents`` or ``commands`` paths: a declaration double-loads or shadows
  the discovered set. ``.cursor-plugin/plugin.json`` is the one manifest that declares them
  and must declare ``skills``, ``agents`` and ``rules`` (Cursor reads nothing else).
* Hooks come from exactly one place per runtime. Grok Build reads the first manifest it finds
  (``plugin.json`` -> ``.grok-plugin/plugin.json`` -> ``.claude-plugin/plugin.json``) and
  Claude Code auto-loads ``hooks/hooks.json`` and rejects a duplicate declaration, so none of
  those three manifests may carry a ``hooks`` key. Cursor reads its own native file, so
  ``.cursor-plugin/plugin.json`` must declare ``"hooks": "./hooks/cursor-hooks.json"`` and
  that file must exist with ``"version": 1``. ``hooks/hooks.json`` must be valid JSON with a
  top-level ``hooks`` object.
* ``template/grok-bot.json`` ``source.release`` equals the version (written without a ``v``).
* The desk-rule paragraph (the paragraph of ``rules/aegis-desk.mdc`` that begins "This desk
  never places") appears verbatim in ``GEMINI.md`` and ``AGENTS.md``, which are loaded on every
  Gemini CLI and generic-agent session respectively.
* ``routines/manifest.toml`` passes a minimal line-based check (``schema_version`` present
  exactly once). The vendored TOML reader will parse it fully in a later release.

Usage: ``python3 scripts/check_manifests.py [repo-root] [--json]``. Exit 0 when every check
passes, 1 when any fails, 2 on a usage error. ``--json`` prints one object with ``ok``,
``version``, ``errors`` and ``checked`` instead of prose.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

CANONICAL_NAME = "aegis-desk"
REPOSITORY = "https://github.com/fairgradient-ai/aegis-grok-desk"
SHORTHAND = "fairgradient-ai/aegis-grok-desk"

ROOT_MANIFEST = "plugin.json"
MANIFESTS = (
    "plugin.json",
    ".grok-plugin/plugin.json",
    ".grok-plugin/marketplace.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/plugin.json",
    "gemini-extension.json",
    ".agents/plugins/marketplace.json",
)
# Grok Build reads the first of these it finds and Claude Code auto-loads hooks/hooks.json:
# a `hooks` key in any of them would double-declare or shadow the gate.
NO_HOOKS_KEY_MANIFESTS = ("plugin.json", ".grok-plugin/plugin.json", ".claude-plugin/plugin.json")
# The same three manifests are read by runtimes that auto-discover components; declaring the
# component paths there double-loads or shadows skills/ and agents/ (plan section 3).
AUTO_DISCOVERY_MANIFESTS = NO_HOOKS_KEY_MANIFESTS
NO_DISCOVERY_KEYS = ("skills", "agents", "commands")
CURSOR_REQUIRED_PATHS = ("skills", "agents", "rules")
GROK_MARKETPLACE = ".grok-plugin/marketplace.json"
CLAUDE_MARKETPLACE = ".claude-plugin/marketplace.json"
CODEX_MARKETPLACE = ".agents/plugins/marketplace.json"
URL_PINNED_MARKETPLACES = (GROK_MARKETPLACE, CODEX_MARKETPLACE)
CURSOR_MANIFEST = ".cursor-plugin/plugin.json"
CURSOR_HOOKS_DECLARATION = "./hooks/cursor-hooks.json"
HOOKS_FILE = "hooks/hooks.json"
GEMINI_MANIFEST = "gemini-extension.json"
TEMPLATE = "template/grok-bot.json"
DESK_RULE = "rules/aegis-desk.mdc"
DESK_RULE_PARAGRAPH_START = "This desk never places"
DESK_RULE_MIRRORS = ("GEMINI.md", "AGENTS.md")
ROUTINES_MANIFEST = "routines/manifest.toml"
CODEX_INSTALL_POLICIES = ("AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE")
CODEX_AUTH_POLICIES = ("ON_INSTALL", "ON_USE")
# Manifest keys whose value is a path into this repository.
PATH_KEYS = ("skills", "agents", "rules", "contextFileName")
# (manifest, nested-entry?) pairs that must declare a version. The Grok marketplace root and
# the Codex index carry no version of their own; their entries pin the release through
# source.ref (and, for Grok, plugins[].version). A version that is present anywhere must match.
VERSION_REQUIRED = frozenset({
    ("plugin.json", False),
    (".grok-plugin/plugin.json", False),
    (".grok-plugin/marketplace.json", True),
    (".claude-plugin/plugin.json", False),
    (".claude-plugin/marketplace.json", False),
    (".claude-plugin/marketplace.json", True),
    (".cursor-plugin/plugin.json", False),
    ("gemini-extension.json", False),
})

PLAIN_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
SCHEMA_VERSION_RE = re.compile(r"^schema_version\s*=\s*\d+\s*(?:#.*)?$")


def read_text(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def load_json(root: str, rel: str, errors: List[str]) -> Optional[Any]:
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        errors.append(f"{rel}: manifest is missing")
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        errors.append(f"{rel}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}")
    except UnicodeDecodeError as exc:
        errors.append(f"{rel}: not valid UTF-8: {exc}")
    return None


def entries(data: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """The manifest object itself plus any nested marketplace ``plugins[]`` entries."""
    if not isinstance(data, dict):
        return []
    found = [("", data)]
    plugins = data.get("plugins")
    if isinstance(plugins, list):
        for index, plugin in enumerate(plugins):
            if isinstance(plugin, dict):
                found.append((f"plugins[{index}].", plugin))
    return found


def declared_version(entry: Dict[str, Any]) -> Optional[Any]:
    """``version`` or, for the Claude marketplace root, ``metadata.version``."""
    if "version" in entry:
        return entry["version"]
    metadata = entry.get("metadata")
    if isinstance(metadata, dict) and "version" in metadata:
        return metadata["version"]
    return None


def check_identity(rel: str, prefix: str, entry: Dict[str, Any], version: Optional[str],
                   errors: List[str]) -> None:
    name = entry.get("name")
    if name != CANONICAL_NAME:
        errors.append(f"{rel}: {prefix}name '{name}' should be '{CANONICAL_NAME}'")
    declared = declared_version(entry)
    if declared is None:
        if (rel, bool(prefix)) in VERSION_REQUIRED:
            errors.append(f"{rel}: {prefix}version is missing")
    elif version is not None and declared != version:
        errors.append(
            f"{rel}: {prefix}version '{declared}' does not match {ROOT_MANIFEST} version '{version}'"
        )
    for key in ("repository", "homepage"):
        if key in entry and entry[key] != REPOSITORY:
            errors.append(f"{rel}: {prefix}{key} '{entry[key]}' should be '{REPOSITORY}'")


def check_path(root: str, rel: str, prefix: str, key: str, value: Any, errors: List[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append(f"{rel}: {prefix}{key} is not a path")
        return
    if value.startswith(("http://", "https://")):
        errors.append(f"{rel}: {prefix}{key} must be a repository path, not a URL")
        return
    resolved = os.path.normpath(os.path.join(root, value))
    if os.path.relpath(resolved, os.path.normpath(root)).startswith(".."):
        errors.append(f"{rel}: {prefix}{key} '{value}' resolves outside the repository")
        return
    if not os.path.exists(resolved):
        errors.append(f"{rel}: {prefix}{key} '{value}' does not exist")


def check_paths(root: str, rel: str, prefix: str, entry: Dict[str, Any], errors: List[str]) -> None:
    for key in PATH_KEYS:
        if key not in entry:
            continue
        value = entry[key]
        items = value if (key == "contextFileName" and isinstance(value, list)) else [value]
        for item in items:
            check_path(root, rel, prefix, key, item, errors)


def check_url_pinned_source(rel: str, prefix: str, entry: Dict[str, Any], version: Optional[str],
                            errors: List[str]) -> None:
    """Grok and Codex install this repository at its release tag, never a moving root."""
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "url":
        errors.append(f"{rel}: {prefix}source must use the pinned remote URL form")
        return
    if source.get("url") != REPOSITORY + ".git":
        errors.append(f"{rel}: {prefix}source.url must be '{REPOSITORY}.git'")
    if version is not None and source.get("ref") != f"v{version}":
        errors.append(f"{rel}: {prefix}source.ref must pin v{version} (found {source.get('ref')!r})")


def check_github_pinned_source(rel: str, prefix: str, entry: Dict[str, Any], version: Optional[str],
                               errors: List[str]) -> None:
    """The Claude marketplace entry pins the release through a github source with a ref."""
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "github":
        errors.append(f"{rel}: {prefix}source must use the github source form with a ref")
        return
    if source.get("repo") != SHORTHAND:
        errors.append(f"{rel}: {prefix}source.repo must be '{SHORTHAND}'")
    if version is not None and source.get("ref") != f"v{version}":
        errors.append(f"{rel}: {prefix}source.ref must pin v{version} (found {source.get('ref')!r})")


def check_codex_entry(rel: str, prefix: str, entry: Dict[str, Any], errors: List[str]) -> None:
    """Codex's marketplace format requires an install policy and a category per entry."""
    policy = entry.get("policy")
    if not isinstance(policy, dict):
        errors.append(f"{rel}: {prefix}policy is required")
    else:
        if policy.get("installation") not in CODEX_INSTALL_POLICIES:
            errors.append(
                f"{rel}: {prefix}policy.installation must be one of {list(CODEX_INSTALL_POLICIES)}"
            )
        if policy.get("authentication") not in CODEX_AUTH_POLICIES:
            errors.append(
                f"{rel}: {prefix}policy.authentication must be one of {list(CODEX_AUTH_POLICIES)}"
            )
    if not isinstance(entry.get("category"), str) or not entry["category"]:
        errors.append(f"{rel}: {prefix}category is required")


def check_component_declarations(manifests: Dict[str, Any], errors: List[str]) -> None:
    """Only the Cursor manifest declares component paths; the others must leave discovery alone."""
    for rel in AUTO_DISCOVERY_MANIFESTS:
        data = manifests.get(rel)
        if not isinstance(data, dict):
            continue
        for key in NO_DISCOVERY_KEYS:
            if key in data:
                errors.append(
                    f"{rel}: must not declare '{key}'; Claude Code and Grok Build auto-discover skills/ "
                    f"and agents/; declaring them double-loads or shadows (only {CURSOR_MANIFEST} declares paths)"
                )
    cursor = manifests.get(CURSOR_MANIFEST)
    if isinstance(cursor, dict):
        for key in CURSOR_REQUIRED_PATHS:
            if key not in cursor:
                errors.append(f"{CURSOR_MANIFEST}: must declare '{key}' (Cursor reads only its own manifest)")


def check_hooks(root: str, manifests: Dict[str, Any], errors: List[str]) -> None:
    for rel in NO_HOOKS_KEY_MANIFESTS:
        data = manifests.get(rel)
        if isinstance(data, dict) and "hooks" in data:
            errors.append(
                f"{rel}: must not declare 'hooks'; Grok Build and Claude Code load hooks/hooks.json "
                "and reject a second declaration"
            )
    cursor = manifests.get(CURSOR_MANIFEST)
    if isinstance(cursor, dict):
        declared = cursor.get("hooks")
        if declared != CURSOR_HOOKS_DECLARATION:
            errors.append(
                f"{CURSOR_MANIFEST}: hooks must be '{CURSOR_HOOKS_DECLARATION}' (found {declared!r})"
            )
        else:
            cursor_hooks = load_json(root, "hooks/cursor-hooks.json", errors)
            if cursor_hooks is not None:
                if not isinstance(cursor_hooks, dict) or cursor_hooks.get("version") != 1:
                    errors.append("hooks/cursor-hooks.json: must declare \"version\": 1")
                elif not isinstance(cursor_hooks.get("hooks"), dict):
                    errors.append("hooks/cursor-hooks.json: 'hooks' must be an object")
    hooks = load_json(root, HOOKS_FILE, errors)
    if hooks is not None and (not isinstance(hooks, dict) or not isinstance(hooks.get("hooks"), dict)):
        errors.append(f"{HOOKS_FILE}: must be an object with a top-level 'hooks' object")


def check_template_release(root: str, version: Optional[str], errors: List[str]) -> None:
    template = load_json(root, TEMPLATE, errors)
    if template is None or version is None:
        return
    source = template.get("source") if isinstance(template, dict) else None
    release = source.get("release") if isinstance(source, dict) else None
    if release != version:
        errors.append(
            f"{TEMPLATE}: source.release {release!r} must equal {ROOT_MANIFEST} version '{version}'"
        )


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[end + 5:]
    return text


def desk_rule_paragraph(root: str, errors: List[str]) -> Optional[str]:
    """The paragraph of the desk rule that states the no-trade boundary, stripped."""
    try:
        text = read_text(root, DESK_RULE)
    except OSError as exc:
        errors.append(f"{DESK_RULE}: cannot read the desk rule: {exc}")
        return None
    body = strip_frontmatter(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    for paragraph in paragraphs:
        if paragraph.startswith(DESK_RULE_PARAGRAPH_START):
            return paragraph
    errors.append(f"{DESK_RULE}: no paragraph starts '{DESK_RULE_PARAGRAPH_START}'")
    return None


def check_desk_rule_mirrors(root: str, errors: List[str]) -> None:
    paragraph = desk_rule_paragraph(root, errors)
    if paragraph is None:
        return
    for rel in DESK_RULE_MIRRORS:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            errors.append(f"{rel}: missing; it must repeat the desk-rule paragraph of {DESK_RULE}")
            continue
        if paragraph not in read_text(root, rel):
            errors.append(f"{rel}: must repeat the desk-rule paragraph of {DESK_RULE} verbatim")


def check_routines_manifest(root: str, errors: List[str]) -> None:
    path = os.path.join(root, ROUTINES_MANIFEST)
    if not os.path.isfile(path):
        errors.append(f"{ROUTINES_MANIFEST}: missing")
        return
    try:
        text = read_text(root, ROUTINES_MANIFEST)
    except UnicodeDecodeError as exc:
        errors.append(f"{ROUTINES_MANIFEST}: not valid UTF-8: {exc}")
        return
    schema_lines = [line for line in text.splitlines() if SCHEMA_VERSION_RE.match(line)]
    if len(schema_lines) != 1:
        errors.append(
            f"{ROUTINES_MANIFEST}: expected exactly one top-level 'schema_version = <int>' line, "
            f"found {len(schema_lines)}"
        )


def run(root: str) -> Dict[str, Any]:
    """Run every manifest check against ``root``; return a JSON-serialisable result."""
    root = os.path.abspath(root)
    errors: List[str] = []
    manifests: Dict[str, Any] = {}

    root_manifest = load_json(root, ROOT_MANIFEST, errors)
    version = root_manifest.get("version") if isinstance(root_manifest, dict) else None
    if not isinstance(version, str) or not PLAIN_VERSION_RE.fullmatch(version):
        errors.append(
            f"{ROOT_MANIFEST}: version {version!r} is not a plain X.Y.Z release version "
            "(pre-release suffixes such as -dev are not allowed)"
        )
        version = None

    for rel in MANIFESTS:
        data = root_manifest if rel == ROOT_MANIFEST else load_json(root, rel, errors)
        if data is None:
            continue
        manifests[rel] = data
        if not isinstance(data, dict):
            errors.append(f"{rel}: manifest must be a JSON object")
            continue
        for prefix, entry in entries(data):
            check_identity(rel, prefix, entry, version, errors)
            check_paths(root, rel, prefix, entry, errors)
            if prefix and rel in URL_PINNED_MARKETPLACES:
                check_url_pinned_source(rel, prefix, entry, version, errors)
            if prefix and rel == CLAUDE_MARKETPLACE:
                check_github_pinned_source(rel, prefix, entry, version, errors)
            if prefix and rel == CODEX_MARKETPLACE:
                check_codex_entry(rel, prefix, entry, errors)
        if rel in (GROK_MARKETPLACE, CLAUDE_MARKETPLACE, CODEX_MARKETPLACE):
            if not isinstance(data.get("plugins"), list) or not data["plugins"]:
                errors.append(f"{rel}: plugins must list the aegis-desk entry")

    check_component_declarations(manifests, errors)
    check_hooks(root, manifests, errors)
    check_template_release(root, version, errors)
    check_desk_rule_mirrors(root, errors)
    check_routines_manifest(root, errors)

    return {
        "ok": not errors,
        "version": version,
        "errors": errors,
        "checked": {
            "manifests": len(MANIFESTS),
            "hooks_files": [HOOKS_FILE, "hooks/cursor-hooks.json"],
            "desk_rule_mirrors": list(DESK_RULE_MIRRORS),
            "template": TEMPLATE,
            "routines_manifest": ROUTINES_MANIFEST,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of prose")
    args = parser.parse_args(argv)
    result = run(args.root)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["errors"]:
        print("\n".join(result["errors"]))
        print(f"{len(result['errors'])} problem(s)")
    else:
        print(
            f"ok: {len(MANIFESTS)} manifests agree on {CANONICAL_NAME} {result['version']}; "
            "hooks declared once per runtime; desk-rule paragraph mirrored; template release pinned"
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
