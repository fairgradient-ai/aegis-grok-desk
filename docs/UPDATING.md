# Update a desk you already set up

Your desk runs from one pinned release: the checkout `/workspace/aegis-grok-desk` sits on a release tag, and the
doctor's check 1 passes only while it does. A newer release changes that checkout and, usually, some of the Bots'
skills or Auto-review texts. Your own files under `/workspace/aegis-desk` (`desk.toml`, `positions.csv`, the adopted
rulebook, `structuring.toml`, `research/calendar.toml`, the journals) are not touched by an update.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Start

Open your Aegis Desk Lead's chat and paste, with the release you want (this page's own release is v0.2.1):

> Update my Aegis desk to v0.2.1 following https://github.com/fairgradient-ai/aegis-grok-desk/blob/v0.2.1/docs/UPDATING.md. Keep the desk read-only and finish with the update receipt.

The Lead does every step below in its own chat and asks you only to tap the cards and to paste any Auto-review text
that changed. Do it outside the routine slots, after the 17:10 ET brief or on a weekend, so no check falls in the
middle of it.

## What the Desk Lead does

Each step ends with evidence: a tool line, a readback, or your confirmation. A step that cannot be done is reported
with its manual step, and the update stops there; the desk keeps running on the release it had.

### 1. Read what changed

Read the target release's `CHANGELOG.md` section and tell the owner, in a few plain lines, what changes for them:
new or changed behaviour, any Auto-review text that changed, any new owner step. Nothing else happens until the
owner says to go on.

### 2. Move the checkout to the new tag

**You:** approve exactly this command line, run as the account that runs the routines. It changes the code every
routine runs, so the Lead runs it only after your go-ahead in step 1 and your tap on its card:

```text
git -C /workspace/aegis-grok-desk fetch --depth 1 origin tag v0.2.1 && git -C /workspace/aegis-grok-desk checkout --quiet --detach v0.2.1
```

- If git refuses because the checkout has local changes, stop: the checkout must stay exactly the release (runtime
  files belong under `/workspace/aegis-desk`). Show the owner `git -C /workspace/aegis-grok-desk status --short`;
  nothing is deleted or reset without the owner's own decision.
- If the tag does not exist, stop and tell the owner.
- If git says the checkout is owned by another user, the fix is SETUP section 9.3 step 4.

Then the doctor's check 1 must read `release v0.2.1 at <commit>, clean`:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
```

### 3. Run the gate

From `/workspace/aegis-grok-desk`, run `bash scripts/check.sh` once. It ends with
`ok: all repository gates passed for /workspace/aegis-grok-desk`; any failure stops the update.

### 4. Refresh the skills

For each Bot, compare every enabled skill's stored body with `skills/<name>/SKILL.md` in the new checkout
(`template/grok-bot.json` lists each file's sha256). Re-save every skill whose body differs, and read it back. Enable
any skill the new agent file lists under `skills:` that the Bot does not have yet (SETUP section 5 lists them), and
disable one the agent file no longer lists. Report one status per skill: `unchanged`, `updated`, `added`, `removed`
or `mismatch` (not ready: the readback differs).

The role skills (`aegis-role-desk-lead`, `aegis-role-exit-sentinel`, `aegis-role-position-control`,
`aegis-role-structuring-desk`) are each Bot's standing instructions: refresh them like any other skill.

### 5. Re-check the Auto-review rules

Print the release's texts:

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --rules-text --desk-root /workspace/aegis-desk
```

Show the owner each text that differs from the one they pasted before (the changelog names them). **You:** in
**Settings, General, Auto-review**, replace that rule with the new text, exactly as printed, and say "done". A rule
that did not change stays as it is.

### 6. Re-check the routines

Compare each Bot's routines with `routines/manifest.toml` of the new release (one routine per slot, each prompt the
manifest's template with the slot filled in):

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --desk-root /workspace/aegis-desk
```

A routine whose prompt or slot changed is edited by its owning Bot and read back; a new slot is created paused, then
unpaused once read back; a removed slot is deleted. With no change, nothing is touched.

### 7. Your desk files

An update never rewrites your files. When the changelog names a new or changed key in `desk.toml` or
`structuring.toml`, or a new intake (for example earnings dates in `research/calendar.toml`), offer the phrase that
adds it ("update the calendar", "set up the structuring desk") and do it only when the owner says so. A file the
owner changes this way is re-acknowledged in the same card (`ack.py config --owner-tapped`).

### 8. The update receipt

One message, opening with two plain lines, for example:

```text
Your Aegis desk is on release v0.2.1: the checkout, the skills and the Auto-review rules match it.
Nothing else is needed now; your positions, rules and settings are unchanged.
```

Then the evidence: the doctor's check 1 line, the `check.sh` result, each skill's status, each Auto-review text
re-pasted or unchanged, each routine changed or unchanged, and anything pending with its manual step. Its last line
is exactly:

```text
Update stayed read-only: no credential was added; no order was created or sent.
```

## Going back

The previous release is still a tag: the same two commands with its tag move the checkout back, and the skills and
Auto-review texts of that release are restored the same way (steps 4 and 5). Your desk files need nothing.

## Release 0.2.1, for a desk set up from an earlier release

- **One front door.** After the update you talk only to the Desk Lead. It enables seven more skills (SETUP section
  5), and the other Bots' role skills change too.
- **One Auto-review text changes:** the owner-authority rule now also names writing `positions.csv`, so saving a row
  is a card instead of a typed "approve". Re-paste it (step 5).
- **Earnings dates.** `research/calendar.toml` can now hold them: say "update the calendar" with each held symbol's
  next earnings date, so a covered call or collar reads a known date instead of counting an unknown one as inside
  every call's life.
- **Your structuring criteria.** If you kept the documented collar window (120 to 210 days) with `earnings` and
  `ex_dividend` set to `exclude`, no collar on a stock that reports earnings can pass. Say "set up the structuring
  desk" to choose `allow` for either; it changes only what the structuring tools accept and silences no alert.
- Routines and your desk files do not change.
