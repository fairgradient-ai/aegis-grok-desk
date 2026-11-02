"""Gate fixture: a runtime X | Y union in isinstance needs Python 3.10."""
from __future__ import annotations


def is_number(value: object) -> bool:
    return isinstance(value, int | float)
