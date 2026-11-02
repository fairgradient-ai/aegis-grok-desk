"""Gate fixture: integer flag unions are valid Python 3.9 and must pass the floor check."""
from __future__ import annotations

import fcntl
import os
import re

HEADING_RE_FLAGS = re.MULTILINE | re.IGNORECASE
LOCK_FLAGS = fcntl.LOCK_EX | fcntl.LOCK_NB
APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT


def open_append(path: str, flags: int = os.O_WRONLY | os.O_APPEND) -> int:
    return os.open(path, flags, 0o600)
