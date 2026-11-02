#!/usr/bin/env python3
"""Validate the public Grok Bot template contract. Stdlib Python only; no network.

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
changes, commit 75a4c79 (scripts/check_grok_template.py). The template shape (a ``skills``
object keyed by skill name, ``roleSkills``, the ``unpublished`` status, the ``v``-less
release) is specific to Aegis Desk.

Contract (``template/grok-bot.json``)
------------------------------------
* ``status`` is ``unpublished``, ``ready-to-publish`` or ``published``. Only a ``published``
  template carries ``publicShareUrl``; that https URL must then appear in ``README.md`` and
  ``docs/FAQ.md`` so the documented link and the published Bot cannot diverge.
* ``name`` is ``Aegis Desk Lead`` (the one Bot the owner talks to); ``title`` is present;
  ``description`` is 1-280 characters, states the boundary ("never places, modifies or cancels
  orders") and, when it quotes ``release X.Y.Z``, quotes the manifest version.
* ``source.repository`` is this repository and ``source.release`` equals the ``plugin.json``
  version exactly (written without a leading ``v``; the git tag is ``v<release>``).
* ``skills`` is an object keyed by skill name. Each value is either the sha256 hex digest of
  ``skills/<name>/SKILL.md`` or an object ``{"path": ..., "sha256": ...}`` whose path, when
  given, is exactly ``skills/<name>/SKILL.md``. The file must exist and its digest must match,
  so a Bot that installs a skill from the release can verify it got the reviewed bytes.
* ``roleSkills``, when present, is an object validated the same way (the generated
  ``aegis-role-*`` skills a Stage-1 Bot is given as its playbook).
* ``templateSkillNames`` is a list of unique names, each a key of ``skills``: the exported Bot
  carries only those, and every one of them must be a verified release skill.
* Coverage: once the template is ``ready-to-publish`` or ``published`` it must claim every skill
  in the tree (each ``skills/aegis-*/SKILL.md`` as a key of ``skills``, each
  ``skills/aegis-role-*/SKILL.md`` as a key of ``roleSkills``) and export at least one skill,
  so no skill ships unhashed: the hashes are how a Bot notices that the template import dropped
  one. An ``unpublished`` template may still be empty while the skills are being written.
* Tag comparison: when the tag ``v<release>`` exists in this checkout, every claimed path must
  be byte-identical to ``git show v<release>:<path>``; a working-tree hash check alone cannot
  see a pin that names an older tag while the hashes describe current files. When git is
  unavailable, the directory is not a checkout, or the tag is absent (a release being
  prepared), the comparison is skipped with a printed ``skip:`` notice rather than reported as
  a pass that never ran.

Usage: ``python3 scripts/check_grok_template.py [repo-root] [--json]``. Exit 0 when every check
passes (skips are printed), 1 when any fails, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPOSITORY = "https://github.com/fairgradient-ai/aegis-grok-desk"
TEMPLATE = "template/grok-bot.json"
ROOT_MANIFEST = "plugin.json"
TEMPLATE_NAME = "Aegis Desk Lead"
STATUSES = ("unpublished", "ready-to-publish", "published")
BOUNDARY = "never places, modifies or cancels orders"
DESCRIPTION_LIMIT = 280
PUBLIC_URL_DOCS = ("README.md", "docs/FAQ.md")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RELEASE_TOKEN_RE = re.compile(r"\brelease (\d+\.\d+\.\d+)\b")
PLAIN_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def load_json(root: str, rel: str, errors: List[str]) -> Optional[Any]:
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        errors.append(f"{rel}: missing")
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{rel}: cannot load JSON: {exc}")
        return None


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_path(name: str) -> str:
    return f"skills/{name}/SKILL.md"


def check_skill_table(root: str, key: str, table: Any, errors: List[str]) -> List[str]:
    """Validate ``skills``/``roleSkills``; return the claimed repository paths."""
    claimed: List[str] = []
    if table is None:
        return claimed
    if not isinstance(table, dict):
        errors.append(f"{TEMPLATE}: {key} must be an object keyed by skill name")
        return claimed
    for name, value in table.items():
        if not isinstance(name, str) or not name:
            errors.append(f"{TEMPLATE}: {key} has an empty skill name")
            continue
        expected_path = skill_path(name)
        if isinstance(value, str):
            digest, path = value, expected_path
        elif isinstance(value, dict):
            digest = value.get("sha256")
            path = value.get("path", expected_path)
            if path != expected_path:
                errors.append(f"{TEMPLATE}: {key}.{name} path must be {expected_path} (found {path!r})")
                continue
        else:
            errors.append(f"{TEMPLATE}: {key}.{name} must be a sha256 string or an object with sha256")
            continue
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append(f"{TEMPLATE}: {key}.{name} sha256 must be a 64-character lowercase hex digest")
            continue
        claimed.append(path)
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            errors.append(f"{TEMPLATE}: {key}.{name} points at a missing file {path}")
        elif sha256(full) != digest:
            errors.append(f"{TEMPLATE}: {key}.{name} sha256 does not match {path}")
    return claimed


def repository_skills(root: str) -> Tuple[List[str], List[str]]:
    """``(role skill names, other skill names)`` for every ``skills/aegis-*/SKILL.md`` in the tree."""
    base = os.path.join(root, "skills")
    roles: List[str] = []
    others: List[str] = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if name.startswith("aegis-") and os.path.isfile(os.path.join(base, name, "SKILL.md")):
                (roles if name.startswith("aegis-role-") else others).append(name)
    return roles, others


def check_coverage(root: str, status: Any, template: Dict[str, Any], errors: List[str]) -> None:
    """A template that is ready to publish or published claims every skill in the tree."""
    if status not in STATUSES or status == "unpublished":
        return
    skills = template.get("skills") if isinstance(template.get("skills"), dict) else {}
    roles = template.get("roleSkills") if isinstance(template.get("roleSkills"), dict) else {}
    role_names, other_names = repository_skills(root)
    for name in other_names:
        if name not in skills:
            errors.append(f"{TEMPLATE}: skills must claim skills/{name}/SKILL.md; a {status} template "
                          "claims every skill in the tree so none ships unhashed")
    for name in role_names:
        if name not in roles:
            errors.append(f"{TEMPLATE}: roleSkills must claim skills/{name}/SKILL.md; a {status} template "
                          "claims every role skill in the tree so none ships unhashed")
    exported = template.get("templateSkillNames")
    if isinstance(exported, list) and not exported:
        errors.append(f"{TEMPLATE}: a {status} template must export at least one skill in templateSkillNames")


def check_public_url(root: str, status: Any, url: Any, errors: List[str]) -> None:
    if status != "published":
        if url is not None:
            errors.append(f"{TEMPLATE}: a {status} template must not carry a publicShareUrl")
        return
    if not isinstance(url, str) or not url.startswith("https://"):
        errors.append(f"{TEMPLATE}: a published template needs an https publicShareUrl")
        return
    for rel in PUBLIC_URL_DOCS:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            errors.append(f"{rel}: missing; the published template URL must appear in it")
            continue
        with open(path, encoding="utf-8") as handle:
            if url not in handle.read():
                errors.append(f"{rel}: published template URL {url} is missing")


def run_git(root: str, args: List[str]) -> Optional["subprocess.CompletedProcess[bytes]"]:
    try:
        return subprocess.run(["git", "-C", root] + args, capture_output=True, check=False)
    except OSError:
        return None


def compare_to_release_tag(root: str, release: Optional[str], claimed: List[str],
                           errors: List[str], skips: List[str]) -> None:
    """Fail when claimed bytes differ from the bytes at the release tag; skip out loud otherwise."""
    if not release:
        return
    tag = f"v{release}"
    inside = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if inside is None:
        skips.append(f"skip: git is unavailable; cannot compare {TEMPLATE} to tag {tag}")
        return
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        skips.append(f"skip: not a git checkout; cannot compare {TEMPLATE} to tag {tag}")
        return
    tagged = run_git(root, ["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"])
    if tagged is None or tagged.returncode != 0:
        skips.append(f"skip: {TEMPLATE} source.release {release} has no local tag {tag}; "
                     "tag comparison not run (expected before the release is tagged)")
        return
    for rel in claimed:
        shown = run_git(root, ["show", f"refs/tags/{tag}:{rel}"])
        if shown is None:
            skips.append(f"skip: git is unavailable; cannot compare {TEMPLATE} to tag {tag}")
            return
        if shown.returncode != 0:
            errors.append(f"{TEMPLATE}: {rel} is missing from tag {tag}")
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue  # reported by the hash check
        with open(path, "rb") as handle:
            if handle.read() != shown.stdout:
                errors.append(f"{TEMPLATE}: {rel} does not match tag {tag}")


def run(root: str) -> Dict[str, Any]:
    root = os.path.abspath(root)
    errors: List[str] = []
    skips: List[str] = []
    manifest = load_json(root, ROOT_MANIFEST, errors)
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not PLAIN_VERSION_RE.fullmatch(version):
        errors.append(f"{ROOT_MANIFEST}: version {version!r} is not a plain X.Y.Z release version")
        version = None

    template = load_json(root, TEMPLATE, errors)
    if not isinstance(template, dict):
        if template is not None:
            errors.append(f"{TEMPLATE}: must be a JSON object")
        return {"ok": False, "errors": errors, "skips": skips, "status": None, "release": version}

    status = template.get("status")
    if status not in STATUSES:
        errors.append(f"{TEMPLATE}: status {status!r} must be one of {list(STATUSES)}")
    if template.get("name") != TEMPLATE_NAME:
        errors.append(f"{TEMPLATE}: name must be '{TEMPLATE_NAME}'")
    if not isinstance(template.get("title"), str) or not template["title"]:
        errors.append(f"{TEMPLATE}: title is required")
    description = template.get("description")
    if not isinstance(description, str) or not description:
        errors.append(f"{TEMPLATE}: description is required")
    else:
        if len(description) > DESCRIPTION_LIMIT:
            errors.append(f"{TEMPLATE}: description is {len(description)} chars (limit {DESCRIPTION_LIMIT})")
        if BOUNDARY not in description:
            errors.append(f"{TEMPLATE}: description must state that the desk {BOUNDARY}")
        for token in RELEASE_TOKEN_RE.findall(description):
            if version is not None and token != version:
                errors.append(f"{TEMPLATE}: description names release {token}, but the manifests declare {version}")

    source = template.get("source")
    release = None
    if not isinstance(source, dict):
        errors.append(f"{TEMPLATE}: source must be an object with repository and release")
    else:
        if source.get("repository") != REPOSITORY:
            errors.append(f"{TEMPLATE}: source.repository must be {REPOSITORY}")
        release = source.get("release")
        if not isinstance(release, str) or not PLAIN_VERSION_RE.fullmatch(release):
            errors.append(f"{TEMPLATE}: source.release {release!r} must be a plain X.Y.Z version")
            release = None
        elif version is not None and release != version:
            errors.append(f"{TEMPLATE}: source.release '{release}' does not match {ROOT_MANIFEST} version '{version}'")

    if "skills" not in template:
        errors.append(f"{TEMPLATE}: skills object is required")
    claimed = check_skill_table(root, "skills", template.get("skills"), errors)
    claimed += check_skill_table(root, "roleSkills", template.get("roleSkills"), errors)

    skill_names = set(template["skills"].keys()) if isinstance(template.get("skills"), dict) else set()
    exported = template.get("templateSkillNames")
    if not isinstance(exported, list) or not all(isinstance(n, str) for n in exported):
        errors.append(f"{TEMPLATE}: templateSkillNames must be a list of skill names")
    else:
        if len(exported) != len(set(exported)):
            errors.append(f"{TEMPLATE}: duplicate templateSkillNames are not allowed")
        unknown = sorted(set(exported) - skill_names)
        if unknown:
            errors.append(f"{TEMPLATE}: templateSkillNames contains unknown skills: {', '.join(unknown)}")

    check_coverage(root, status, template, errors)
    check_public_url(root, status, template.get("publicShareUrl"), errors)
    compare_to_release_tag(root, release, claimed, errors, skips)

    return {
        "ok": not errors,
        "errors": errors,
        "skips": skips,
        "status": status if status in STATUSES else None,
        "release": release,
        "skills_verified": len(claimed),
        "exported": exported if isinstance(exported, list) else [],
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
        for line in result["skips"]:
            print(line)
        print(
            f"ok: Grok Bot template is {result['status']}, release {result['release']}, "
            f"{result['skills_verified']} verified skill file(s), exports {len(result['exported'])}"
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
