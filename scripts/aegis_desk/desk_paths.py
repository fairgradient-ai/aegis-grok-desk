"""The desk root layout (plan section 5): every path the tools share, derived from one root.

Standard library only; no I/O beyond checking that the root exists and creating the runtime
directories on request. The layout::

    <desk_root>/
      desk.toml                 owner-authored (strict; hashed; never written by routines)
      positions.csv             the book of record (hand-edited; never written by a tool)
      rules/                    exit_rules-X.Y.Z.toml, ADOPTED, CHANGELOG.md
      research/calendar.toml
      state/                    desk_state.json, heartbeat/, acks.jsonl, rule_state.json, .lock
      data/positions/  data/book/  data/cache/
      journal/  levels/  plans/  tickets/  reports/  incidents/
      fixtures/tradier/<set>/   offline recorded responses (fixture desk roots only)

:func:`for_root` builds a :class:`DeskPaths` from an absolute, existing directory (anything else
is a :class:`DeskPathsError`, a ``ValueError``); every attribute is an absolute path under that
root. :func:`ensure_runtime_dirs` creates the directories a tool may need to write into
(:data:`RUNTIME_DIRS`: ``state/``, ``state/heartbeat/``, ``data/cache/``, ``data/book/``,
``data/positions/``, ``journal/``, ``levels/``, ``plans/``, ``reports/``, ``incidents/``) with
``exist_ok`` and nothing else: it never creates ``desk.toml``, ``positions.csv``, ``rules/``,
``research/``, ``tickets/`` or ``fixtures/``, which are the owner's (or the fixture tree's) to
provide. ``plans/`` holds the exit-plan cards ``trigger_levels.py --plan`` writes. The tools pass ``args.desk_root`` from :class:`aegis_desk.contract.ReadOnlyParser`
here, so a tool can never derive a path from anything but the explicit root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Tuple

RUNTIME_DIRS: Tuple[str, ...] = (
    "state", "state/heartbeat", "data/cache", "data/book", "data/positions", "journal", "levels", "plans",
    "reports", "incidents",
)
"""Root-relative directories :func:`ensure_runtime_dirs` creates (in this order), and no others."""

TRADIER_FIXTURES_SUBDIR: str = "tradier"
"""Sub-directory of ``fixtures/`` holding the recorded Tradier sets (``<set>/<endpoint>__<key>.json``)."""


class DeskPathsError(ValueError):
    """The desk root is not an absolute, existing directory."""


@dataclass(frozen=True)
class DeskPaths:
    """Absolute paths of one desk root (module notes); built by :func:`for_root`."""

    root: str
    desk_toml: str
    positions_csv: str
    rules_dir: str
    adopted_file: str
    research_dir: str
    calendar_toml: str
    state_dir: str
    heartbeat_dir: str
    desk_state_json: str
    acks_jsonl: str
    rule_state_json: str
    lock_path: str
    positions_snapshots_dir: str
    book_dir: str
    cache_dir: str
    journal_dir: str
    levels_dir: str
    plans_dir: str
    tickets_dir: str
    reports_dir: str
    incidents_dir: str
    fixtures_dir: str

    @property
    def tradier_fixtures_dir(self) -> str:
        """``fixtures/tradier`` under the root: what an offline transport is pointed at."""
        return os.path.join(self.fixtures_dir, TRADIER_FIXTURES_SUBDIR)

    def relative(self, path: str) -> str:
        """``path`` relative to the root with ``/`` separators, for messages and ledgers."""
        return os.path.relpath(path, self.root).replace(os.sep, "/")


def for_root(desk_root: str) -> DeskPaths:
    """The :class:`DeskPaths` of ``desk_root``, which must be an absolute, existing directory.

    The root is normalised (``os.path.normpath``: trailing separators and ``.``/``..`` segments
    removed) but symbolic links are not resolved, so the paths read back as the owner wrote them.
    """
    if not isinstance(desk_root, str) or not desk_root:
        raise DeskPathsError("desk root must be a non-empty absolute path, got %r" % (desk_root,))
    if not os.path.isabs(desk_root):
        raise DeskPathsError("desk root must be an absolute path, got %r" % (desk_root,))
    root = os.path.normpath(desk_root)
    if not os.path.isdir(root):
        raise DeskPathsError("desk root %s is not an existing directory" % root)

    def under(*parts: str) -> str:
        return os.path.join(root, *parts)

    return DeskPaths(
        root=root,
        desk_toml=under("desk.toml"),
        positions_csv=under("positions.csv"),
        rules_dir=under("rules"),
        adopted_file=under("rules", "ADOPTED"),
        research_dir=under("research"),
        calendar_toml=under("research", "calendar.toml"),
        state_dir=under("state"),
        heartbeat_dir=under("state", "heartbeat"),
        desk_state_json=under("state", "desk_state.json"),
        acks_jsonl=under("state", "acks.jsonl"),
        rule_state_json=under("state", "rule_state.json"),
        lock_path=under("state", ".lock"),
        positions_snapshots_dir=under("data", "positions"),
        book_dir=under("data", "book"),
        cache_dir=under("data", "cache"),
        journal_dir=under("journal"),
        levels_dir=under("levels"),
        plans_dir=under("plans"),
        tickets_dir=under("tickets"),
        reports_dir=under("reports"),
        incidents_dir=under("incidents"),
        fixtures_dir=under("fixtures"),
    )


def ensure_runtime_dirs(paths: DeskPaths) -> List[str]:
    """Create :data:`RUNTIME_DIRS` under the root (``exist_ok``) and return the ones created.

    Never touches ``desk.toml``, ``positions.csv``, ``rules/``, ``research/``, ``tickets/`` or
    ``fixtures/``; existing files and directories are left as they are.
    """
    if not isinstance(paths, DeskPaths):
        raise DeskPathsError("ensure_runtime_dirs() takes a DeskPaths, got %s" % type(paths).__name__)
    created: List[str] = []
    for rel in RUNTIME_DIRS:
        target = os.path.join(paths.root, *rel.split("/"))
        if not os.path.isdir(target):
            os.makedirs(target, exist_ok=True)
            created.append(target)
    return sorted(created)


__all__ = [
    "DeskPaths",
    "DeskPathsError",
    "RUNTIME_DIRS",
    "TRADIER_FIXTURES_SUBDIR",
    "ensure_runtime_dirs",
    "for_root",
]
