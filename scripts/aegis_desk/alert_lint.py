"""Alert lint: proof that a message carries only numbers the desk emitted, the fixed footer and no trade language.

Plan section 8 ("Signals and alerts") and the PR 2c brief; standard library only, no I/O. The model that relays a
message never does arithmetic: every number in an alert is a string a formatter of :mod:`aegis_desk.alerts`
emitted, and :func:`check` proves it before anything is printed or posted. ``check(text, emitted)`` returns a
:class:`LintResult` whose ``ok`` is true only when every rule below holds; ``problems`` are one-line strings (the
``message`` of each :class:`Finding`, in text order, the footer finding last) that never repeat the forbidden text
they report, so a tool may print them anywhere.

Numbers
-------
:func:`numeric_tokens` finds every run of digits, whatever it is attached to: letters (``2x``, ``x2``, ``TP50``),
hyphens (``21-DTE``), ``%`` (``52.4%``), thousands separators (``1,640``) and decimal points (``3.42``, ``.5``,
``1.0.0``). Trailing sentence punctuation (``.`` and ``,``) is not part of a number (``at 3.42.`` is ``3.42``). A
``+`` or ``-`` directly before the digits (or before a ``$`` before them) is the number's sign when nothing
alphanumeric precedes it (``move -5 %`` is ``-5``, ``-$1,000.00`` is ``-1,000.00``, ``x-5`` and ``10-20`` are
unsigned). Each token outside the identifier whitelist must equal, character for character, an entry of
``emitted`` after the entry is reduced the same way (a trailing ``%``, ``x`` or ``,`` and a ``$`` are not part
of it): ``2x credit`` against ``2.0`` fails, ``21-DTE`` against ``34`` fails, ``52.4 %`` against ``52.1`` fails,
``-5`` against ``5`` fails.

Identifier whitelist (addendum decision 6)
------------------------------------------
Numbers inside these spans need no emission: trade ids ``AG-\\d{8}-\\d{2,3}``, signal ids ``SIG-\\d{8}-\\d{3}``,
group ids ``ROW-\\d+``, rulebook versions ``rules X.Y.Z``, shas ``sha <8 lowercase hex>``, clock times ``HH:MM
UTC`` and ``HH:MM ET`` (a real time of day), ISO dates ``YYYY-MM-DD`` (a real date), rule ids, and the footer's
``data delayed 15 min``. A rule id is a word of the grammar's shape (``[A-Z][A-Z0-9_]{1,15}``): with ``rule_ids``
given, only those exact ids are identifiers; without it, a word of two capitals followed by capitals, digits
or underscores (``TP80``, ``MOVE5_DOWN``) is. Structure words and grade letters carry no digits. Every other
number is checked: ``N min late``, ``N trading days``, ``N calendar days``, percentages and the header's
``15-min delayed`` must be emitted like any number.

Strict mode (the emitted document ``book_check.py`` prints, :func:`load_emitted`): with ``identifiers`` given, a
trade, signal or group id, a ``rules X.Y.Z``, a ``sha <8 hex>`` or an ISO date is an identifier only when that exact
text is in the list (each builder of :mod:`aegis_desk.alerts` records the ones it printed), and with ``clocks``
given the same holds for ``HH:MM UTC`` / ``HH:MM ET``; any other span of those shapes is the finding
``identifier_not_emitted`` (an invented ``ROW-105``, ``sha 00000110`` or ``10:50 ET`` cannot smuggle digits). With
``as_of`` given, the footer's clock must be that instant's ``HH:MM UTC`` (a relay cannot move the data in time).

Footer
------
The last non-empty line must be exactly :func:`footer_line` of some instant: ``deterministic rules you adopted
· posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of HH:MM UTC``. A
delay other than 15 minutes must be emitted. :func:`check` with ``footer="snapshot"`` instead requires the
market-snapshot footer, ``public market data · posted by an AI assistant · not a signal and not personalized
investment advice · data delayed 15 min, as of HH:MM UTC`` (:func:`snapshot_footer_line`), the accurate disclosure
for a public quote shown before any rule exists (``opening_bell.py``). That footer closes only rule-free text: a
rule id passed in ``rule_ids``, ``your rule``, a rulebook version ``rules X.Y.Z`` or a verdict word (``EXIT
SIGNAL``, ``WATCH``, ``HOLD``, ``UNAVAILABLE``) under it is the finding ``footer_missing``, so the rules footer
stays mandatory for anything a rule says. The default is the rules footer.

``footer="structuring"`` requires the structuring footer, ``computed by the desk's tools from your criteria · posted
by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your
brokerage account · data delayed 15 min, as of HH:MM UTC`` (:func:`structuring_footer_line`): the accurate footer of
the Structuring Desk's tickets, roll analyses and proposals, which only the owner's own request produces. It never
closes rule content either: ``your rule`` (up to two words between, any joining), ``your plan:``, a rulebook version
or a verdict word (an exit signal in any case or joining; ``WATCH`` and ``HOLD`` in capitals, or ``Watch`` / ``Hold`` at
a sentence start or after ``verdict``: ``you hold 200 shares`` is not a verdict) under it, or a non-empty ``rule_ids``,
is ``footer_missing`` (``UNAVAILABLE`` stays allowed: the structuring tools print it). The reverse holds too: the rules
and snapshot footers never close a structuring text (:data:`_STRUCTURING_CONTENT_RE`): a leg reference anywhere in a
line (``leg 2 of 2``, ``leg 2/2``, ``leg one of two``, any joining), the leg grammar's effect words (``closes your
short``, ``opens a new long``, ``of your long WMT shares``), the fixed starts of the structuring texts (TICKET,
PROPOSAL, STRUCTURING, NOTHING_TO_PROPOSE), an action tag, FAILS_CRITERION, a best or numbered candidate (``best
candidate``, ``best:``, ``candidate #1``, ``candidate-2``, ``1st candidate``, ``rank 1``), the counts line, the subjects
of the answers that price nothing (``roll analysis``, ``collar proposal``, ``CLOSE ticket``), the three tool names
(``roll_analysis.py``, ``propose.py``, ``exit_ticket.py``: the structuring WITHHELD line names its tool, while the
routine's own ``WITHHELD | book_check: ...`` line closes with the fixed footer) and the approval convention's
``self-attested level`` under either of them is ``footer_missing`` (``candidate 1.1.0`` and
``rules/candidate-1.1.0.toml`` are not structuring text). No alert, sheet, brief or routine output can therefore carry a
leg or a ranked candidate.

Language
--------
:data:`FORBIDDEN_LANGUAGE` is the table of forbidden phrases (case-insensitive, word-bounded, any run of
whitespace between words, line breaks included): advice phrases, trade verbs and their forms, order
instructions, order kinds and durations (a resting ``working order`` among them), roll destinations, price
objectives and order-ticket fields; the words of a phrase may be joined by any run of whitespace, hyphens or
underscores (:data:`WORD_SEPARATOR`).
:data:`FORBIDDEN_PATTERNS` holds the same compiled patterns for other screens (``cli_journeys.py``'s
``FORBIDDEN_OUTPUT``). The table covers order-ticket fields however their words are joined (``limit price:``,
``Limit-Price:``, ``order type:``, ``quantity:``, ``price:``, ``limit 0.16``), roll destinations with up to three
words between the verb and ``to``/``into`` (``roll up and out to``), advice from any subject (``recommend`` in any
form) and ``you ... should`` with up to two words between. A sentence must not start with one of
:data:`IMPERATIVE_START_WORDS` (any case): a sentence starts at the start of a line, after ``!``, ``?``, ``;``,
``:``, ``|`` or ``,`` (with or without whitespace), after ``.`` followed by whitespace (``3.42`` and ``ack.py`` are
not boundaries) and after a dash standing between spaces (`` - `` or `` -- ``); opening quotes, brackets, heading
and list marks (``-``, ``*``, ``1)``, ``1.``, ``a)``) and the filler words of :data:`FILLER_WORDS` (``so close``,
``now close``) are skipped. Two fixed phrases are the exceptions: ``your plan: close`` (the plan word standing
alone, followed by the end of the line, ``|``, ``(`` or ``;``) and the alert kind ``EXIT SIGNAL`` in capitals.

Structuring mode
----------------
With the structuring footer the lint adds rules and relaxes none. ``rule_ids=None`` is read as ``[]`` (no
capitals-and-digits word is whitelisted as a rule id). A line that holds a leg reference anywhere
(:data:`STRUCTURING_LEG_DETECT_RE`: any case, any indent or list mark, ``N/M`` or spelled numbers, any word separator)
or the leg grammar's effect words (:data:`STRUCTURING_EFFECT_RE`) must be one of the fixed leg grammar
(:data:`STRUCTURING_LEG_RES`): ``leg N of M: closes your long|short Q x ROOT YYYY-MM-DD STRIKE call|put`` or ``leg N of
M: opens a new long|short Q x ROOT YYYY-MM-DD STRIKE call|put`` or ``leg N of M: closes Q of your long SYMBOL shares``,
each optionally followed by `` | `` and exactly one of the quote segments the tools print (``bid P, ask P, mid P |
quote grade G, SOURCE, N-min delayed, as of HH:MM UTC``, SOURCE being one of :data:`aegis_desk.data_source.ALL_WORDS`;
``ask P | quote grade G, N min old``; the bare sides) and the grade E note; any other leg line is ``order_field`` (the effect words are unambiguous: a closing twin of
a side word would read as the wrong direction). :data:`STRUCTURING_FORBIDDEN_LANGUAGE` (``SL01`` .. ``SL22``: advice
from any subject, a preference, probability, risk, return, valuation and non-advice claims, an assurance, urgency,
suitability, the bare instruction verb in any form, order-type, duration and route abbreviations, a trade verb outside
the base table, and a price after ``at`` or ``for`` or a few words after ``limit``, ``pay`` or ``price``) is matched like
the base table
(any case, any word separator; the claim and instruction words with lookarounds, so underscore-joined forms are caught)
everywhere but the footer line; ``SL14`` and ``SL20`` are ``order_field``, the others ``forbidden_language``; the table
stays out of :data:`FORBIDDEN_PATTERNS`, so other screens are unchanged. The sentence starts of
:data:`STRUCTURING_IMPERATIVE_START_WORDS` are refused as ``IS10`` .. ``IS42``, after the base ``IS01`` .. ``IS09``, and
``your plan: close`` is no exception in structuring mode (a plan word is rule content there).

Alphabet
--------
A message is printable ASCII plus newline and tab, and the middle dot (U+00B7) inside the footer line only: a
homoglyph, an invisible character (zero-width space, no-break space), a full-width digit, a typographic minus or a
middle dot between two words cannot hide a number, a phrase or a sentence start from the rules above.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import data_source

MIDDLE_DOT: str = "·"
FOOTER_DELAY_MIN: int = 15
FOOTER_PREFIX: str = ("deterministic rules you adopted · posted by an AI assistant · not personalized "
                      "investment advice · data delayed ")
FOOTER_RE = re.compile(r"^" + re.escape(FOOTER_PREFIX)
                       + r"(?P<delay>[0-9]+) min, as of (?P<clock>(?:[01][0-9]|2[0-3]):[0-5][0-9]) UTC$")
"""The fixed footer (module notes): the delay in minutes and the data's as-of time of day in UTC."""
SNAPSHOT_FOOTER_PREFIX: str = ("public market data · posted by an AI assistant · not a signal and not personalized "
                               "investment advice · data delayed ")
SNAPSHOT_FOOTER_RE = re.compile(r"^" + re.escape(SNAPSHOT_FOOTER_PREFIX)
                                + r"(?P<delay>[0-9]+) min, as of (?P<clock>(?:[01][0-9]|2[0-3]):[0-5][0-9]) UTC$")
"""The market-snapshot footer (module notes): only for rule-free text."""
STRUCTURING_FOOTER_PREFIX: str = ("computed by the desk's tools from your criteria · posted by an AI assistant · "
                                  "a personalized suggestion for positions you hold, not an order · you decide and act "
                                  "in your brokerage account · data delayed ")
STRUCTURING_FOOTER_RE = re.compile(r"^" + re.escape(STRUCTURING_FOOTER_PREFIX)
                                   + r"(?P<delay>[0-9]+) min, as of (?P<clock>(?:[01][0-9]|2[0-3]):[0-5][0-9]) UTC$")
"""The structuring footer (module notes): only for the Structuring Desk's tickets, roll analyses and proposals."""
FOOTER_RULES: str = "rules"
FOOTER_SNAPSHOT: str = "snapshot"
FOOTER_STRUCTURING: str = "structuring"
FOOTER_KINDS: Tuple[str, ...] = (FOOTER_RULES, FOOTER_SNAPSHOT, FOOTER_STRUCTURING)
_FOOTER_RES = {FOOTER_RULES: FOOTER_RE, FOOTER_SNAPSHOT: SNAPSHOT_FOOTER_RE, FOOTER_STRUCTURING: STRUCTURING_FOOTER_RE}
_FOOTER_PREFIXES = {FOOTER_RULES: FOOTER_PREFIX, FOOTER_SNAPSHOT: SNAPSHOT_FOOTER_PREFIX,
                    FOOTER_STRUCTURING: STRUCTURING_FOOTER_PREFIX}
_FOOTER_NAMES = {FOOTER_RULES: "fixed footer", FOOTER_SNAPSHOT: "market-snapshot footer",
                 FOOTER_STRUCTURING: "structuring footer"}
_STRUCTURING_RULE_CONTENT_RE = re.compile(
    r"(?i:\byour(?:[\s_-]+[a-z]+){0,2}?[\s_-]+rules?\b)|(?i:\byour[\s_-]+plan[\s_-]*:)|(?i:\bEXIT[\s_-]*SIGNAL)"
    r"|\bWATCH\b|\bHOLD\b|(?m:(?:^|[|:;!?]|\.[ \t])[ \t]*(?:Watch|Hold)\b)"
    r"|(?i:\bverdicts?(?:[\s_-]+[a-z]+){0,2}?[\s_-]+(?:watch|hold|exit))\b"
    r"|(?i:\brules\s+[0-9]+\.[0-9]+\.[0-9]+)")
"""Rule content the structuring footer can never close (module notes): ``your ... rule``, ``your plan:``, an exit
signal in any case or joining, ``WATCH`` and ``HOLD`` in capitals (or ``Watch``/``Hold`` at a sentence start or after
``verdict``) and a rulebook version; ``UNAVAILABLE`` and a lower-case ``you hold 200 shares`` stay allowed."""
_LEG_NUMBER = r"(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten)"
STRUCTURING_LEG_DETECT_RE = re.compile(r"(?i)(?<![a-z0-9])leg[\s_-]*" + _LEG_NUMBER + r"[\s_-]*(?:of|/)[\s_-]*"
                                       + _LEG_NUMBER + r"(?![a-z0-9])")
"""A leg reference anywhere in a line, in any case, with any word separator, ``N/M`` or a spelled number (review L2-2):
in structuring mode such a line must be one of :data:`STRUCTURING_LEG_RES`; under the rules or snapshot footer it is
structuring content."""
STRUCTURING_EFFECT_RE = re.compile(r"(?i)\bcloses[\s_-]+your[\s_-]+(?:long|short)\b|\bopens[\s_-]+a[\s_-]+new[\s_-]+"
                                   r"(?:long|short)\b|\bof[\s_-]+your[\s_-]+long[\s_-]+[A-Za-z][A-Za-z0-9.]*[\s_-]+shares\b")
"""The leg grammar's effect words: a line that holds them is a leg line (structuring mode) and structuring content."""
_STRUCTURING_CONTENT_RE = re.compile(
    STRUCTURING_LEG_DETECT_RE.pattern[4:].join(("(?i:", ")"))
    + "|" + STRUCTURING_EFFECT_RE.pattern[4:].join(("(?i:", ")"))
    + r"|(?m:^[ \t]*(?:TICKET|PROPOSAL|STRUCTURING|NOTHING_TO_PROPOSE)\b)"
    r"|\(action (?:CLOSE|UNWIND_STOCK|LET_ASSIGN|MONETIZE_PUT|ROLL)\)|FAILS_CRITERION"
    r"|\b(?:CLOSE|UNWIND_STOCK|LET_ASSIGN|MONETIZE_PUT|ROLL) ticket\b"
    r"|(?i:\bno[\s_-]+leg[\s_-]+to[\s_-]+enter\b|\bcriteria[\s_-]+structuring\.toml\b|\bno[\s_-]+structuring\.toml\b"
    r"|\bbest[\s_-]+candidates?\b|\bbest[ \t]*:|\bcandidates?[\s_-]*#?[0-9]+(?![0-9]|\.[0-9])"
    r"|\b[0-9]+(?:st|nd|rd|th)[\s_-]+candidates?\b|\brank(?:ed)?[\s_-]*#?[0-9]+(?![0-9]|\.[0-9])"
    r"|\breplacement[\s_-]+candidates?\b|\bpassed[\s_-]+your[\s_-]+criteria\b|\bnames[\s_-]+no[\s_-]+candidate\b"
    r"|\bprints[\s_-]+no[\s_-]+ticket\b|\broll[\s_-]+analysis\b|\b(?:covered[\s_-]+call|protective[\s_-]+put|collar)"
    r"[\s_-]+proposal\b|\bself-attested[\s_-]+level\b|\bstructuring[\s_-]+desk\b"
    r"|\b(?:exit_ticket|roll_analysis|propose)\.py\b)")
"""Structuring text the rules and snapshot footers can never close (module notes, review L2-4): a leg reference or the
leg grammar's effect words anywhere, the fixed starts of the structuring texts (TICKET, PROPOSAL, STRUCTURING,
NOTHING_TO_PROPOSE), an action tag, FAILS_CRITERION, a ranked or numbered candidate (``candidate #1``, ``1st
candidate``, ``rank 1``, ``best:``), the counts line, the subjects of the answers that price nothing, the three tool
names (the structuring WITHHELD line names its tool; the routine's ``WITHHELD | <tool>:`` line is rules text) and the
approval convention's self-attested level (``candidate 1.1.0`` and ``candidate-1.1.0.toml`` do not match)."""
_SNAPSHOT_RULE_CONTENT_RE = re.compile(r"(?i:\byour\s+rules?\b)|\bEXIT[ _]SIGNAL\b|\bWATCH\b|\bHOLD\b|\bUNAVAILABLE\b"
                                       r"|(?i:\brules\s+[0-9]+\.[0-9]+\.[0-9]+)")
"""Rule content a market-snapshot footer can never close (module notes)."""
ALLOWED_EXTRA_CHARACTERS: Tuple[str, ...] = ("\n", "\t", MIDDLE_DOT)

CODE_EMPTY: str = "empty_message"
CODE_NON_ASCII: str = "non_ascii"
CODE_NUMBER: str = "number_not_emitted"
CODE_FORBIDDEN: str = "forbidden_language"
CODE_ORDER_FIELD: str = "order_field"
CODE_IMPERATIVE: str = "imperative_start"
CODE_FOOTER: str = "footer_missing"
CODE_IDENTIFIER: str = "identifier_not_emitted"
CODES: Tuple[str, ...] = (CODE_EMPTY, CODE_NON_ASCII, CODE_NUMBER, CODE_IDENTIFIER, CODE_FORBIDDEN, CODE_ORDER_FIELD,
                          CODE_IMPERATIVE, CODE_FOOTER)

FORBIDDEN_CATEGORIES: Tuple[str, ...] = ("advice", "trade-verb", "order-instruction", "order-kind",
                                         "roll-destination", "price-objective", "order-field")

_I = re.IGNORECASE


@dataclass(frozen=True)
class ForbiddenPhrase:
    """One entry of :data:`FORBIDDEN_LANGUAGE`: a stable ``code`` (what a problem names instead of the phrase),
    its ``category``, the compiled ``pattern`` and an ``example`` the pattern matches (for the tests only)."""

    code: str
    category: str
    pattern: "re.Pattern[str]"
    example: str


WORD_SEPARATOR: str = r"[\s_-]+"
"""What may stand between the words of a forbidden phrase: any run of whitespace (line breaks included),
hyphens and underscores (a phrase written with hyphens or underscores between its words, or broken with a hyphen
across lines)."""


def _phrase(code: str, category: str, regex: str, example: str) -> ForbiddenPhrase:
    regex = regex.replace(r"[\s-]+", WORD_SEPARATOR).replace(r"\s+", WORD_SEPARATOR)
    return ForbiddenPhrase(code, category, re.compile(regex, _I), example)


FORBIDDEN_LANGUAGE: Tuple[ForbiddenPhrase, ...] = (
    _phrase("FL01", "advice", r"\byou(?:\s+[a-z']+){0,2}?\s+should\b", "you should"),
    _phrase("FL02", "advice", r"(?<![a-z0-9])recommend(?:s|ed|ing)?(?![a-z0-9])", "we recommend"),
    _phrase("FL03", "advice", r"\bi\s+recommend(?:s|ed|ing)?\b", "I recommend"),
    _phrase("FL04", "advice", r"\brecommendations?\b", "recommendation"),
    _phrase("FL05", "trade-verb", r"\bbuy(?:s|ing)?\b", "buy"),
    _phrase("FL06", "trade-verb", r"\bbought\b", "bought"),
    _phrase("FL07", "trade-verb", r"\bsell(?:s|ing)?\b", "sell"),
    _phrase("FL08", "trade-verb", r"\bsold\b", "sold"),
    _phrase("FL09", "order-instruction", r"\bplace\s+(?:an?\s+|the\s+|your\s+|this\s+)?orders?\b", "place an order"),
    _phrase("FL10", "order-instruction", r"\bsubmit(?:s|ted|ting)?\b", "submit"),
    _phrase("FL11", "order-kind", r"\blimit\s+orders?\b", "limit order"),
    _phrase("FL12", "order-kind", r"\bmarket\s+orders?\b", "market order"),
    _phrase("FL13", "order-kind", r"\bgtc\b", "GTC"),
    _phrase("FL14", "order-kind", r"\bday\s+orders?\b", "day order"),
    _phrase("FL15", "order-kind", r"\bgood[\s-]+(?:till|til|until)[\s-]+cancel(?:l?ed)?\b", "good till canceled"),
    _phrase("FL16", "order-kind", r"\btime\s+in\s+force\b", "time in force"),
    _phrase("FL17", "order-kind", r"\bstop(?:[\s-]+loss)?[\s-]+orders?\b", "stop order"),
    _phrase("FL18", "roll-destination", r"\broll(?:s|ed|ing)?(?:\s+[a-z]+){0,3}?\s+to\b", "roll to"),
    _phrase("FL19", "roll-destination", r"\broll(?:s|ed|ing)?(?:\s+[a-z]+){0,3}?\s+into\b", "roll into"),
    _phrase("FL20", "price-objective", r"\btarget(?:s|ed|ing)?\b", "target"),
    _phrase("FL21", "order-field", r"\b(?:buy|sell)_to_(?:open|close)\b", "buy_to_close"),
    _phrase("FL22", "order-field",
            r"\b(?:side|order\s+type|order\s+class|limit\s+price|stop\s+price|time\s+in\s+force|tif|duration|qty|"
            r"quantity|price|limit)\s*[:=]", "side: x"),
    _phrase("FL23", "order-field", r"@\s*\$?[0-9]", "2 @ 0.16"),
    _phrase("FL24", "order-field", r"\blimit\s+(?:at\s+|of\s+)?(?:\$[0-9]|[0-9]+\.[0-9])", "limit 0.16"),
    _phrase("FL25", "order-kind", r"\bworking\s+orders?\b", "working order"),
)
"""The forbidden phrases (module notes): with :data:`STRUCTURING_FORBIDDEN_LANGUAGE`, the one table in the
repository's code that spells them out."""
FORBIDDEN_PATTERNS: Tuple["re.Pattern[str]", ...] = tuple(entry.pattern for entry in FORBIDDEN_LANGUAGE)

IMPERATIVE_START_WORDS: Tuple[str, ...] = ("close", "sell", "buy", "roll", "place", "enter", "exit", "execute", "fill")
"""Words a sentence must not start with (any case); a problem names the entry as ``IS01`` .. ``IS09`` (``execute`` and
``fill`` joined in PR 2d as starts only: as words they stay allowed, so the level sheet's warning ``a limit at this price
executes immediately`` passes)."""
STRUCTURING_IMPERATIVE_START_WORDS: Tuple[str, ...] = (
    "open", "write", "take", "go", "consider", "choose", "pick", "use", "let", "keep", "hold", "set", "type", "act",
    "do", "try", "hedge", "protect", "cover", "collar", "unwind", "monetize", "replace", "lock", "add",
    "purchase", "liquidate", "exercise", "short", "trade", "acquire", "dump", "unload")
"""Sentence starts refused in structuring mode only, named ``IS10`` .. ``IS42`` in this order (module notes; IS35 ..
IS42, the trade verbs, from the PR 3b review)."""
STRUCTURING_FORBIDDEN_LANGUAGE: Tuple[ForbiddenPhrase, ...] = (
    _phrase("SL01", "advice", r"\b(?:you|one)\s+(?:might|may|could|would)\s+(?:want|wish|like)\s+to\b",
            "you might want to"),
    _phrase("SL02", "advice", r"\b(?:i|we)(?:'d|\s+would)\b", "I'd"),
    _phrase("SL03", "advice", r"\b(?:i|we)\s+(?:suggest|advise|prefer|favou?r|propose|like|think|believe|feel)\b",
            "I suggest"),
    _phrase("SL04", "advice", r"\bgo(?:es|ing)?\s+with\b", "go with"),
    _phrase("SL05", "advice", r"\bthe\s+one\s+to\s+(?:pick|choose|take|use|open)\b", "the one to pick"),
    _phrase("SL06", "advice", r"\b(?:best|better|smart|smarter|wise|right|good)\s+(?:move|choice|bet|trade|play|idea|deal)\b",
            "best move"),
    _phrase("SL07", "advice", r"\byou(?:\s+[a-z']+){0,2}?\s+(?:must|need\s+to|have\s+to|ought\s+to)\b", "you need to"),
    # SL08, SL09, SL12, SL13 and the claim words below use lookarounds, not \b, so an underscore-joined form
    # (probability_of_profit, place_it) is caught too (BD7), as base FL02 does
    _phrase("SL08", "claim", r"(?<![a-z0-9])(?:probabilit(?:y|ies)|probabl[ey]|improbab(?:le|ly|ility)"
            r"|(?:un)?likel(?:y|ier|iest|ihood)|chances?|odds|pop)(?![a-z0-9])", "probability of profit"),
    _phrase("SL09", "claim", r"(?<![a-z0-9])(?:guarantee(?:s|d)?|risk[\s-]+free|riskless|can(?:not|'t)\s+lose|sure\s+thing"
            r"|(?:no|zero|without)\s+risk)(?![a-z0-9])", "risk-free"),
    _phrase("SL10", "urgency", r"\b(?:act|move)\s+(?:now|fast|quickly|today)\b|\bdon'?t\s+miss\b|\burgent(?:ly)?\b"
            r"|\bhurr(?:y|ies|ied|ying)\b|\bimmediately\b", "act now"),
    _phrase("SL11", "advice", r"\bsuit(?:s|ed|able|ability)?\b"
            r"|\b(?:right|best|good|better|appropriate|ideal)(?:\s+[a-z]+){0,2}?\s+for\s+you\b"
            r"|\bappropriate(?:ly|ness)?\b|\bfits?\s+you\b", "suitable"),
    _phrase("SL12", "order-instruction", r"(?<![a-z0-9])plac(?:e|es|ed|ing)(?![a-z0-9])", "place it"),
    _phrase("SL13", "claim", r"(?<![a-z0-9])saf(?:e|er|est|ely|ety)(?![a-z0-9])", "safe"),
    _phrase("SL14", "order-field", r"\b(?:at|for)(?:\s+|\s*~\s*)[+-]?\$?(?:[0-9][0-9,]*)?\.[0-9]+\b", "at 1.90"),
    _phrase("SL15", "claim", r"(?<![a-z0-9])(?:returns?|yield(?:s|ed|ing)?|income|gains?)(?![a-z0-9])",
            "a return per day"),
    _phrase("SL16", "claim", r"(?<![a-z0-9])not\s+(?:personali[sz]ed\s+)?(?:investment\s+)?advice(?![a-z0-9])"
            r"|(?<![a-z0-9])non[\s_-]*advice(?![a-z0-9])", "not investment advice"),
    _phrase("SL17", "advice", r"\b(?:my|our|top|clear)\s+(?:pick|choice|winner|favou?rite)s?\b"
            r"|\bit\s+(?:is|would\s+be)\s+(?:best|wise|smart|better)\s+to\b|(?:\bhad|'d)\s+better\b"
            r"|\b(?:desk|aegis|assistant|tool)\s+(?:suggests|prefers|favou?rs|advises)\b"
            r"|\b(?:desk|aegis|assistant|tool)'s\s+(?:suggestion|pick|choice|preference|favou?rite)s?\b"
            r"|\bno[\s_-]*brainer\b", "my pick"),
    _phrase("SL18", "claim", r"(?<![a-z0-9])sure(?:ly)?(?![a-z0-9])", "a sure gain"),
    _phrase("SL19", "order-kind", r"(?<![a-z0-9])(?:lmt|mkt|stp|gtd|gfd|ioc|fok|aon|moc|loc|opg|sto|bto|stc|btc)"
            r"(?![a-z0-9])|(?-i:(?<![A-Za-z0-9])DAY(?![A-Za-z0-9]))|\bgood\s+for\s+the\s+day\b"
            r"|(?<![a-z0-9])rout(?:e|es|ed|ing)(?![a-z0-9])", "LMT DAY"),
    _phrase("SL20", "order-field", r"(?<![a-z0-9])(?<!stressed )(?:limit|pay|prices?)(?:\s+[a-z]+){0,4}?\s+\(?\$?"
            r"[0-9]*\.[0-9]+", "limit of about 3.50"),
    _phrase("SL21", "trade-verb", r"(?<![a-z0-9])purchas(?:e|es|ed|ing)(?![a-z0-9])", "purchase"),
    _phrase("SL22", "claim", r"(?<![a-z0-9])(?:attractive|cheap(?:er|est|ly)?|bargains?|undervalued|overvalued"
            r"|confiden(?:ce|t|tly))(?![a-z0-9])", "an attractive call"),
)
"""The structuring mode's phrases (module notes): advice from any subject, probability, risk, return and non-advice
claims, urgency, suitability, a preference, an assurance, the bare instruction verb in any form, order-type, duration
and route abbreviations, a price after ``at`` or ``for`` (signed, with a dollar sign, thousands separators or no leading
digit; clocks, dates and counts carry no decimal point) or a few words after ``limit``, ``pay`` or ``price``, and a
trade verb outside the base table. ``SL14`` and ``SL20`` are ``order_field`` findings."""
STRUCTURING_LEG_START_RE = STRUCTURING_LEG_DETECT_RE
"""The name the leg detector had before the PR 3b review (a line start only): the detector now reads the whole line."""
_LEG_PRICE = r"[0-9]+\.[0-9]{2,}"
_LEG_SIDES = (r"(?:bid {p}(?:, ask {p})?(?:, mid {p})?|ask {p}(?:, mid {p})?|mid {p})").format(p=_LEG_PRICE)
_LEG_SOURCE = "(?:%s)" % "|".join(re.escape(words) for words in data_source.ALL_WORDS)
"""The source phrases a quote segment may name (:data:`aegis_desk.data_source.ALL_WORDS`), as one alternation."""
_LEG_TAIL = (r"(?: \| (?:{sides} \| quote grade [A-F], {source}, [0-9]+-min delayed, as of "
             r"(?:[01][0-9]|2[0-3]):[0-5][0-9] UTC|(?:bid|ask) {p} \| quote grade [A-F](?:, [0-9]+ min old)?|{sides})"
             r"(?: \| quote grade E: the conservative side stands)?)?$").format(sides=_LEG_SIDES, p=_LEG_PRICE,
                                                                       source=_LEG_SOURCE)
STRUCTURING_LEG_RES: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"^leg [0-9]+ of [0-9]+: (?:closes your (?:long|short)|opens a new (?:long|short)) [0-9]+ x "
               r"[A-Z][A-Z0-9]{0,5} [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]+(?:\.[0-9]+)? (?:call|put)" + _LEG_TAIL),
    re.compile(r"^leg [0-9]+ of [0-9]+: closes [0-9]+(?:\.[0-9]+)? of your long [A-Z][A-Z0-9.]{0,9} shares" + _LEG_TAIL),
)
"""The fixed leg grammar (module notes): an option leg closed or opened, or long shares closed, each followed only by
the quote segments the tools print (review L2-2: no free tail): ``| bid P, ask P, mid P | quote grade G, SOURCE,
N-min delayed, as of HH:MM UTC`` (SOURCE: one of :data:`aegis_desk.data_source.ALL_WORDS`), ``| ask P | quote grade G,
N min old``, the bare sides, and the grade E note."""
FILLER_WORDS: Tuple[str, ...] = ("so", "then", "and", "now", "please", "just", "simply", "also", "kindly")
"""Words skipped at a sentence start before the imperative test (``so close ...`` starts with its verb)."""
PLAN_CLOSE_PREFIX: str = "your plan: "
EXIT_SIGNAL_HEADER: str = "EXIT SIGNAL"

_WHITELIST_FORMS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("trade_id", re.compile(r"\bAG-[0-9]{8}-[0-9]{2,3}\b")),
    ("signal_id", re.compile(r"\bSIG-[0-9]{8}-[0-9]{3}\b")),
    ("group_id", re.compile(r"\bROW-[0-9]+\b")),
    ("rules_version", re.compile(r"\brules [0-9]+\.[0-9]+\.[0-9]+\b")),
    ("sha", re.compile(r"\bsha [0-9a-f]{8}\b")),
    ("clock", re.compile(r"\b(?:[01][0-9]|2[0-3]):[0-5][0-9] (?:UTC|ET)\b")),
)
_DATE_RE = re.compile(r"\b([0-9]{4})-([0-9]{2})-([0-9]{2})\b")
_RULE_ID_WORD_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,15}\b")
_RULE_ID_PATTERN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9_]{0,14}$")
_FOOTER_DELAY_RE = re.compile(r"data delayed (?P<delay>[0-9]+) min, as of ")
_DIGITS_RE = re.compile(r"(?<![0-9])\.?[0-9][0-9.,]*")
_WORD_RE = re.compile(r"[A-Za-z]+")
_SENTENCE_END_CHARS = ".!?;:|"
_HARD_BOUNDARY_CHARS = "!?;:|,"
_OPENERS = "\"'([{*_~>`#-"
_LIST_MARK_RE = re.compile(r"(?:[0-9]{1,3}[.)]|[A-Za-z]\))[ \t]+")
_IDENTIFIER_FORMS: Tuple[str, ...] = ("trade_id", "signal_id", "group_id", "rules_version", "sha", "date")


@dataclass(frozen=True)
class NumberToken:
    """One numeric token: its normalised ``text`` (sign included, trailing ``.``/``,`` removed), its span in the
    message (``start``/``end``, the sign or ``$`` included) and the 1-based ``line`` and ``column`` of ``start``."""

    text: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class Finding:
    """One lint finding: ``code`` (:data:`CODES`), the 1-based ``line`` and ``column`` (0 when the finding is
    about the whole message) and the one-line ``message``."""

    code: str
    line: int
    column: int
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "line": self.line, "column": self.column, "message": self.message}


@dataclass(frozen=True)
class LintResult:
    """``ok`` when nothing was found; ``problems`` are the findings' messages (strings, in order);
    ``numbers_checked`` counts the numeric tokens outside the whitelist."""

    ok: bool
    problems: List[str]
    findings: List[Finding] = field(default_factory=list)
    numbers_checked: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems), "findings": [f.as_dict() for f in self.findings],
                "numbers_checked": self.numbers_checked}


# --------------------------------------------------------------------------------------------
# Footer
# --------------------------------------------------------------------------------------------


def footer_line(as_of: datetime.datetime, delay_min: int = FOOTER_DELAY_MIN) -> str:
    """The fixed footer with the data's as-of time of day in UTC (``HH:MM``); ``as_of`` must be aware."""
    if not isinstance(as_of, datetime.datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("the footer's as-of instant must be an aware datetime")
    if isinstance(delay_min, bool) or not isinstance(delay_min, int) or delay_min < 0:
        raise ValueError("the footer's delay must be a non-negative integer number of minutes")
    clock = as_of.astimezone(datetime.timezone.utc).strftime("%H:%M")
    return "%s%d min, as of %s UTC" % (FOOTER_PREFIX, delay_min, clock)


def snapshot_footer_line(as_of: datetime.datetime, delay_min: int = FOOTER_DELAY_MIN) -> str:
    """The market-snapshot footer (module notes) with the data's as-of time of day in UTC."""
    return SNAPSHOT_FOOTER_PREFIX + footer_line(as_of, delay_min)[len(FOOTER_PREFIX):]


def structuring_footer_line(as_of: datetime.datetime, delay_min: int = FOOTER_DELAY_MIN) -> str:
    """The structuring footer (module notes) with the data's as-of time of day in UTC; ``as_of`` must be aware."""
    return STRUCTURING_FOOTER_PREFIX + footer_line(as_of, delay_min)[len(FOOTER_PREFIX):]


def _footer_re(footer: str) -> Any:
    if footer not in _FOOTER_RES:
        raise ValueError("footer must be one of %s, got %r" % (", ".join(FOOTER_KINDS), footer))
    return _FOOTER_RES[footer]


def footer_clock(text: str) -> Optional[str]:
    """The ``HH:MM`` of the message's footer (its last non-empty line), or ``None`` without a footer."""
    match = FOOTER_RE.match(_last_line(text))
    return match.group("clock") if match else None


def _last_line(text: str) -> str:
    lines = [line for line in text.split("\n") if line.strip()]
    return lines[-1] if lines else ""


# --------------------------------------------------------------------------------------------
# Tokens and the whitelist
# --------------------------------------------------------------------------------------------


def _position(text: str, index: int) -> Tuple[int, int]:
    line = text.count("\n", 0, index) + 1
    column = index - (text.rfind("\n", 0, index) + 1) + 1
    return line, column


def _sign_start(text: str, start: int) -> int:
    """The index where the token's sign starts, or ``start`` when the token is unsigned (module notes)."""
    cursor = start
    if cursor > 0 and text[cursor - 1] == "$":
        cursor -= 1
    if cursor > 0 and text[cursor - 1] in "+-":
        before = text[cursor - 2] if cursor >= 2 else ""
        if before == "" or not (before.isalnum() or before in "._"):
            return cursor - 1
    return start


def numeric_tokens(text: str) -> List[NumberToken]:
    """Every numeric token of ``text`` (module notes, "Numbers"), in order."""
    tokens: List[NumberToken] = []
    for match in _DIGITS_RE.finditer(text):
        digits = match.group(0).rstrip(".,")
        if not any(char.isdigit() for char in digits):
            continue
        start = match.start()
        sign_at = _sign_start(text, start)
        sign = text[sign_at] if sign_at < start and text[sign_at] in "+-" else ""
        line, column = _position(text, sign_at)
        tokens.append(NumberToken(sign + digits, sign_at, start + len(digits), line, column))
    return tokens


def _real_date(match: "re.Match[str]") -> bool:
    try:
        datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def whitelist_spans(text: str, rule_ids: Optional[Iterable[str]] = None, *,
                    footer: str = FOOTER_RULES) -> List[Tuple[int, int, str]]:
    """``(start, end, form)`` of every identifier span (module notes, "Identifier whitelist"); ``footer`` names the
    footer whose fixed delay is whitelisted."""
    footer_re = _footer_re(footer)
    spans: List[Tuple[int, int, str]] = []
    for form, pattern in _WHITELIST_FORMS:
        spans.extend((m.start(), m.end(), form) for m in pattern.finditer(text))
    spans.extend((m.start(), m.end(), "date") for m in _DATE_RE.finditer(text) if _real_date(m))
    known: Optional[Set[str]] = set(rule_ids) if rule_ids is not None else None
    for match in _RULE_ID_WORD_RE.finditer(text):
        word = match.group(0)
        if not any(char.isdigit() for char in word):
            continue
        if (known is not None and word in known) or (known is None and _RULE_ID_PATTERN_RE.match(word)):
            spans.append((match.start(), match.end(), "rule_id"))
    offset = 0
    for line in text.split("\n"):
        footer_match = footer_re.match(line)
        if footer_match and footer_match.group("delay") == str(FOOTER_DELAY_MIN):
            delay = _FOOTER_DELAY_RE.search(line)
            if delay is not None:
                spans.append((offset + delay.start("delay"), offset + delay.end("delay"), "footer_delay"))
        offset += len(line) + 1
    return sorted(spans)


def _covered(token: NumberToken, spans: Sequence[Tuple[int, int, str]]) -> bool:
    digits_start = token.end - len(token.text.lstrip("+-"))
    return any(start <= digits_start and token.end <= end for start, end, _form in spans)


def unwhitelisted_numbers(text: str, rule_ids: Optional[Iterable[str]] = None, *,
                          footer: str = FOOTER_RULES) -> List[NumberToken]:
    """The numeric tokens of ``text`` that the whitelist does not cover: what a formatter emits for a text it
    passes through, and what :func:`check` compares with the emitted strings."""
    spans = whitelist_spans(text, rule_ids, footer=footer)
    return [token for token in numeric_tokens(text) if not _covered(token, spans)]


def _normalise_emitted(emitted: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for entry in emitted:
        value = str(entry).strip()
        while value and value[-1] in "%x,":
            value = value[:-1].rstrip()
        value = value.replace("$", "")
        if value:
            out.add(value)
            out.update(token.text for token in numeric_tokens(value))
    return out


# --------------------------------------------------------------------------------------------
# Language
# --------------------------------------------------------------------------------------------


def _sentence_starts(text: str) -> List[int]:
    """Indices where a sentence's first word begins (module notes, "Language")."""
    starts: List[int] = []
    candidates = [0]
    for index, char in enumerate(text):
        after = text[index + 1] if index + 1 < len(text) else ""
        if char == "\n":
            candidates.append(index + 1)
        elif char in _HARD_BOUNDARY_CHARS or (char == "." and after in (" ", "\t")):
            candidates.append(index + 1)
        elif char == "-" and index > 0 and text[index - 1] in " \t" and (after in (" ", "\t") or (
                after == "-" and text[index + 2:index + 3] in (" ", "\t"))):
            candidates.append(index + 1)  # a dash between spaces: `` - `` or `` -- ``
    for cursor in candidates:
        while True:
            while cursor < len(text) and (text[cursor] in " \t" or text[cursor] in _OPENERS):
                cursor += 1
            mark = _LIST_MARK_RE.match(text, cursor)
            if mark is not None:
                cursor = mark.end()
                continue
            word = _WORD_RE.match(text, cursor)
            if word is not None and word.group(0).lower() in FILLER_WORDS and word.end() < len(text) \
                    and text[word.end()] in " \t,":
                cursor = word.end()
                while cursor < len(text) and text[cursor] in " \t,":
                    cursor += 1
                continue
            break
        if cursor < len(text) and text[cursor].isalpha():
            starts.append(cursor)
    return starts


def _imperative_exception(text: str, start: int, word: str, structuring: bool = False) -> bool:
    if word == IMPERATIVE_START_WORDS[0] and text[:start].endswith(PLAN_CLOSE_PREFIX) and not structuring:
        rest = text[start + len(word):]
        stripped = rest.lstrip(" \t")
        return stripped == "" or stripped[0] in "|(;\n"
    if word == "EXIT" and text.startswith(EXIT_SIGNAL_HEADER, start):
        after = text[start + len(EXIT_SIGNAL_HEADER):start + len(EXIT_SIGNAL_HEADER) + 1]
        return after in ("", " ", "\n")
    return False


# --------------------------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------------------------


def _footer_spans(text: str, footer: str = FOOTER_RULES) -> List[Tuple[int, int]]:
    """``(start, end)`` of every line that is exactly the footer in use (the only place a middle dot may stand)."""
    footer_re = _footer_re(footer)
    offset = 0
    spans: List[Tuple[int, int]] = []
    for line in text.split("\n"):
        if footer_re.match(line):
            spans.append((offset, offset + len(line)))
        offset += len(line) + 1
    return spans


def _clock_of(as_of: Any) -> str:
    """``HH:MM UTC`` of an aware instant or of its ISO-8601 text (``ValueError`` otherwise)."""
    moment = as_of
    if isinstance(moment, str):
        try:
            moment = datetime.datetime.fromisoformat(moment.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("as_of must be an ISO-8601 instant with a zone, got %r" % (as_of,)) from None
    if not isinstance(moment, datetime.datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("as_of must be an aware instant, got %r" % (as_of,))
    return moment.astimezone(datetime.timezone.utc).strftime("%H:%M UTC")


def check(text: str, emitted: Iterable[str], *, rule_ids: Optional[Iterable[str]] = None,
          identifiers: Optional[Iterable[str]] = None, clocks: Optional[Iterable[str]] = None,
          as_of: Any = None, footer: str = FOOTER_RULES) -> LintResult:
    """Lint ``text`` against the ``emitted`` number strings (module notes); ``rule_ids`` narrows the rule-id
    whitelist to those exact ids; ``identifiers`` and ``clocks`` narrow the id, sha, version, date and clock spans to
    those exact texts (strict mode), ``as_of`` pins the footer's clock, and ``footer`` (``rules``, the default,
    ``snapshot`` or ``structuring``) names the footer the last line must be; ``structuring`` reads ``rule_ids=None`` as
    ``[]`` and adds the structuring rules (module notes)."""
    footer_re = _footer_re(footer)
    structuring = footer == FOOTER_STRUCTURING
    findings: List[Finding] = []
    if not isinstance(text, str) or not text.strip():
        finding = Finding(CODE_EMPTY, 0, 0, "the message is empty")
        return LintResult(False, [finding.message], [finding], 0)
    footer_spans = _footer_spans(text, footer)
    for index, char in enumerate(text):
        if (" " <= char <= "~") or char in ("\n", "\t"):
            continue
        line, column = _position(text, index)
        if char == MIDDLE_DOT:
            if any(start <= index < end for start, end in footer_spans):
                continue
            findings.append(Finding(CODE_NON_ASCII, line, column, "line %d, column %d: the middle dot U+00B7 belongs to "
                                    "the footer line only" % (line, column)))
            continue
        findings.append(Finding(CODE_NON_ASCII, line, column, "line %d, column %d: character U+%04X is outside the "
                                "message alphabet (printable ASCII, newline, tab and the footer's middle dot)"
                                % (line, column, ord(char))))
    known = list(rule_ids) if rule_ids is not None else ([] if structuring else None)
    allowed_ids = set(identifiers) if identifiers is not None else None
    allowed_clocks = set(clocks) if clocks is not None else None
    for start, end, form in whitelist_spans(text, known, footer=footer):
        span = text[start:end]
        if (form in _IDENTIFIER_FORMS and allowed_ids is not None and span not in allowed_ids) or (
                form == "clock" and allowed_clocks is not None and span not in allowed_clocks):
            line, column = _position(text, start)
            findings.append(Finding(CODE_IDENTIFIER, line, column, "line %d, column %d: %s %s is not one the desk "
                                    "printed" % (line, column, form.replace("_", " "), span)))
    allowed = _normalise_emitted(emitted)
    checked = 0
    for token in unwhitelisted_numbers(text, known, footer=footer):
        checked += 1
        if token.text not in allowed:
            findings.append(Finding(CODE_NUMBER, token.line, token.column, "line %d, column %d: number %s is not in "
                                    "the emitted list" % (token.line, token.column, token.text)))
    reported: Set[Tuple[int, str]] = set()
    for entry in FORBIDDEN_LANGUAGE:
        for match in entry.pattern.finditer(text):
            key = (match.start(), entry.category)
            if key in reported:
                continue
            reported.add(key)
            line, column = _position(text, match.start())
            code = CODE_ORDER_FIELD if entry.category == "order-field" else CODE_FORBIDDEN
            findings.append(Finding(code, line, column, "line %d, column %d: forbidden language (%s, %s)"
                                    % (line, column, entry.category, entry.code)))
    if structuring:
        for number, line_text in enumerate(text.split("\n"), 1):
            looks_like_a_leg = STRUCTURING_LEG_DETECT_RE.search(line_text) or STRUCTURING_EFFECT_RE.search(line_text)
            if looks_like_a_leg and not any(leg.match(line_text) for leg in STRUCTURING_LEG_RES):
                findings.append(Finding(CODE_ORDER_FIELD, number, 1, "line %d, column 1: a leg line outside the fixed "
                                        "leg grammar" % number))
        for entry in STRUCTURING_FORBIDDEN_LANGUAGE:
            for match in entry.pattern.finditer(text):
                if any(start <= match.start() < end for start, end in footer_spans):
                    continue  # the footer line is the desk's own fixed text
                key = (match.start(), entry.category)
                if key in reported:
                    continue
                reported.add(key)
                line, column = _position(text, match.start())
                code = CODE_ORDER_FIELD if entry.category == "order-field" else CODE_FORBIDDEN
                findings.append(Finding(code, line, column, "line %d, column %d: forbidden language (%s, %s)"
                                        % (line, column, entry.category, entry.code)))
    start_words = IMPERATIVE_START_WORDS + (STRUCTURING_IMPERATIVE_START_WORDS if structuring else ())
    for start in _sentence_starts(text):
        word_match = _WORD_RE.match(text, start)
        if word_match is None:
            continue
        word = word_match.group(0)
        if word.lower() not in start_words or _imperative_exception(text, start, word, structuring):
            continue
        line, column = _position(text, start)
        index = start_words.index(word.lower()) + 1
        findings.append(Finding(CODE_IMPERATIVE, line, column, "line %d, column %d: imperative sentence start "
                                "(IS%02d of the lint's list)" % (line, column, index)))
    findings.sort(key=lambda f: (f.line, f.column, CODES.index(f.code)))
    last = footer_re.match(_last_line(text))
    if last is None:
        findings.append(Finding(CODE_FOOTER, 0, 0, "the last line is not the %s (%sN min, as of HH:MM UTC)"
                                % (_FOOTER_NAMES[footer], _FOOTER_PREFIXES[footer])))
    elif as_of is not None and last.group("clock") + " UTC" != _clock_of(as_of):
        findings.append(Finding(CODE_FOOTER, 0, 0, "the footer's as-of time %s UTC is not the data's %s"
                                % (last.group("clock"), _clock_of(as_of))))
    if footer == FOOTER_SNAPSHOT:
        content = _SNAPSHOT_RULE_CONTENT_RE.search(text)
        if content is not None or (known is not None and len(known) > 0):
            line = _position(text, content.start())[0] if content is not None else 0
            findings.append(Finding(CODE_FOOTER, line, 0, "the market-snapshot footer cannot close rule content (a rule, "
                                    "a verdict or a rulebook version%s): use the rules footer"
                                    % (", line %d" % line if content is not None else ": rule ids were given")))
    if structuring:
        content = _STRUCTURING_RULE_CONTENT_RE.search(text)
        if content is not None or (known is not None and len(known) > 0):
            line = _position(text, content.start())[0] if content is not None else 0
            findings.append(Finding(CODE_FOOTER, line, 0, "the structuring footer cannot close rule content (a rule, a "
                                    "verdict or a rulebook version%s): an alert keeps the rules footer"
                                    % (", line %d" % line if content is not None else ": rule ids were given")))
    else:
        content = _STRUCTURING_CONTENT_RE.search(text)
        if content is not None:
            line = _position(text, content.start())[0]
            findings.append(Finding(CODE_FOOTER, line, 0, "the %s cannot close a structuring text (a leg or a candidate, "
                                    "line %d): use the structuring footer" % (_FOOTER_NAMES[footer], line)))
    return LintResult(not findings, [finding.message for finding in findings], findings, checked)


EMITTED_DOCUMENT_KEYS: Tuple[str, ...] = ("emitted_numbers", "rule_ids", "identifiers", "clocks", "as_of")
"""The keys of an emitted document (``emitted_numbers`` required; the others select strict mode)."""
EMITTED_DOCUMENT_OPTIONAL_KEYS: Tuple[str, ...] = ("footer",)
"""Optional keys: ``footer`` (``rules`` when absent) names the footer the text is linted with."""


@dataclass(frozen=True)
class EmittedDocument:
    """An emitted-numbers file read by :func:`load_emitted`; :meth:`check` lints a text against all of it."""

    emitted_numbers: List[str]
    rule_ids: Optional[List[str]] = None
    identifiers: Optional[List[str]] = None
    clocks: Optional[List[str]] = None
    as_of: Optional[datetime.datetime] = None
    footer: str = FOOTER_RULES

    def check(self, text: str, footer: Optional[str] = None) -> LintResult:
        """Lint ``text`` against the whole document, with its own footer kind unless ``footer`` overrides it."""
        return check(text, self.emitted_numbers, rule_ids=self.rule_ids, identifiers=self.identifiers,
                     clocks=self.clocks, as_of=self.as_of, footer=self.footer if footer is None else footer)


def load_emitted(document: Any) -> EmittedDocument:
    """An emitted-numbers file's JSON: a list of strings (plain mode), or an object with the list ``emitted_numbers``
    and, optionally, ``rule_ids``, ``identifiers``, ``clocks`` (lists of strings) and ``as_of`` (an ISO-8601 instant
    with a zone); anything else is ``ValueError``."""
    if isinstance(document, list):
        return EmittedDocument(_strings(document, "the emitted list"))
    if not isinstance(document, dict):
        raise ValueError("the emitted document must be a JSON list of strings or an object with emitted_numbers")
    unknown = sorted(set(document) - set(EMITTED_DOCUMENT_KEYS) - set(EMITTED_DOCUMENT_OPTIONAL_KEYS))
    if unknown:
        raise ValueError("unknown keys in the emitted document: %s" % ", ".join(unknown))
    if "emitted_numbers" not in document:
        raise ValueError("the emitted document has no emitted_numbers list")
    lists = {key: (None if document.get(key) is None else _strings(document[key], key))
             for key in ("rule_ids", "identifiers", "clocks")}
    as_of = document.get("as_of")
    moment: Optional[datetime.datetime] = None
    if as_of is not None:
        if not isinstance(as_of, str):
            raise ValueError("as_of must be an ISO-8601 instant with a zone")
        _clock_of(as_of)
        moment = datetime.datetime.fromisoformat(as_of.strip().replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    footer = document.get("footer", FOOTER_RULES)
    if footer not in FOOTER_KINDS:
        raise ValueError("footer must be one of %s, got %r" % (", ".join(FOOTER_KINDS), footer))
    return EmittedDocument(_strings(document["emitted_numbers"], "emitted_numbers"), lists["rule_ids"],
                           lists["identifiers"], lists["clocks"], moment, footer)


def load_emitted_document(document: Any) -> Tuple[List[str], Optional[List[str]]]:
    """``(emitted, rule_ids)`` of :func:`load_emitted` (the plain pair; strict-mode keys are read and checked)."""
    loaded = load_emitted(document)
    return loaded.emitted_numbers, loaded.rule_ids


def _strings(value: Any, what: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("%s must be a list of strings" % what)
    return list(value)


__all__ = [
    "ALLOWED_EXTRA_CHARACTERS",
    "CODES",
    "CODE_EMPTY",
    "CODE_FOOTER",
    "CODE_IDENTIFIER",
    "CODE_FORBIDDEN",
    "CODE_IMPERATIVE",
    "CODE_NON_ASCII",
    "CODE_NUMBER",
    "CODE_ORDER_FIELD",
    "EMITTED_DOCUMENT_KEYS",
    "EMITTED_DOCUMENT_OPTIONAL_KEYS",
    "EXIT_SIGNAL_HEADER",
    "EmittedDocument",
    "FILLER_WORDS",
    "FOOTER_DELAY_MIN",
    "FOOTER_KINDS",
    "FOOTER_PREFIX",
    "FOOTER_RE",
    "FOOTER_RULES",
    "FOOTER_SNAPSHOT",
    "FOOTER_STRUCTURING",
    "FORBIDDEN_CATEGORIES",
    "FORBIDDEN_LANGUAGE",
    "FORBIDDEN_PATTERNS",
    "Finding",
    "WORD_SEPARATOR",
    "ForbiddenPhrase",
    "IMPERATIVE_START_WORDS",
    "LintResult",
    "MIDDLE_DOT",
    "NumberToken",
    "PLAN_CLOSE_PREFIX",
    "SNAPSHOT_FOOTER_PREFIX",
    "SNAPSHOT_FOOTER_RE",
    "STRUCTURING_FOOTER_PREFIX",
    "STRUCTURING_FOOTER_RE",
    "STRUCTURING_FORBIDDEN_LANGUAGE",
    "STRUCTURING_IMPERATIVE_START_WORDS",
    "STRUCTURING_EFFECT_RE",
    "STRUCTURING_LEG_DETECT_RE",
    "STRUCTURING_LEG_RES",
    "STRUCTURING_LEG_START_RE",
    "check",
    "footer_clock",
    "footer_line",
    "load_emitted",
    "load_emitted_document",
    "numeric_tokens",
    "snapshot_footer_line",
    "structuring_footer_line",
    "unwhitelisted_numbers",
    "whitelist_spans",
]
