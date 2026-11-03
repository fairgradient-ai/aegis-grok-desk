"""Materialise gate fixture trees for the tests under ``tests/gates``.

``tests/fixtures/gates/good`` is a minimal tree in the shape of this repository that passes
every gate. ``tests/fixtures/gates/bad/<case>/`` holds overlays: the files that make one thing
wrong. A case is materialised by copying the good tree into a temporary directory, copying the
overlay on top, and deleting every path listed in the overlay's optional ``_remove.txt``. The
checkers are then run against that directory, so each failure is attributable to exactly the
overlay's change.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from typing import Iterator, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
CHECK_SH = os.path.join(SCRIPTS, "check.sh")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "gates")
GOOD = os.path.join(FIXTURES, "good")
BAD = os.path.join(FIXTURES, "bad")
REMOVE_MANIFEST = "_remove.txt"


def bad_cases() -> List[str]:
    return sorted(name for name in os.listdir(BAD) if os.path.isdir(os.path.join(BAD, name)))


def apply_overlay(root: str, case: str) -> None:
    overlay = os.path.join(BAD, case)
    if not os.path.isdir(overlay):
        raise FileNotFoundError(f"no bad fixture named {case!r} under {BAD}")
    for dirpath, _dirnames, filenames in os.walk(overlay):
        for name in filenames:
            source = os.path.join(dirpath, name)
            rel = os.path.relpath(source, overlay)
            if rel == REMOVE_MANIFEST:
                continue
            target = os.path.join(root, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
    removals = os.path.join(overlay, REMOVE_MANIFEST)
    if os.path.isfile(removals):
        with open(removals, encoding="utf-8") as handle:
            for line in handle:
                rel = line.strip()
                if rel and not rel.startswith("#"):
                    os.remove(os.path.join(root, rel))


@contextlib.contextmanager
def fixture_tree(case: Optional[str] = None) -> Iterator[str]:
    """Yield a temporary copy of the good tree, with ``case``'s overlay applied when given."""
    with tempfile.TemporaryDirectory(prefix="aegis-gates-") as temporary:
        root = os.path.join(temporary, "tree")
        shutil.copytree(GOOD, root)
        if case is not None:
            apply_overlay(root, case)
        yield root


def write(root: str, rel: str, content: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()
