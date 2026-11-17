# Update a desk you already set up

Your desk runs from one pinned release: the checkout `/workspace/aegis-grok-desk` sits on a release tag, and the
doctor's check 1 passes only while it does. A newer release changes that checkout and, usually, some of the Bots'
skills or Auto-review texts. Your own files under `/workspace/aegis-desk` (`desk.toml`, `positions.csv`, the adopted
rulebook, `structuring.toml`, `research/calendar.toml`, the journals) are not touched by an update.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Start

Open your Aegis Desk Lead's chat and paste, with the release you want (this page's own release is v1.0.0):

> Update my Aegis desk to v1.0.0 following https://github.com/fairgradient-ai/aegis-grok-desk/blob/v1.0.0/docs/UPDATING.md. Keep the desk read-only and finish with the update receipt.

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
git -C /workspace/aegis-grok-desk fetch --depth 1 origin tag v1.0.0 && git -C /workspace/aegis-grok-desk checkout --quiet --detach v1.0.0
```

- If git refuses because the checkout has local changes, stop: the checkout must stay exactly the release (runtime
  files belong under `/workspace/aegis-desk`). Show the owner `git -C /workspace/aegis-grok-desk status --short`;
  nothing is deleted or reset without the owner's own decision.
- If the tag does not exist, stop and tell the owner.
- If git says the checkout is owned by another user, the fix is SETUP section 9.3 step 4.

Then the doctor's check 1 must read `release v1.0.0 at <commit>, clean`:

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

The role skills (`aegis-role-<the agent file's name>`, one per Bot; SETUP section 5 lists them) are each Bot's
standing instructions: refresh them like any other skill.

**A new Bot.** When SETUP section 4 lists an agent file the desk has no Bot for, create that Bot first, with the Name
and Job of that table, and read them back. Enable its skills and its role skill as above. A seat whose agent file
says `seat: floor` joins the Aegis Options Floor (read back its members); a `seat: off-floor` seat stays in its own
chat. Set the Timezone America/New_York and notifications on for each new Bot that owns a routine (SETUP section
9.3), and read them back. Its routines are created in step 6.

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

When the changelog names a new objective for `structuring.toml`, ask the owner about it as part of this step. For each
section of their file, show the objective it uses now next to every objective that section accepts in the new
release, each with its words from `docs/STRUCTURING.md` ("The objectives"), and ask whether to keep or switch. Keeping
is the default and changes nothing. Only on a switch, write full answers with the chosen objectives and run, after the
owner's tap, `desk_init.py --answers ... --replace --owner-tapped && ack.py config --owner-tapped`. The receipt names
each section's objective and whether it changed.

### 8. The update receipt

One message, opening with two plain lines, for example:

```text
Your Aegis desk is on release v1.0.0: the checkout, the skills and the Auto-review rules match it.
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

## Release 1.0.0, for a desk set up from release 0.2.1

- **Four new Bots** (step 4). The Aegis Book & Risk Analyst and the Aegis Events and Calendar Analyst join the Floor,
  which is then full at six. The Aegis Rules Steward and the Aegis Review Desk answer by direct message only.
- **Nine new routines** (step 6), fifteen in all: the opening marks, the pre-event and weekend stress, the Monday
  calendar build and event sheet, the journal close, the rules sanity, the Friday rulebook memo and the Saturday
  weekly review. A week is 66 runs in the lean profile and 86 in the active one (41 and 61 before).
- **One Auto-review text changes** (step 5): rule 1, the Allow rule, now names the fifteen routine ids. Replace it
  with the printed text; rules 2 and 3 are unchanged.
- **The American model of record.** Implied volatilities, Greeks and the structuring prices are computed on the
  American model, so some figures move; a leg near its early-exercise boundary says so.
- **Your files keep their bytes.** New optional keys: each account in `desk.toml` may state its own
  `approval_level` and its `cash` with `cash_as_of`; a structuring section may set `expirations = "weekly"`.
  Three `desk.toml` values (`engagement`, `chatty_or_quiet`, `attention_thresholds`) are read by nothing now; your
  file keeps them and its hash. Step 7 offers each new key; nothing changes unless you say so.
- **Sourced dividends.** On Mondays the Events and Calendar Analyst proposes the cash dividends Alpaca declares for
  your held symbols, for your tap. Earnings dates, yields and AM settlement values stay yours to state.
- **Rulebook change control** moves to the Rules Steward, with a replay of a candidate over your past end-of-day
  snapshots before you adopt it. Adoption still needs your typed phrase and tap; the Desk Lead runs the same steps
  when you ask it. The example limit rules (`BWD`, `UDELTA`) are yours to set before they can be adopted.
- **The weekly review** on Saturday counts your signals and marks per rule, never a money total, a rate or a ranking.
- **New objectives for your structuring criteria.** A covered call, a protective put and a collar can be ranked by
  `band_centre` (closeness to the centre of your own bands, with each leg's quote spread counted), and a roll by
  `net_extrinsic_per_added_day` (the time value the roll adds or gives up). The update asks you, section by section,
  whether to keep your current objective or switch (step 7). Keeping changes nothing.
- **Two information notes** can appear in structuring answers: when the runner-up lies within the best's own quote
  spread, and, under `band_centre`, the share prices at which another candidate would be best. Neither changes the
  ranking.

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
