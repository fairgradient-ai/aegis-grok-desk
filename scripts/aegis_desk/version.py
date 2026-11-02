"""The release version the tools stamp into their envelopes (``tool_version``).

Standard library only. :func:`release_version` reads ``plugin.json`` at the repository root (two
levels above the ``scripts/aegis_desk/`` package: ``<root>/scripts/aegis_desk/version.py``) and
returns its ``version`` string; when the file is missing, unreadable, not JSON, not an object, or
carries no string ``version``, it returns :data:`FALLBACK_VERSION` (``"0.0.0"``) so a tool still
prints a well-formed envelope instead of failing on its own metadata. The gate
(``check_manifests.py``) is what proves the manifests agree; this helper only reports.
"""

from __future__ import annotations

import json
import os
from typing import Optional

FALLBACK_VERSION: str = "0.0.0"
"""Returned when ``plugin.json`` cannot be read or holds no string ``version``."""

PLUGIN_JSON: str = "plugin.json"


def repository_root() -> str:
    """The checkout root: two directories above the ``aegis_desk`` package directory."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(package_dir))


def plugin_json_path() -> str:
    """``<repository root>/plugin.json``."""
    return os.path.join(repository_root(), PLUGIN_JSON)


def release_version(plugin_path: Optional[str] = None) -> str:
    """The ``version`` of ``plugin.json`` (or of ``plugin_path``), else :data:`FALLBACK_VERSION`."""
    path = plugin_json_path() if plugin_path is None else plugin_path
    try:
        with open(path, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):  # missing, a directory, unreadable, bad UTF-8 or bad JSON
        return FALLBACK_VERSION
    if not isinstance(document, dict):
        return FALLBACK_VERSION
    version = document.get("version")
    if not isinstance(version, str):
        return FALLBACK_VERSION
    version = version.strip()
    return version if version else FALLBACK_VERSION


__all__ = ["FALLBACK_VERSION", "PLUGIN_JSON", "plugin_json_path", "release_version", "repository_root"]
