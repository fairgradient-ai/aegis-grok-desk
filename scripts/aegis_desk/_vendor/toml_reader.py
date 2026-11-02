"""Strict TOML 1.0 subset reader for the desk's own files. Standard library only; no float.

Original work written for Aegis Desk (FairGradient) and released under the MIT licence; it is
vendored under ``scripts/aegis_desk/_vendor/`` so that every interpreter the desk may meet
(Python 3.9 and later; ``tomllib`` exists only from 3.11) reads ``desk.toml``, ``limits.toml``,
``rules/exit_rules-X.Y.Z.toml``, ``research/calendar.toml`` and ``routines/manifest.toml``
with the same parser. ``tomllib`` is used by the tests as a cross-check only
(:mod:`aegis_desk.toml_compat`).

    MIT License

    Copyright (c) 2026 FairGradient

    Permission is hereby granted, free of charge, to any person obtaining a copy of this
    software and associated documentation files (the "Software"), to deal in the Software
    without restriction, including without limitation the rights to use, copy, modify, merge,
    publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
    to whom the Software is furnished to do so, subject to the following conditions: The above
    copyright notice and this permission notice shall be included in all copies or substantial
    portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
    COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
    OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
    THE USE OR OTHER DEALINGS IN THE SOFTWARE.

API
---
``loads(text) -> dict`` parses a document; ``load(path) -> dict`` reads a UTF-8 file (a leading
byte-order mark is accepted and dropped). Both raise :class:`TomlError` (a :class:`ValueError`)
whose ``line`` and ``col`` (1-based) point at the offending text and whose ``source`` names the
file when ``load`` raised it; ``str(error)`` reads ``"desk.toml: line 12, column 5: duplicate
key owner.name"``.

Supported (TOML 1.0.0 semantics)
--------------------------------
* comments; bare keys ``[A-Za-z0-9_-]+``, basic ``"quoted"`` and literal ``'quoted'`` keys,
  dotted keys with whitespace around the dots;
* ``[tables]`` and ``[[arrays of tables]]``, including sub-tables of the latest array element;
* basic strings with the standard escapes (``\\b \\t \\n \\f \\r \\" \\\\ \\uXXXX \\UXXXXXXXX``),
  literal strings, and both multi-line forms (first newline trimmed, line-ending backslash,
  up to two quotation marks kept before the closing delimiter);
* integers: decimal with ``_`` separators and sign, ``0x`` / ``0o`` / ``0b`` prefixes; leading
  zeros are an error; no 64-bit range check (Python integers are unbounded);
* floats -> :class:`decimal.Decimal` built from the literal text, never a binary float
  (``cost = 3.42`` is exactly ``Decimal("3.42")``; the exponent form ``1e-3`` is kept as
  written and compares equal to ``Decimal("0.001")``);
* booleans; offset date-times (aware ``datetime.datetime``, ``Z`` or ``+HH:MM``), local
  date-times (naive), local dates (``datetime.date``) and local times (``datetime.time``);
  fractional seconds are truncated to microseconds; ``T``, ``t`` or one space separate date
  and time; seconds are required;
* arrays (nested, multi-line, comments inside, trailing comma allowed, mixed types allowed as
  in TOML 1.0) and single-line inline tables (no trailing comma, as in TOML 1.0).

Strictness
----------
Duplicate keys, a table defined twice, a key used as both value and table, extending an inline
table or an array through a dotted key or header, a header for a table already created by
dotted keys in an earlier section, ``[t]`` after ``[[t]]`` and ``[[t]]`` after ``[t]`` are all
errors, exactly as in ``tomllib``. Control characters (other than tab, and newline where the
grammar allows it) are rejected inside strings, comments and between tokens.

Not supported (rejected with a clear message)
--------------------------------------------
* ``inf`` and ``nan`` floats: the desk requires finite numbers, so they are errors rather than
  non-finite ``Decimal`` values;
* TOML 1.1 extensions: optional seconds, ``\\e`` and ``\\xHH`` escapes, newlines and trailing
  commas inside inline tables;
* anything that is not UTF-8 text.
"""

from __future__ import annotations

import datetime
import re
import string
from decimal import Decimal
from typing import Any

__all__ = ["TomlError", "load", "loads"]

_WS = " \t"
_BARE_KEY_CHARS = frozenset(string.ascii_letters + string.digits + "-_")
_HEX_DIGITS = frozenset(string.hexdigits)
_BASIC_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
# Control characters never allowed in a single-line string or a comment (tab is allowed; the
# newline ends the construct and is reported separately).
_CONTROL_SINGLE_LINE = frozenset(chr(code) for code in range(0x20) if code != 0x09).union({"\x7f"})
# Inside multi-line strings the newline is content; everything else below U+0020 is rejected.
_CONTROL_MULTI_LINE = _CONTROL_SINGLE_LINE - {"\n"}
_NON_FINITE = ("inf", "nan", "+inf", "-inf", "+nan", "-nan")

_INT_HEX_RE = re.compile(r"0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*")
_INT_OCT_RE = re.compile(r"0o[0-7](?:_?[0-7])*")
_INT_BIN_RE = re.compile(r"0b[01](?:_?[01])*")
# Decimal integer or float: an integer part, then an optional fraction and an optional exponent.
_DEC_NUMBER_RE = re.compile(
    r"[+-]?(?:0|[1-9](?:_?[0-9])*)"
    r"(?:\.[0-9](?:_?[0-9])*)?"
    r"(?:[eE][+-]?[0-9](?:_?[0-9])*)?"
)
_DATETIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"
    r"(?:[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:\d{2})?)?"
)
_LOCAL_TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?")
_UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9


class TomlError(ValueError):
    """A document that is not valid under this reader's TOML subset.

    ``line`` and ``col`` are 1-based positions in the text (``None`` for whole-file problems
    such as an encoding error); ``source`` is the file name when :func:`load` raised.
    """

    def __init__(
        self,
        message: str,
        line: int | None = None,
        col: int | None = None,
        source: str | None = None,
    ) -> None:
        self.message = message
        self.line = line
        self.col = col
        self.source = source
        super().__init__(self._render())

    def _render(self) -> str:
        where: list[str] = []
        if self.source is not None:
            where.append(str(self.source))
        if self.line is not None:
            position = "line %d" % self.line
            if self.col is not None:
                position += ", column %d" % self.col
            where.append(position)
        prefix = ": ".join(where)
        return prefix + ": " + self.message if prefix else self.message


def _dotted(path: tuple[str, ...]) -> str:
    """Render a key path for messages; parts that are not bare keys are quoted."""
    parts = []
    for part in path:
        if part and all(ch in _BARE_KEY_CHARS for ch in part):
            parts.append(part)
        else:
            parts.append('"' + part.replace("\\", "\\\\").replace('"', '\\"') + '"')
    return ".".join(parts) if parts else "(root)"


class _Namespace:
    """Definition flags for one scope (the document, or one inline table).

    ``explicit`` holds paths defined by a ``[table]`` or ``[[array]]`` header; ``frozen`` holds
    inline tables and arrays (closed to later extension); ``dotted`` holds tables implied by
    dotted keys in sections that have ended, ``pending`` the same for the open section (they
    become ``dotted`` at the next header). A path is frozen when it or any prefix is frozen.
    """

    __slots__ = ("explicit", "frozen", "dotted", "pending")

    def __init__(self) -> None:
        self.explicit: set[tuple[str, ...]] = set()
        self.frozen: set[tuple[str, ...]] = set()
        self.dotted: set[tuple[str, ...]] = set()
        self.pending: set[tuple[str, ...]] = set()

    def frozen_prefix(self, path: tuple[str, ...]) -> tuple[str, ...] | None:
        for length in range(1, len(path) + 1):
            if path[:length] in self.frozen:
                return path[:length]
        return None

    def clear_under(self, path: tuple[str, ...]) -> None:
        width = len(path)
        for flags in (self.explicit, self.frozen, self.dotted, self.pending):
            for entry in [entry for entry in flags if entry[:width] == path]:
                flags.discard(entry)

    def seal(self) -> None:
        self.dotted |= self.pending
        self.pending.clear()


class _Parser:
    def __init__(self, text: str) -> None:
        self.src = text.replace("\r\n", "\n")
        self.n = len(self.src)
        self.pos = 0
        self.root: dict[str, Any] = {}
        self.table: dict[str, Any] = self.root
        self.header: tuple[str, ...] = ()
        self.ns = _Namespace()

    # -- diagnostics -----------------------------------------------------------------------
    def error(self, message: str, pos: int | None = None) -> TomlError:
        at = self.pos if pos is None else pos
        at = min(at, self.n)
        line = self.src.count("\n", 0, at) + 1
        col = at - self.src.rfind("\n", 0, at)
        return TomlError(message, line, col)

    def _describe_here(self) -> str:
        if self.pos >= self.n:
            return "end of document"
        ch = self.src[self.pos]
        if ch == "\n":
            return "end of line"
        if ch in _CONTROL_SINGLE_LINE:
            return "control character U+%04X" % ord(ch)
        return repr(ch)

    # -- whitespace and comments ------------------------------------------------------------
    def skip_ws(self) -> None:
        while self.pos < self.n and self.src[self.pos] in _WS:
            self.pos += 1

    def skip_comment(self) -> None:
        if self.pos < self.n and self.src[self.pos] == "#":
            while self.pos < self.n and self.src[self.pos] != "\n":
                if self.src[self.pos] in _CONTROL_SINGLE_LINE:
                    raise self.error(
                        "control character U+%04X in a comment" % ord(self.src[self.pos])
                    )
                self.pos += 1

    def skip_blank(self) -> None:
        """Whitespace, comments and newlines between statements or inside arrays."""
        while True:
            self.skip_ws()
            self.skip_comment()
            if self.pos < self.n and self.src[self.pos] == "\n":
                self.pos += 1
                continue
            return

    def end_statement(self) -> None:
        self.skip_ws()
        self.skip_comment()
        if self.pos >= self.n:
            return
        if self.src[self.pos] == "\n":
            self.pos += 1
            return
        raise self.error(
            "expected a newline or the end of the document after the statement, found %s"
            % self._describe_here()
        )

    # -- document --------------------------------------------------------------------------
    def parse_document(self) -> dict[str, Any]:
        while True:
            self.skip_blank()
            if self.pos >= self.n:
                return self.root
            if self.src[self.pos] == "[":
                self.parse_header()
            else:
                self.parse_key_value(self.table, self.header, self.ns)
            self.end_statement()

    def parse_header(self) -> None:
        start = self.pos
        is_array = self.src.startswith("[[", self.pos)
        self.pos += 2 if is_array else 1
        self.skip_ws()
        key = self.parse_key()
        self.skip_ws()
        closer = "]]" if is_array else "]"
        if not self.src.startswith(closer, self.pos):
            raise self.error(
                "expected '%s' to close the table header, found %s"
                % (closer, self._describe_here())
            )
        self.pos += len(closer)
        self.ns.seal()
        if is_array:
            self.open_array_of_tables(key, start)
        else:
            self.open_table(key, start)

    def open_table(self, key: tuple[str, ...], start: int) -> None:
        if key in self.ns.explicit:
            raise self.error("table %s is defined twice" % _dotted(key), start)
        if key in self.ns.dotted:
            raise self.error(
                "table %s was already created by dotted keys and cannot be reopened "
                "with a header" % _dotted(key),
                start,
            )
        parent = self.descend(self.root, key[:-1], (), self.ns, start)
        name = key[-1]
        frozen = self.ns.frozen_prefix(key)
        if frozen is not None:
            raise self.error(
                "%s is an inline table or an array and cannot be extended by a header"
                % _dotted(frozen),
                start,
            )
        if name not in parent:
            parent[name] = {}
        value = parent[name]
        if isinstance(value, list):
            raise self.error(
                "%s is an array of tables; use [[%s]] to add an element"
                % (_dotted(key), _dotted(key)),
                start,
            )
        if not isinstance(value, dict):
            raise self.error(
                "%s already holds a value and cannot become a table" % _dotted(key), start
            )
        self.ns.explicit.add(key)
        self.table = value
        self.header = key

    def open_array_of_tables(self, key: tuple[str, ...], start: int) -> None:
        parent = self.descend(self.root, key[:-1], (), self.ns, start)
        name = key[-1]
        frozen = self.ns.frozen_prefix(key)
        if frozen is not None:
            raise self.error(
                "%s is an inline table or an array value and cannot be extended by "
                "[[%s]]" % (_dotted(frozen), _dotted(key)),
                start,
            )
        if name not in parent:
            parent[name] = [{}]
        else:
            existing = parent[name]
            if isinstance(existing, list):
                existing.append({})
            elif isinstance(existing, dict):
                raise self.error(
                    "%s is already defined as a table; [[%s]] would redefine it as an "
                    "array of tables" % (_dotted(key), _dotted(key)),
                    start,
                )
            else:
                raise self.error(
                    "%s already holds a value and cannot become an array of tables" % _dotted(key),
                    start,
                )
        self.ns.clear_under(key)
        self.ns.explicit.add(key)
        self.table = parent[name][-1]
        self.header = key

    def descend(
        self,
        node: dict[str, Any],
        parts: tuple[str, ...],
        base: tuple[str, ...],
        ns: _Namespace,
        start: int,
    ) -> dict[str, Any]:
        """Walk ``parts`` below ``node`` (at path ``base``), creating missing tables.

        An intermediate array of tables resolves to its latest element; a frozen inline table
        or array value, or a scalar, cannot be walked into.
        """
        for index, part in enumerate(parts):
            path = base + parts[: index + 1]
            if path in ns.frozen:
                raise self.error(
                    "%s is an inline table or an array and cannot be extended" % _dotted(path),
                    start,
                )
            if part not in node:
                node[part] = {}
                node = node[part]
                continue
            value = node[part]
            if isinstance(value, dict):
                node = value
            elif isinstance(value, list) and value and isinstance(value[-1], dict):
                node = value[-1]
            else:
                raise self.error("%s already holds a value, not a table" % _dotted(path), start)
        return node

    # -- key/value pairs -------------------------------------------------------------------
    def parse_key_value(self, table: dict[str, Any], base: tuple[str, ...], ns: _Namespace) -> None:
        start = self.pos
        key = self.parse_key()
        self.skip_ws()
        if self.pos >= self.n or self.src[self.pos] != "=":
            raise self.error(
                "expected '=' after key %s, found %s" % (_dotted(key), self._describe_here())
            )
        self.pos += 1
        self.skip_ws()
        value = self.parse_value()
        self.assign(table, base, key, value, ns, start)

    def assign(
        self,
        table: dict[str, Any],
        base: tuple[str, ...],
        key: tuple[str, ...],
        value: Any,
        ns: _Namespace,
        start: int,
    ) -> None:
        for index in range(1, len(key)):
            prefix = base + key[:index]
            if prefix in ns.explicit:
                raise self.error(
                    "dotted key %s cannot extend table %s, which a header defined"
                    % (_dotted(base + key), _dotted(prefix)),
                    start,
                )
            ns.pending.add(prefix)
        parent = self.descend(table, key[:-1], base, ns, start)
        name = key[-1]
        if name in parent:
            raise self.error("duplicate key %s" % _dotted(base + key), start)
        if isinstance(value, (dict, list)):
            ns.frozen.add(base + key)
        parent[name] = value

    def parse_key(self) -> tuple[str, ...]:
        parts = [self.parse_simple_key()]
        while True:
            self.skip_ws()
            if self.pos < self.n and self.src[self.pos] == ".":
                self.pos += 1
                self.skip_ws()
                parts.append(self.parse_simple_key())
                continue
            return tuple(parts)

    def parse_simple_key(self) -> str:
        if self.pos >= self.n:
            raise self.error("expected a key, found end of document")
        ch = self.src[self.pos]
        if ch == '"':
            return self.parse_basic_string()
        if ch == "'":
            return self.parse_literal_string()
        start = self.pos
        while self.pos < self.n and self.src[self.pos] in _BARE_KEY_CHARS:
            self.pos += 1
        if self.pos == start:
            raise self.error(
                "expected a key (bare keys use A-Z a-z 0-9 _ -; quote anything else), "
                "found %s" % self._describe_here()
            )
        return self.src[start : self.pos]

    # -- values ----------------------------------------------------------------------------
    def parse_value(self) -> Any:
        if self.pos >= self.n:
            raise self.error("expected a value, found end of document")
        ch = self.src[self.pos]
        if ch == '"':
            if self.src.startswith('"""', self.pos):
                return self.parse_multiline_string('"')
            return self.parse_basic_string()
        if ch == "'":
            if self.src.startswith("'''", self.pos):
                return self.parse_multiline_string("'")
            return self.parse_literal_string()
        if ch == "[":
            return self.parse_array()
        if ch == "{":
            return self.parse_inline_table()
        if self.src.startswith("true", self.pos):
            self.pos += 4
            return True
        if self.src.startswith("false", self.pos):
            self.pos += 5
            return False
        for literal in _NON_FINITE:
            if self.src.startswith(literal, self.pos):
                raise self.error(
                    "non-finite number %r is not supported: the desk requires finite values"
                    % literal
                )
        if ch in "0123456789+-":
            return self.parse_number_or_datetime()
        raise self.error("expected a value, found %s" % self._describe_here())

    def parse_number_or_datetime(self) -> Any:
        start = self.pos
        match = _DATETIME_RE.match(self.src, self.pos)
        if match:
            self.pos = match.end()
            self.check_literal_end("date-time")
            return self.build_datetime(match, start)
        match = _LOCAL_TIME_RE.match(self.src, self.pos)
        if match:
            self.pos = match.end()
            self.check_literal_end("time")
            return self.build_time(
                match.group(1), match.group(2), match.group(3), match.group(4), start
            )
        for regex, base in ((_INT_HEX_RE, 16), (_INT_OCT_RE, 8), (_INT_BIN_RE, 2)):
            match = regex.match(self.src, self.pos)
            if match:
                self.pos = match.end()
                self.check_literal_end("integer")
                return int(match.group()[2:].replace("_", ""), base)
        match = _DEC_NUMBER_RE.match(self.src, self.pos)
        if match:
            text = match.group()
            self.pos = match.end()
            self.check_literal_end("number")
            cleaned = text.replace("_", "")
            if "." in text or "e" in text or "E" in text:
                return Decimal(cleaned)
            return int(cleaned)
        raise self.error("expected a value, found %s" % self._describe_here())

    def check_literal_end(self, what: str) -> None:
        if self.pos < self.n and (
            self.src[self.pos] in _BARE_KEY_CHARS or self.src[self.pos] in ".:"
        ):
            raise self.error(
                "invalid %s literal: unexpected %s (leading zeros, trailing dots, "
                "doubled underscores and missing seconds are not allowed)"
                % (what, self._describe_here())
            )

    def build_datetime(self, match: re.Match[str], start: int) -> Any:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            date = datetime.date(year, month, day)
        except ValueError as exc:
            raise self.error("invalid date %s: %s" % (match.group()[:10], exc), start) from None
        if match.group(4) is None:
            return date
        time = self.build_time(
            match.group(4), match.group(5), match.group(6), match.group(7), start
        )
        offset_text = match.group(8)
        if offset_text is None:
            return datetime.datetime.combine(date, time)
        if offset_text in ("Z", "z"):
            tz = _UTC
        else:
            sign = 1 if offset_text[0] == "+" else -1
            hours, minutes = int(offset_text[1:3]), int(offset_text[4:6])
            if hours > 23 or minutes > 59:
                raise self.error("invalid UTC offset %s" % offset_text, start)
            tz = datetime.timezone(sign * datetime.timedelta(hours=hours, minutes=minutes))
        return datetime.datetime.combine(date, time).replace(tzinfo=tz)

    def build_time(
        self, hour: str, minute: str, second: str, fraction: str | None, start: int
    ) -> datetime.time:
        micro = int(fraction.ljust(6, "0")[:6]) if fraction else 0
        try:
            return datetime.time(int(hour), int(minute), int(second), micro)
        except ValueError as exc:
            raise self.error(
                "invalid time %s:%s:%s: %s" % (hour, minute, second, exc), start
            ) from None

    # -- strings ---------------------------------------------------------------------------
    def parse_basic_string(self) -> str:
        start = self.pos
        self.pos += 1
        chunks: list[str] = []
        while True:
            if self.pos >= self.n:
                raise self.error("unterminated string", start)
            ch = self.src[self.pos]
            if ch == '"':
                self.pos += 1
                return "".join(chunks)
            if ch == "\\":
                self.pos += 1
                chunks.append(self.parse_escape(multiline=False))
                continue
            if ch == "\n":
                raise self.error("newline inside a single-line string (use a multi-line string)")
            if ch in _CONTROL_SINGLE_LINE:
                raise self.error("control character U+%04X in a string" % ord(ch))
            chunks.append(ch)
            self.pos += 1

    def parse_literal_string(self) -> str:
        start = self.pos
        self.pos += 1
        end = self.pos
        while True:
            if end >= self.n:
                raise self.error("unterminated literal string", start)
            ch = self.src[end]
            if ch == "'":
                text = self.src[self.pos : end]
                self.pos = end + 1
                return text
            if ch == "\n":
                raise self.error(
                    "newline inside a single-line literal string (use a multi-line string)", end
                )
            if ch in _CONTROL_SINGLE_LINE:
                raise self.error("control character U+%04X in a literal string" % ord(ch), end)
            end += 1

    def parse_multiline_string(self, quote: str) -> str:
        start = self.pos
        delimiter = quote * 3
        self.pos += 3
        if self.pos < self.n and self.src[self.pos] == "\n":
            self.pos += 1  # a newline immediately after the opening delimiter is trimmed
        chunks: list[str] = []
        while True:
            if self.pos >= self.n:
                raise self.error("unterminated multi-line string", start)
            ch = self.src[self.pos]
            if ch == quote:
                run = 0
                while self.pos + run < self.n and self.src[self.pos + run] == quote:
                    run += 1
                if run >= 3:
                    if run > 5:
                        raise self.error(
                            "at most two %s characters may precede the closing %s"
                            % (quote, delimiter)
                        )
                    chunks.append(quote * (run - 3))
                    self.pos += run
                    return "".join(chunks)
                chunks.append(quote * run)
                self.pos += run
                continue
            if ch == "\\" and quote == '"':
                self.pos += 1
                chunks.append(self.parse_escape(multiline=True))
                continue
            if ch in _CONTROL_MULTI_LINE:
                raise self.error("control character U+%04X in a multi-line string" % ord(ch))
            chunks.append(ch)
            self.pos += 1

    def parse_escape(self, multiline: bool) -> str:
        """``self.pos`` is just past the backslash; returns the decoded text."""
        if self.pos >= self.n:
            raise self.error("unterminated escape sequence")
        ch = self.src[self.pos]
        if ch in _BASIC_ESCAPES:
            self.pos += 1
            return _BASIC_ESCAPES[ch]
        if ch == "u":
            return self.parse_unicode_escape(4)
        if ch == "U":
            return self.parse_unicode_escape(8)
        if multiline and ch in " \t\n":
            probe = self.pos
            while probe < self.n and self.src[probe] in _WS:
                probe += 1
            if probe < self.n and self.src[probe] == "\n":
                probe += 1
                while probe < self.n and self.src[probe] in " \t\n":
                    probe += 1
                self.pos = probe
                return ""
            raise self.error(
                "a backslash must start an escape sequence or end the line", self.pos - 1
            )
        raise self.error(
            "invalid escape sequence '\\%s' (allowed: \\b \\t \\n \\f \\r \\\" \\\\ \\uXXXX "
            "\\UXXXXXXXX)" % ch,
            self.pos - 1,
        )

    def parse_unicode_escape(self, width: int) -> str:
        digits = self.src[self.pos + 1 : self.pos + 1 + width]
        if len(digits) != width or any(d not in _HEX_DIGITS for d in digits):
            raise self.error(
                "\\%s escape needs exactly %d hexadecimal digits" % (self.src[self.pos], width),
                self.pos - 1,
            )
        code = int(digits, 16)
        if 0xD800 <= code <= 0xDFFF or code > 0x10FFFF:
            raise self.error(
                "\\%s%s is not a Unicode scalar value" % (self.src[self.pos], digits), self.pos - 1
            )
        self.pos += 1 + width
        return chr(code)

    # -- containers ------------------------------------------------------------------------
    def parse_array(self) -> list[Any]:
        start = self.pos
        self.pos += 1
        items: list[Any] = []
        while True:
            self.skip_blank()
            if self.pos >= self.n:
                raise self.error("unterminated array", start)
            if self.src[self.pos] == "]":
                self.pos += 1
                return items
            items.append(self.parse_value())
            self.skip_blank()
            if self.pos >= self.n:
                raise self.error("unterminated array", start)
            ch = self.src[self.pos]
            if ch == ",":
                self.pos += 1
                continue
            if ch == "]":
                self.pos += 1
                return items
            raise self.error("expected ',' or ']' in array, found %s" % self._describe_here())

    def parse_inline_table(self) -> dict[str, Any]:
        start = self.pos
        self.pos += 1
        table: dict[str, Any] = {}
        ns = _Namespace()
        self.skip_ws()
        if self.pos < self.n and self.src[self.pos] == "}":
            self.pos += 1
            return table
        while True:
            self.skip_ws()
            if self.pos >= self.n:
                raise self.error("unterminated inline table", start)
            ch = self.src[self.pos]
            if ch == "\n":
                raise self.error(
                    "newline inside an inline table (inline tables are single-line in TOML 1.0)"
                )
            if ch == "}":
                raise self.error("trailing comma in an inline table is not allowed in TOML 1.0")
            self.parse_key_value(table, (), ns)
            self.skip_ws()
            if self.pos >= self.n:
                raise self.error("unterminated inline table", start)
            ch = self.src[self.pos]
            if ch == ",":
                self.pos += 1
                continue
            if ch == "}":
                self.pos += 1
                return table
            if ch == "\n":
                raise self.error(
                    "newline inside an inline table (inline tables are single-line in TOML 1.0)"
                )
            raise self.error(
                "expected ',' or '}' in inline table, found %s" % self._describe_here()
            )


def loads(text: str) -> dict[str, Any]:
    """Parse a TOML document (module notes) into nested ``dict``/``list`` values."""
    if not isinstance(text, str):
        raise TypeError("loads() takes str, got %s" % type(text).__name__)
    return _Parser(text).parse_document()


def load(path: Any) -> dict[str, Any]:
    """Read ``path`` as UTF-8 (a BOM is dropped) and parse it; errors name the file."""
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TomlError("file is not valid UTF-8: %s" % exc, source=str(path)) from None
    try:
        return loads(text)
    except TomlError as exc:
        raise TomlError(exc.message, exc.line, exc.col, source=str(path)) from None
