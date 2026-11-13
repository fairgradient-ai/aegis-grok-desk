---
name: aegis-desk-bootstrap-and-onboarding
description: The first run of the read-only Aegis options desk on Grok Bot, for the Aegis Desk Lead. The pinned release and its gate, a demo on a sample book before any question, the four Stage-1 Bots, the desk skills with a readback each, the Aegis Options Floor, the one owner action of the first run (a free Alpaca paper key pair, added through the secure secret card, never pasted in chat, verified by a live Opening Bell) and the receipt. The later steps (add my positions, adopt my exit rules, turn on monitoring, set up the structuring desk) start only when the owner says their phrase and follow SETUP.md section 9. Use for a first setup, a repair or a readiness check.
license: MIT
metadata:
  version: "0.2.0"
  author: FairGradient
  category: desk
---

# Desk bootstrap and onboarding

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), commit 75a4c79; see ATTRIBUTION.md.

The Aegis Desk Lead follows these steps in order for the owner's first run. `SETUP.md` in the checkout is the full
runbook; this skill is its checklist for sections 1 to 8. Each step ends with evidence: a tool line, a readback, or
the owner's own confirmation. A step that cannot be done is reported with its exact manual step, and every step that
depends on it stays pending. Nothing is ever reported as done on a guess.

The first run asks the owner for one thing: a free Alpaca paper key (a key ID and its secret), added through the
secure secret card, never pasted in chat. The owner's touchpoints are three: paste the Start prompt, add the key through the card, read the receipt. No
Auto-review rule, connector readback, Notifications, Timezone, interview, positions, rules or routines are asked for
in the first run; each waits for its phrase (step 8).

## Safety boundary for setup

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

- Setup is read-only. No brokerage credential is asked for, read or stored, and no order is created or sent.
- The only credential is the Alpaca paper key pair. The owner adds it through the secure secret card; it is never
  asked for or pasted in chat.
- Local writes stay under `/workspace/aegis-grok-desk` (the release checkout), `/workspace/aegis-desk` (the desk root)
  and `/workspace/aegis-demo` (the sample desk copy). Creating the four Bots and one private group is in scope;
  sharing anything publicly is not.
- Owner-authority commands (`desk_init.py`, `rules_adopt.py`, `ack.py config`, `ack.py connectors`) belong to the later
  steps. Each runs only after the owner taps approve on its Ask-first card; the Lead never adds `--owner-tapped` or
  `--allow-rth` on its own.
- Numbers come from the tools, never from the Lead. UNAVAILABLE is a verdict, never a zero.
- If a capability is unavailable, return the exact manual step and continue the independent work.

## 1. Get the pinned release

```text
git clone --depth 1 --branch v0.2.0 https://github.com/fairgradient-ai/aegis-grok-desk.git /workspace/aegis-grok-desk
mkdir -p /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
```

- If the tag does not exist, stop and tell the owner. An existing `/workspace/aegis-grok-desk` is kept as it is: no
  overwrite, reset or switch; report what the doctor's check 1 says about it.
- Check 1 (`release_pin`) passes with `release v0.2.0 at <commit>, clean`. Until `desk.toml` exists (SETUP section
  9.1), checks 4, 5, 10 and 11 warn and check 8 fails; that is expected.
- If check 1 says `git refuses this checkout: it is owned by another user (see SETUP)`, record it as pending. Its fix,
  one owner-approved `safe.directory` command, belongs to SETUP section 9.3 step 4. Nothing in the first run needs it:
  no `routine.py` runs before SETUP section 9.3.

## 2. Read the desk and run the gate

Read `README.md`, `SETUP.md`, `aegis-desk-charter` and the four agent files. Then, from `/workspace/aegis-grok-desk`,
run `bash scripts/check.sh` once. It takes a few minutes and ends with
`ok: all repository gates passed for /workspace/aegis-grok-desk`; any failure stops the setup.

## 3. Show the demo before asking anything

Copy the sample desk first (tools write into their desk root), then run the demo commands of SETUP section 3:

```text
cp -r /workspace/aegis-grok-desk/tests/fixtures/desk /workspace/aegis-demo
cd /workspace/aegis-grok-desk
python3 scripts/opening_bell.py --demo
```

- The explicit form of the bell on the copy is
  `python3 scripts/opening_bell.py --symbol SPY --offline --desk-root /workspace/aegis-demo --now 2026-11-03T19:50:00Z`.
- Then `positions_check.py`, `book.py` and `trigger_levels.py` with
  `--offline --desk-root /workspace/aegis-demo --now 2026-11-03T19:50:00Z`; each exits 0.
- The demo runs no `routine.py`, `book_check.py` or `exit_check.py`: on the never-acknowledged sample files they open
  with a CONFIG CHANGED line that names an owner-authority command, and `routine.py` also needs the pin check of
  step 1. No `ack.py` command runs on the demo copy.
- Post each output verbatim, labelled "synthetic demo on a sample book: not market data, not your positions".

## 4. Create the Bots

- From `agents/desk-lead.md`: Name "Aegis Desk Lead", Job "Head of the options desk".
- From `agents/exit-sentinel.md`: Name "Aegis Exit Sentinel", Job "Exit-rule monitor of the options desk".
- From `agents/position-control.md`: Name "Aegis Position Control", Job "Book-of-record and expiry controller of the
  options desk".
- From `agents/structuring-desk.md`: Name "Aegis Structuring Desk", Job "On-request choices desk of the options
  desk".

Read back each Bot's native Name and Job. Its standing instructions are its role skill (`aegis-role-desk-lead`,
`aegis-role-exit-sentinel`, `aegis-role-position-control` or `aegis-role-structuring-desk`), enabled for that Bot only.
The Stage-2 and Stage-3 seats are not in this release; asked for one, a Bot says so.

## 5. Install the skills

Each Bot gets the desk skills its agent file lists under `skills:` (the list is in SETUP section 5). Every skill is
installed with a readback: the stored name, description and body compared with `skills/<name>/SKILL.md` in the
checkout; `template/grok-bot.json` records each file's sha256 (`skills` and `roleSkills`). One status each:

- `template`: already present from the template and identical;
- `installed`: saved now, and the readback is identical;
- `pointer`: the product could not hold the body, so the stored text points to the checkout file;
- `mismatch`: the stored text differs or cannot be read back. That skill is not ready, and a shared skill with the
  same name but a different body is never enabled.

## 6. Create the Aegis Options Floor

One private group, "Aegis Options Floor", with exactly the four Bots; read back its members. A group holds two to six
Bots, which leaves room for the Stage-2 seats. Nothing is shared publicly.

## 7. Connect market data

The one owner action. Send the owner the key steps of SETUP section 7 as written there (sign up at alpaca.markets
with an email, switch the dashboard to Paper Trading, API Keys, Generate New Key, copy the key ID and the secret,
which is shown once; never a live key).

1. Ask for two secrets with the secure secret card: the key ID as `APCA_API_KEY_ID` and the secret as
   `APCA_API_SECRET_KEY`. The owner pastes them there, never in chat. The fallback is the file
   `/workspace/.aegis-secrets/alpaca.key` with mode 0600 (the key ID on line 1, the secret on line 2), passed as
   `--credentials-file`.
2. Verify with the live Opening Bell, posted verbatim (a public market snapshot, not a signal):

```text
python3 /workspace/aegis-grok-desk/scripts/opening_bell.py --symbol SPY --desk-root /workspace/aegis-desk
```

   With the file fallback, add `--credentials-file /workspace/.aegis-secrets/alpaca.key`. Without a key it prints
   `UNAVAILABLE: no_credential: ...` and exits 20. `UNAVAILABLE: the quotes request failed: auth: ...` is a 401 or
   403 from the market-data host: a key ID and secret that do not belong together, or a pair regenerated or deleted
   since. The bell's contract listing is read on Alpaca's paper trading host, which refuses a live key, so a live key
   prints (exit 20; 403 in place of 401 is the same verdict):

```text
UNAVAILABLE: not a paper key: Alpaca's paper trading host refused the key pair (HTTP 401) after the market-data host answered it; delete both secrets and add a new pair from the Paper Trading dashboard
```

   On either line the owner deletes both secrets, generates a new paper pair and adds it through the card, and you
   verify again. A live key can trade and must never stay, so even before the bell runs: if the key ID starts with
   `AK`, or the owner is not sure it came from the Paper Trading dashboard, ask the owner to delete both secrets.
3. Record the key source (the card with `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`, or the file). The doctor
   verifies it from SETUP section 9.1 on, once `desk.toml` exists: check 5 asks Alpaca's paper trading host, which
   refuses a key from another environment with a 401 or 403 (Alpaca's documented mismatch error, not a published
   paper/live test).

## 8. The receipt

One message. It opens with three plain lines: the desk is built (four Bots, the Floor, the skills, release v0.2.0);
whether live market data works; and that nothing else is needed now, with "say 'add my positions'" when the owner
wants the desk to watch the book. Then the evidence:

- the release and the pin check line; the `check.sh` result; the demo commands run (synthetic);
- the four Bots by Name and Job; each skill and its status; the Floor and its members;
- the live Opening Bell output, or the exact step still pending; the key source;
- the doctor's verdict line; every pending item with its manual step.

Then three notes: until "add my positions" no Auto-review rule is in place, and the paper key cannot reach a live
account (it could place paper orders on Alpaca's paper trading host, which the desk's GET-only code cannot, and the
write-side Ask-first rule covers from SETUP section 9.1 on); never sign in to a brokerage in the Bot computer's browser; and the phrases for
later: say "add my positions", "adopt my exit rules", "turn on monitoring", or optionally "set up the structuring desk".
Each phrase runs SETUP section 9.1, 9.2, 9.3 or 9.4, prerequisites first: every later phrase first checks 9.1's
safety settings and desk questions (both Ask-first rules, the connected-apps answer recorded by `ack.py connectors`,
and `desk.toml`) and, if any is missing, does those first, then continues. No owner-authority command and no routine
comes before the two Ask-first rules.

Its last line is exactly the line below, and only when the live Opening Bell succeeded:

```text
Setup stayed read-only: one Alpaca paper key pair was added by you; no order was created or sent.
```

If the owner added no key, the last line is
`Setup stayed read-only: no credential was added; no order was created or sent.` If an added key failed the bell,
the receipt names the failed line and its next step (after `auth:`, deleting both secrets) and ends
`Setup stayed read-only: a market-data key pair was added by you but did not pass its check; no order was created or sent.`
Do not call the desk ready while a required check failed: name the failed check and the next safe action.
