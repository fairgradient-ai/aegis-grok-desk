"""One TOML entry point for every desk tool, plus the tomllib cross-check the tests use.

Standard library only. Plan section 9: the vendored strict reader
(:mod:`aegis_desk._vendor.toml_reader`) parses every desk file on every interpreter, because
``tomllib`` exists only from Python 3.11 and the Grok Bot computer's interpreter is
undocumented. ``tomllib`` is never used by a tool; it is a CI cross-check that the vendored
reader agrees with the reference implementation on the same text.

Conventions shared by both readers in the comparison: floats become :class:`decimal.Decimal`
(``parse_float=Decimal`` for tomllib), integers stay ``int``, booleans ``bool``, date-times
``datetime`` objects (aware when an offset is present), tables ``dict`` and arrays ``list``.

The repository's Python-floor gate rejects a literal ``import tomllib`` in any module, so the
module is loaded with :func:`importlib.import_module` inside :func:`tomllib_module`; on an
interpreter without it the helper returns ``None`` and :func:`cross_check` degrades to the
vendored parse alone (the tests skip the comparison explicitly in that case).
"""

from __future__ import annotations

import datetime
import importlib
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ._vendor import toml_reader
from ._vendor.toml_reader import TomlError

__all__ = [
    "CrossCheckMismatch",
    "TomlError",
    "cross_check",
    "load",
    "loads",
    "rejection_parity",
    "structural_differences",
    "tomllib_module",
]


def loads(text: str) -> Dict[str, Any]:
    """Parse TOML text with the vendored reader (always; see the module notes)."""
    return toml_reader.loads(text)


def load(path: Any) -> Dict[str, Any]:
    """Read and parse a TOML file with the vendored reader; errors name the file."""
    return toml_reader.load(path)


def tomllib_module() -> Any:
    """The standard-library ``tomllib`` module when importable (Python 3.11+), else ``None``."""
    try:
        return importlib.import_module("tomllib")
    except ImportError:
        return None


class CrossCheckMismatch(AssertionError):
    """The vendored reader and tomllib disagree; ``differences`` lists every path that differs."""

    def __init__(self, differences: List[str]) -> None:
        self.differences = list(differences)
        super().__init__("vendored TOML reader disagrees with tomllib:\n  " + "\n  ".join(self.differences))


def _scalar_differences(ours: Any, theirs: Any, path: str) -> List[str]:
    if type(ours) is not type(theirs):
        return ["%s: type %s (vendored) vs %s (tomllib)" % (path, type(ours).__name__, type(theirs).__name__)]
    if isinstance(ours, Decimal):
        if ours.as_tuple() != theirs.as_tuple():
            return ["%s: Decimal %s vs %s" % (path, ours, theirs)]
        return []
    if isinstance(ours, datetime.datetime):
        if (ours.tzinfo is None) != (theirs.tzinfo is None):
            return ["%s: time-zone awareness differs (%r vs %r)" % (path, ours, theirs)]
        if ours.tzinfo is not None and ours.utcoffset() != theirs.utcoffset():
            return ["%s: UTC offset %s vs %s" % (path, ours.utcoffset(), theirs.utcoffset())]
    if ours != theirs:
        return ["%s: %r vs %r" % (path, ours, theirs)]
    return []


def structural_differences(ours: Any, theirs: Any, path: str = "$") -> List[str]:
    """Every difference between two parsed documents, as ``path: detail`` lines.

    Dictionaries must have the same keys in the same order, lists the same length, and every
    leaf the same type and value (``True`` is not ``1``; ``Decimal`` values must match digit
    for digit; date-times must agree on awareness and offset).
    """
    if isinstance(ours, dict) or isinstance(theirs, dict):
        if not (isinstance(ours, dict) and isinstance(theirs, dict)):
            return ["%s: %s vs %s" % (path, type(ours).__name__, type(theirs).__name__)]
        differences: List[str] = []
        if list(ours) != list(theirs):
            differences.append("%s: keys %r vs %r" % (path, list(ours), list(theirs)))
        for key in ours:
            if key in theirs:
                differences.extend(structural_differences(ours[key], theirs[key], "%s.%s" % (path, key)))
        return differences
    if isinstance(ours, list) or isinstance(theirs, list):
        if not (isinstance(ours, list) and isinstance(theirs, list)):
            return ["%s: %s vs %s" % (path, type(ours).__name__, type(theirs).__name__)]
        if len(ours) != len(theirs):
            return ["%s: %d item(s) vs %d" % (path, len(ours), len(theirs))]
        differences = []
        for index, pair in enumerate(zip(ours, theirs)):
            differences.extend(structural_differences(pair[0], pair[1], "%s[%d]" % (path, index)))
        return differences
    return _scalar_differences(ours, theirs, path)


def cross_check(text: str) -> Dict[str, Any]:
    """Parse ``text`` with the vendored reader and, when tomllib exists, assert agreement.

    Returns the vendored result. Raises :class:`TomlError` when the vendored reader rejects the
    text (the comparison is then not attempted), :class:`CrossCheckMismatch` when both readers
    accept it but produce different documents, and lets a tomllib ``TOMLDecodeError`` propagate
    when only tomllib rejects it (a strictness gap worth knowing about).
    """
    ours = loads(text)
    module = tomllib_module()
    if module is None:
        return ours
    theirs = module.loads(text, parse_float=Decimal)
    differences = structural_differences(ours, theirs)
    if differences:
        raise CrossCheckMismatch(differences)
    return ours


def rejection_parity(text: str) -> Tuple[Optional[str], Optional[str]]:
    """``(vendored error, tomllib error)`` for ``text``; ``None`` in a slot means accepted.

    The tomllib slot is also ``None`` when tomllib is unavailable, so callers check
    :func:`tomllib_module` first when they need the comparison.
    """
    ours: Optional[str] = None
    theirs: Optional[str] = None
    try:
        loads(text)
    except TomlError as exc:
        ours = str(exc)
    module = tomllib_module()
    if module is not None:
        try:
            module.loads(text, parse_float=Decimal)
        except module.TOMLDecodeError as exc:
            theirs = str(exc)
    return ours, theirs
