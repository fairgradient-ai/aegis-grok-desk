# Example exit rules

Nothing in this directory is active. [`exit_rules-1.0.0.toml`](exit_rules-1.0.0.toml) is a set of example exit
rules: every rule in it is inert until the owner adopts it, rule by rule, through the owner-tapped adoption
step. Until then the desk evaluates nothing and posts nothing on the strength of these examples.

## How a rule becomes the owner's

1. The owner copies the file, reviews each rule and marks it: `origin = "example-accepted"` when the
   documented threshold is the owner's own choice, `origin = "owner-entered"` when the owner writes a
   different value. A rule left at `origin = "example"` is refused by the adoption lint.
2. Two rules, `DAILY` (the book's day loss) and `CONC` (one underlying's share of the declared equity), carry
   `owner_must_set = true`: their values depend on the owner's account, so the lint refuses them until the
   owner has decided, either by entering a value (`owner-entered`) or by writing `owner_must_set = false` to
   keep the documented default after reviewing it.
3. The adoption step (`rules_adopt.py --file <copy> --version X.Y.Z --owner-phrase "adopt rules X.Y.Z"`) runs
   outside regular trading hours unless the owner passes `--allow-rth`. It lints the rules against the
   structures held in `positions.csv`, copies the file immutably to `rules/exit_rules-X.Y.Z.toml` (a version
   once adopted never changes), records the file's SHA-256 and every rule's own SHA-256 in `rules/ADOPTED`, and
   appends one entry to `rules/CHANGELOG.md` listing each rule as `owner-entered` or `example-accepted` with
   its threshold. Rule state carries over only for rules whose meaning did not change.

## What the lint checks

The grammar is strict: unknown keys, unknown metrics, a missing `unit` on a percent or dollar condition, a `dte`
condition without `dte_unit` (`calendar` or `trading`), or a parameter outside its safe bounds (1 to 3
confirmations, 1 to 60 minutes between them, a window of at most 240 minutes, a cooldown of at most 1440
minutes, a hysteresis of at most 50 percent) stops the file from loading. A cooldown holds a rule's re-arm back
after a post, but never past the open of the next trading session, so a long cooldown cannot keep an exit rule
silent into the next day's session. The adoption lint then checks that
every held structure has a take-profit rule that applies to it (a protective put, being a hedge, needs none),
that each take-profit rule reads the structure's own measure (`pct_captured` for a credit, `pct_of_debit` or
`pct_of_max_profit` for a debit), that a structure has at most one loss rule, that a covered call or collar
has no loss rule on its short call, and that no `pct_of_max_loss` threshold exceeds 100.

## The families

| Family | Rules | Applies to |
| --- | --- | --- |
| Short premium | `TP50`, `TP80`, `LOSSX`, `DTE21`, `DELTA_WATCH`, `DELTA_SIGNAL`, `PIN3` | cash-secured puts, naked short calls, credit verticals, the short call of a covered call or collar (`LOSSX` excepted there) |
| Short calls | `EXDIV` | every open short call: the extrinsic value (bid) against the coming dividend |
| Covered calls | `CAP` | the stock above the call's strike in the last 21 trading days |
| Long hedges | `LAPSE14`, `ROLL30`, `MONETIZE`, `FLOOR`, `COVER`, `AUTOEX` | the put of a protective put or collar, the collar's call, every long option on its last trading day |
| Long directional | `LTP100`, `LLOSS50`, `LDTE30` | long calls, long puts (and debit verticals for `LDTE30`) |
| Verticals | `VTP`, `VLOSS`, `VBETWEEN` | debit verticals; `VBETWEEN` every vertical |
| Underlying | `USTOP`, `MOVE5_DOWN`, `MOVE5_UP` | the stop written in `positions.csv`; the move from the desk's own previous end-of-day snapshot |
| Events | `EARN2` | an earnings release within 2 trading days on a group with a short option |
| Portfolio | `DAILY`, `CONC` | the whole book |

Two row tags switch rules off for one group, through each rule's `suppress_if_tag`: in this example
rulebook `#assign-ok` (the owner accepted assignment) silences `EXDIV`, `DELTA_WATCH`, `DELTA_SIGNAL` and `CAP`,
and `#earnings-ok` silences `EARN2`. The example gives `PIN3` no `suppress_if_tag`, because whether assignment
happens stays uncertain near the strike; an owner's own rulebook decides this rule by rule.

## Thresholds are the owner's choices

Each example names its public reference class in its `source`: tastylive's research on managing short
premium and spreads, the OIC and Cboe material on assignment, early exercise and pin risk, OCC's exercise by
exception, or plain risk control. Those references describe how such thresholds are commonly reasoned about;
the numbers in this file are documented defaults for the owner to accept or change, not a judgment that they
suit any account. A stop alert is risk control, not edge.

## Why every rule is the owner's (FINRA Notice to Members 01-23, stated plainly)

FINRA Notice to Members 01-23 discusses when an online communication can amount to a recommendation: the more
a message is tailored to one investor and the more it reads as a call to action, the more likely it is one.
The desk is built to stay on the informational side of that line. The rules are the owner's, adopted one by
one with a typed owner phrase; an alert says that the owner's own rule fired, with the numbers that made it
fire and the plan the owner wrote down; it carries no order fields, no strikes or expiries to move to and no
ranked alternatives; and the owner acts, or not, in the brokerage account. This describes how the desk is
designed; it is not legal advice, and whether a given use is a recommendation is a question for the owner's
own counsel.
