"""Gate fixture tool: only its parser matters to the manifest check (read by AST, never imported)."""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book_check")
    parser.add_argument("--post", action="store_true", help="record the run")
    parser.add_argument("--summary", action="store_true", help="always print the pre-close line")
    return parser
