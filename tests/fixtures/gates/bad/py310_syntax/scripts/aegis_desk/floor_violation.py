"""Gate fixture: a match statement needs Python 3.10."""
from __future__ import annotations


def describe(value: int) -> str:
    match value:
        case 0:
            return "zero"
        case _:
            return "other"
