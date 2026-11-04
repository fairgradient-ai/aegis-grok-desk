# Set up Aegis Desk

You are a Grok Bot building the owner's read-only options desk from this repository. Follow this
file top to bottom. Setup is read-only: no brokerage credential is requested, no order is created
or sent. The complete runbook lands with release 0.1.0; this scaffold lists the sections.

## 1. Get the repository onto the desk computer
## 2. Read the desk
## 3. Prepare the desk computer (read-only)
## 4. Create the Stage-1 Bots

| File | Name | Job |
| --- | --- | --- |
| `agents/desk-lead.md` | Aegis Desk Lead | Head of the options desk |
| `agents/exit-sentinel.md` | Aegis Exit Sentinel | Exit-rule monitor of the options desk |
| `agents/position-control.md` | Aegis Position Control | Book-of-record and expiry controller of the options desk |

## 5. Install the skills

Skills to install (Stage 1, scaffold): `aegis-desk-charter`

<!-- role-skills:start -->
Role skills (generated from the agents' System prompts by `scripts/gen_role_skills.py`): `aegis-role-desk-lead`, `aegis-role-exit-sentinel`, `aegis-role-position-control`
<!-- role-skills:end -->

## 6. Create the Aegis Options Floor
## 7. Approvals and the connector check
## 8. Desk configuration, positions and rulebook interviews
## 9. Verify the desk (read-only)
## 10. Return the receipt
