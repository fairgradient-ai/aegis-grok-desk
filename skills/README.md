# Skills

Portable `SKILL.md` skills (`name` and `description` frontmatter, body under 320 lines), all prefixed
`aegis-`. The full set of twenty-one lands with release 0.1.0.

| Skill | What it teaches | Primary users |
| --- | --- | --- |
| [aegis-desk-charter](aegis-desk-charter/SKILL.md) | Roles, seats, artifact ownership, message grammar, UNAVAILABLE semantics | everyone |

Role skills are generated from each agent's `## System prompt` section by `scripts/gen_role_skills.py`:
edit the agent, never a role skill, and run the generator (`check.sh` step 10 fails on drift).

<!-- role-skills:start -->
| Role skill | Generated from | Seat |
| --- | --- | --- |
| [aegis-role-desk-lead](aegis-role-desk-lead/SKILL.md) | `agents/desk-lead.md` | floor |
| [aegis-role-exit-sentinel](aegis-role-exit-sentinel/SKILL.md) | `agents/exit-sentinel.md` | floor |
| [aegis-role-position-control](aegis-role-position-control/SKILL.md) | `agents/position-control.md` | floor |
<!-- role-skills:end -->
