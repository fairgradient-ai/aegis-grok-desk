# Rulebook changelog

Append-only: rules_adopt.py appends one entry per adoption and never edits an earlier one. Each entry names
the adopted file and the SHA-256 of its bytes, how the owner adopted it, and every rule with its origin mark
(owner-entered or example-accepted) and its threshold.

## 1.0.0 adopted 2026-11-02T22:00:00Z

- file exit_rules-1.0.0.toml, sha256 cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe
- owner phrase sha256 846857d73aeb9edf5cc4f8fed1a1da9e3171e93e28ef97e2fc5942d6aab3d8a9
- TP50 example-accepted: pct_captured >= 50 percent and dte > 21 trading days; EXIT_SIGNAL, plan close
- TP80 example-accepted: pct_captured >= 80 percent; EXIT_SIGNAL, plan close
- LOSSX example-accepted: loss_multiple_of_credit >= 2 x; EXIT_SIGNAL, plan close
- DTE21 example-accepted: dte <= 21 trading days; WATCH, plan roll_review
- DELTA_WATCH example-accepted: abs_delta >= 0.30 delta; WATCH, plan hold_review
- DELTA_SIGNAL example-accepted: abs_delta >= 0.50 delta; EXIT_SIGNAL, plan roll_review
- PIN3 example-accepted: pct_from_strike <= 3 percent and dte <= 3 trading days; WATCH, plan hold_review
- EXDIV example-accepted: days_to_ex_dividend <= 5 days and extrinsic_minus_dividend < 0 usd; EXIT_SIGNAL, plan roll_review
- CAP example-accepted: stock_above_short_call is_true and dte <= 21 trading days; WATCH, plan roll_review
- LAPSE14 example-accepted: dte <= 14 calendar days; EXIT_SIGNAL, plan roll_review
- ROLL30 example-accepted: dte <= 30 calendar days; WATCH, plan roll_review
- MONETIZE example-accepted: abs_delta >= 0.80 delta; EXIT_SIGNAL, plan hold_review
- FLOOR example-accepted: stock_below_long_put is_true; WATCH, plan hold_review
- COVER example-accepted: stock_above_short_call is_true; WATCH, plan roll_review
- AUTOEX example-accepted: auto_exercise_itm is_true; EXIT_SIGNAL, plan hold_review
- LTP100 example-accepted: pct_of_debit >= 100 percent; EXIT_SIGNAL, plan close
- LLOSS50 example-accepted: pct_of_debit <= -50 percent; EXIT_SIGNAL, plan close
- LDTE30 example-accepted: dte <= 30 calendar days; WATCH, plan hold_review
- VTP example-accepted: pct_of_max_profit >= 60 percent; EXIT_SIGNAL, plan close
- VLOSS example-accepted: pct_of_max_loss >= 50 percent; EXIT_SIGNAL, plan close
- VBETWEEN example-accepted: between_strikes is_true and dte <= 2 trading days; EXIT_SIGNAL, plan close
- USTOP example-accepted: spot_vs_stop_pct <= 0 percent; EXIT_SIGNAL, plan hold_review
- MOVE5_DOWN example-accepted: move_pct_from_prev_eod <= -5 percent; WATCH, plan hold_review
- MOVE5_UP example-accepted: move_pct_from_prev_eod >= 5 percent; WATCH, plan hold_review
- EARN2 example-accepted: days_to_earnings <= 2 days and days_to_earnings >= 0 days; WATCH, plan hold_review
- DAILY example-accepted: day_pl <= -1000 usd; WATCH, plan hold_review
- CONC example-accepted: max_concentration_pct >= 25 percent; WATCH, plan hold_review
