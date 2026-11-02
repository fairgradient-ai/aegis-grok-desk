# Aegis Desk — Claude Code guide

Read `AGENTS.md` first; it is the contributor guide for every agent runtime. Development rules:
standard-library Python only in `scripts/` (Python 3.9 syntax floor), tests first, `bash scripts/check.sh`
green before every commit, and the desk never places, modifies or cancels orders.
