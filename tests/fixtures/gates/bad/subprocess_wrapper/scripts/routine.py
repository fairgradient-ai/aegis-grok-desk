"""Gate fixture: the routine entry point launders a command through check_grok_template.run_git."""
from __future__ import annotations

import check_grok_template


def status(root: str) -> object:
    return check_grok_template.run_git(root, ["status"])
