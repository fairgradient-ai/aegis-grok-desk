#!/usr/bin/env python3
"""build_graphics.py: render the README graphics of Aegis Desk as small, self-contained SVG files. Stdlib only.

Usage::

    python3 assets/src/build_graphics.py            # write the five SVG files into assets/
    python3 assets/src/build_graphics.py --check    # exit 1 if a committed file differs from a fresh render

Files written (each a dark card that reads on a light or a dark page, with ``role="img"``, ``<title>`` and ``<desc>``,
and under :data:`MAX_BYTES`):

* ``hero.svg`` and ``hero-mobile.svg``: the desk's name, what it does for an options holder, and one real alert line
  from the fixture desk (no number in it).
* ``desk-flow.svg`` and ``desk-flow-mobile.svg``: your positions, marked by the tools, checked against your adopted
  rules, an alert in your chat, and you act in your broker; "What are my choices?" goes to the Structuring Desk, on
  request.
* ``demo-card.svg``: the exact output of ``python3 scripts/opening_bell.py --demo`` in a terminal card. The text is
  produced by running the tool from this checkout at build time (it writes nothing); long lines are soft-wrapped at
  word boundaries, as a narrow terminal would, and the unwrapped text is repeated in ``<desc>``.

No fonts are embedded: the text uses the reader's ``system-ui`` and ``monospace`` font stacks, so text widths are
estimated and every line is kept well inside its box. Animation is CSS only (a slow pulse), and it is switched off under
``prefers-reduced-motion``. Original artwork for Aegis Desk; no third-party art, logo or font.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.dirname(HERE)
ROOT = os.path.dirname(ASSETS)
MAX_BYTES = 60 * 1024
SVG_NS = "http://www.w3.org/2000/svg"

SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace"

# Palette: ink and slate with an azure line and an amber threshold.
INK = "#0c1322"
INK_2 = "#121c30"
PANEL = "#17233a"
PANEL_2 = "#1d2b46"
EDGE = "#2c3b5c"
TEXT = "#e9eef7"
MUTED = "#a3b1c9"
FAINT = "#8a98b3"
AZURE = "#62b4ff"
AMBER = "#f3b641"
CORAL = "#ff8a73"

# One real alert from the fixture desk (book_check on the expiry_day set): the text parts that carry no number.
ALERT_HEAD = "WATCH AG-20261028-03 | SPY protective put | your rule ROLL30"
ALERT_PLAN = "your plan: review a roll"
ALERT_TAIL = "not an instruction to trade; you act in your brokerage account."

DEMO_COMMAND = "python3 scripts/opening_bell.py --demo"


def style(extra: str = "") -> str:
    return ("<style>"
            ".s{font-family:%s}.m{font-family:%s}"
            ".tx{fill:%s}.mu{fill:%s}.fa{fill:%s}.az{fill:%s}.am{fill:%s}.co{fill:%s}"
            ".b{font-weight:700}.sb{font-weight:600}"
            "@keyframes pulse{0%%,100%%{opacity:.3}50%%{opacity:1}}"
            ".pulse{animation:pulse 3.6s ease-in-out infinite}"
            ".d1{animation-delay:.6s}.d2{animation-delay:1.2s}.d3{animation-delay:1.8s}.d4{animation-delay:2.4s}"
            "%s"
            "@media (prefers-reduced-motion:reduce){.pulse{animation:none;opacity:1}}"
            "</style>") % (SANS, MONO, TEXT, MUTED, FAINT, AZURE, AMBER, CORAL, extra)


def svg_open(width: int, height: int, title: str, desc: str, extra_style: str = "") -> List[str]:
    return [
        '<svg xmlns="%s" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-labelledby="t d">'
        % (SVG_NS, width, height, width, height),
        '<title id="t">%s</title>' % escape(title),
        '<desc id="d">%s</desc>' % escape(desc),
        style(extra_style),
        "<defs>"
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>'
        '<marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0L10 5L0 10z" fill="%s"/></marker>'
        '<marker id="ara" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0L10 5L0 10z" fill="%s"/></marker>'
        "</defs>" % (INK, INK_2, MUTED, AMBER),
        '<rect x="0.5" y="0.5" width="%d" height="%d" rx="18" fill="url(#bg)" stroke="%s"/>' % (width - 1, height - 1, EDGE),
    ]


def text(x: float, y: float, value: str, cls: str, size: float, anchor: str = "start") -> str:
    anchor_attr = "" if anchor == "start" else ' text-anchor="%s"' % anchor
    return '<text x="%s" y="%s" class="%s" font-size="%s"%s xml:space="preserve">%s</text>' % (
        num(x), num(y), cls, num(size), anchor_attr, escape(value))


def lines(x: float, y: float, values: Sequence[str], cls: str, size: float, leading: float, anchor: str = "start") -> List[str]:
    return [text(x, y + index * leading, value, cls, size, anchor) for index, value in enumerate(values)]


def num(value: float) -> str:
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded == int(rounded) else ("%.2f" % rounded).rstrip("0")


def wrap(value: str, width: int) -> List[str]:
    """Word-wrap ``value`` to at most ``width`` characters a line (a word longer than ``width`` is split)."""
    out: List[str] = []
    current = ""
    for word in value.split(" "):
        while len(word) > width:
            if current:
                out.append(current)
                current = ""
            out.append(word[:width])
            word = word[width:]
        candidate = word if not current else current + " " + word
        if len(candidate) <= width:
            current = candidate
        else:
            out.append(current)
            current = word
    if current or not out:
        out.append(current)
    return out


def shield(cx: float, top: float, width: float, height: float) -> List[str]:
    """The emblem: a shield outline holding a payoff line that rises, then levels at a cap, with an amber threshold."""
    half = width / 2
    left, right, bottom = cx - half, cx + half, top + height
    body = ("M{l} {t}Q{cx} {tq} {r} {t}L{r} {m}Q{r} {mb} {cx} {b}Q{l} {mb} {l} {m}Z").format(
        l=num(left), r=num(right), t=num(top + height * 0.08), tq=num(top - height * 0.04), cx=num(cx),
        m=num(top + height * 0.55), mb=num(top + height * 0.86), b=num(bottom))
    inner_left, inner_right = left + width * 0.18, right - width * 0.18
    low, cap = top + height * 0.68, top + height * 0.34
    knee = cx + width * 0.08
    payoff = "M%s %sL%s %sL%s %s" % (num(inner_left), num(low), num(knee), num(cap), num(inner_right), num(cap))
    threshold = top + height * 0.46
    cross_x = inner_left + (knee - inner_left) * (low - threshold) / (low - cap)
    return [
        '<path d="%s" fill="%s" stroke="%s" stroke-width="2.5"/>' % (body, PANEL, AZURE),
        '<path d="%s" fill="none" stroke="%s" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>'
        % (payoff, AZURE),
        '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" stroke-dasharray="5 5"/>'
        % (num(inner_left - 4), num(threshold), num(inner_right + 4), num(threshold), AMBER),
        '<circle cx="%s" cy="%s" r="9" fill="%s" opacity=".25" class="pulse"/>' % (num(cross_x), num(threshold), AMBER),
        '<circle cx="%s" cy="%s" r="4.5" fill="%s"/>' % (num(cross_x), num(threshold), AMBER),
    ]


def alert_bubble(x: float, y: float, width: float, rows: Sequence[Tuple[str, str]], size: float, leading: float) -> List[str]:
    """A chat message from the Exit Sentinel: a header, then monospace rows ``(text, class)``."""
    height = 44 + leading * len(rows) + 6
    out = [
        '<rect x="%s" y="%s" width="%s" height="%s" rx="14" fill="%s" stroke="%s"/>'
        % (num(x), num(y), num(width), num(height), PANEL, EDGE),
        '<rect x="%s" y="%s" width="4" height="%s" rx="2" fill="%s"/>' % (num(x + 12), num(y + 14), num(height - 28), AMBER),
        '<circle cx="%s" cy="%s" r="9" fill="%s" stroke="%s"/>' % (num(x + 34), num(y + 22), PANEL_2, AZURE),
        text(x + 34, y + 26, "E", "s b az", 10, "middle"),
        text(x + 50, y + 26.5, "Exit Sentinel", "s sb tx", 13),
        text(x + 140, y + 26.5, "in your desk chat", "s mu", 12),
    ]
    for index, (value, cls) in enumerate(rows):
        out.append(text(x + 28, y + 50 + index * leading, value, "m " + cls, size))
    return out


def hero_desktop() -> str:
    width, height = 800, 400
    out = svg_open(width, height, "Aegis Desk",
                   "Aegis Desk: exit alerts from rules you adopted, for the options you already hold. A shield holds a "
                   "payoff line with an amber threshold. A chat message from the Exit Sentinel reads: " + ALERT_HEAD
                   + "; " + ALERT_PLAN + "; " + ALERT_TAIL + " The desk never places, modifies or cancels an order.")
    out += [
        '<g opacity=".5">%s</g>' % "".join(
            '<line x1="40" y1="%d" x2="760" y2="%d" stroke="%s" stroke-width="1"/>' % (y, y, PANEL_2) for y in (64, 236, 352)),
    ]
    out += shield(128, 64, 132, 164)
    out += [text(236, 104, "Aegis Desk", "s b tx", 40)]
    out += lines(238, 142, ["Exit alerts from rules you adopted,", "for the options you already hold."], "s tx", 20, 27)
    out += lines(238, 210, ["It never places, modifies or cancels an order.", "You decide and act in your own broker."],
                 "s mu", 14.5, 20)
    out += alert_bubble(236, 252, 524, [(ALERT_HEAD, "tx"), (ALERT_PLAN, "am"), (ALERT_TAIL, "mu")], 12, 19)
    out += [text(64, 300, "read-only", "s sb fa", 12), text(64, 318, "sandbox market data", "s sb fa", 12),
            text(64, 336, "numbers from tools", "s sb fa", 12)]
    out.append("</svg>")
    return "\n".join(out) + "\n"


def hero_mobile() -> str:
    width, height = 400, 470
    out = svg_open(width, height, "Aegis Desk",
                   "Aegis Desk: exit alerts from rules you adopted, for the options you already hold. A shield holds a "
                   "payoff line with an amber threshold. A chat message from the Exit Sentinel reads: " + ALERT_HEAD
                   + "; " + ALERT_PLAN + "; " + ALERT_TAIL + " The desk never places, modifies or cancels an order.")
    out += shield(200, 34, 92, 114)
    out += [text(200, 192, "Aegis Desk", "s b tx", 32, "middle")]
    out += lines(200, 224, ["Exit alerts from rules you adopted,", "for the options you already hold."], "s tx", 16.5, 22,
                 "middle")
    out += lines(200, 274, ["It never places, modifies or cancels an order.", "You decide and act in your own broker."],
                 "s mu", 12.5, 18, "middle")
    rows = [(part, "tx") for part in wrap(ALERT_HEAD, 42)] + [(ALERT_PLAN, "am")] + [
        (part, "mu") for part in wrap(ALERT_TAIL, 42)]
    out += alert_bubble(20, 312, 360, rows, 10.5, 16.5)
    out.append("</svg>")
    return "\n".join(out) + "\n"


FLOW_STAGES: Tuple[Tuple[str, str, str], ...] = (
    ("1", "Your positions", "The book you keep in positions.csv, drafted from your own words."),
    ("2", "Marked by the tools", "Quotes, P/L and Greeks from deterministic tools, never from the AI."),
    ("3", "Your adopted rules", "Only the exit rules you said yes to are checked."),
    ("4", "An alert in your chat", "Which of your rules fired and why, with the data's age."),
    ("5", "You act in your broker", "The desk never places, modifies or cancels orders."),
)
FLOW_ASK = "“What are my choices?”"
FLOW_STRUCTURING = ("Structuring Desk", "on request", "Scores choices for a position you hold, by your own criteria.")
FLOW_NOTE = ("Every number comes from a tool, with its source and time. Data that cannot be fetched is UNAVAILABLE, "
             "never a guess.")
FLOW_DESC = ("How the desk works: 1 your positions; 2 marked by the tools; 3 checked against your adopted rules; 4 an alert "
             "in your chat; 5 you act in your broker. When you ask \"What are my choices?\", the Structuring Desk scores "
             "choices for a position you hold by your own criteria, on request, and you still decide in your broker. "
             + FLOW_NOTE)


def stage_card(x: float, y: float, width: float, height: float, number: str, title: str, body: str, title_wrap: int,
               body_wrap: int, title_size: float, body_size: float, accent: str, delay: str) -> List[str]:
    out = [
        '<rect x="%s" y="%s" width="%s" height="%s" rx="12" fill="%s" stroke="%s"/>'
        % (num(x), num(y), num(width), num(height), PANEL, EDGE),
        '<circle cx="%s" cy="%s" r="12" fill="%s" stroke="%s" stroke-width="1.5"/>'
        % (num(x + 24), num(y + 24), PANEL_2, accent),
        '<circle cx="%s" cy="%s" r="16" fill="none" stroke="%s" stroke-width="1" class="pulse %s"/>'
        % (num(x + 24), num(y + 24), accent, delay),
        text(x + 24, y + 28.5, number, "s b tx", 12, "middle"),
    ]
    title_lines = wrap(title, title_wrap)
    out += lines(x + 14, y + 60, title_lines, "s b tx", title_size, title_size + 4)
    body_top = y + 60 + len(title_lines) * (title_size + 4) + 6
    out += lines(x + 14, body_top, wrap(body, body_wrap), "s mu", body_size, body_size + 5)
    return out


def flow_desktop() -> str:
    width, height = 800, 452
    out = svg_open(width, height, "How the desk works", FLOW_DESC)
    out += [text(20, 40, "HOW THE DESK WORKS", "s sb fa", 12), text(20, 62, "From the positions you hold to the decision you make",
                                                                     "s sb tx", 17)]
    card_w, gap, top, card_h = 136, 20, 84, 182
    delays = ("", "d1", "d2", "d3", "d4")
    for index, (number, title, body) in enumerate(FLOW_STAGES):
        x = 20 + index * (card_w + gap)
        accent = AMBER if index == 3 else AZURE
        out += stage_card(x, top, card_w, card_h, number, title, body, 14, 18, 13, 11.5, accent, delays[index])
        if index < len(FLOW_STAGES) - 1:
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" marker-end="url(#ar)"/>'
                       % (num(x + card_w + 3), num(top + card_h / 2), num(x + card_w + gap - 3), num(top + card_h / 2), MUTED))
    # The branch: from the alert, the owner asks; the Structuring Desk answers on request; the owner still decides.
    alert_x = 20 + 3 * (card_w + gap) + card_w / 2
    act_x = 20 + 4 * (card_w + gap)
    chip_x, chip_y, chip_w, chip_h = 404, 320, 214, 40
    out += [
        '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" stroke-dasharray="4 4" marker-end="url(#ara)"/>'
        % (num(alert_x), num(top + card_h + 3), num(alert_x), num(chip_y - 3), AMBER),
        '<rect x="%s" y="%s" width="%s" height="%s" rx="20" fill="%s" stroke="%s"/>'
        % (num(chip_x), num(chip_y), num(chip_w), num(chip_h), PANEL_2, AMBER),
        text(chip_x + 16, chip_y + 25, "You ask", "s sb am", 12.5),
        text(chip_x + 70, chip_y + 25, FLOW_ASK, "s tx", 13),
        '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" stroke-dasharray="4 4" marker-end="url(#ara)"/>'
        % (num(chip_x + chip_w + 3), num(chip_y + chip_h / 2), num(act_x - 3), num(chip_y + chip_h / 2), AMBER),
    ]
    sx, sy, sw, sh = act_x, 300, card_w, 132
    out += [
        '<rect x="%s" y="%s" width="%s" height="%s" rx="12" fill="%s" stroke="%s" stroke-dasharray="5 4"/>'
        % (num(sx), num(sy), num(sw), num(sh), PANEL, AMBER),
        text(sx + 14, sy + 26, FLOW_STRUCTURING[0], "s b tx", 13.5),
        text(sx + 14, sy + 44, FLOW_STRUCTURING[1], "s sb am", 11.5),
    ]
    out += lines(sx + 14, sy + 66, wrap(FLOW_STRUCTURING[2], 19), "s mu", 11.5, 16.5)
    out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" stroke-dasharray="4 4" '
               'marker-end="url(#ara)"/>' % (num(sx + sw / 2), num(sy - 3), num(sx + sw / 2), num(top + card_h + 4), AMBER))
    out += lines(20, 336, wrap(FLOW_NOTE, 52), "s mu", 12.5, 19)
    out.append("</svg>")
    return "\n".join(out) + "\n"


def flow_mobile() -> str:
    width = 400
    card_x, card_w, gap = 20, 316, 22
    top = 104
    rows = len(FLOW_STAGES)
    bodies = [wrap(body, 34) for _number, _title, body in FLOW_STAGES]
    heights = [62 + 17 * len(body) for body in bodies]
    tops = [top + sum(heights[:index]) + index * gap for index in range(rows)]
    branch_top = tops[-1] + heights[-1] + 34
    height = int(branch_top + 196 + 104)
    out = svg_open(width, height, "How the desk works", FLOW_DESC)
    out += [text(20, 38, "HOW THE DESK WORKS", "s sb fa", 11.5)]
    out += lines(20, 60, ["From the positions you hold", "to the decision you make"], "s sb tx", 16, 21)
    delays = ("", "d1", "d2", "d3", "d4")
    for index, (number, title, _body) in enumerate(FLOW_STAGES):
        y, card_h = tops[index], heights[index]
        accent = AMBER if index == 3 else AZURE
        out += [
            '<rect x="%s" y="%s" width="%s" height="%s" rx="12" fill="%s" stroke="%s"/>'
            % (num(card_x), num(y), num(card_w), num(card_h), PANEL, EDGE),
            '<circle cx="%s" cy="%s" r="12" fill="%s" stroke="%s" stroke-width="1.5"/>'
            % (num(card_x + 26), num(y + 28), PANEL_2, accent),
            '<circle cx="%s" cy="%s" r="16" fill="none" stroke="%s" stroke-width="1" class="pulse %s"/>'
            % (num(card_x + 26), num(y + 28), accent, delays[index]),
            text(card_x + 26, y + 32.5, number, "s b tx", 12, "middle"),
            text(card_x + 50, y + 33, title, "s b tx", 14.5),
        ]
        out += lines(card_x + 50, y + 55, bodies[index], "s mu", 12, 17)
        if index < rows - 1:
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" marker-end="url(#ar)"/>'
                       % (num(card_x + 26), num(y + card_h + 3), num(card_x + 26), num(y + card_h + gap - 3), MUTED))
    # The branch runs from the alert card (4) down the right margin to the owner's question.
    alert_y = tops[3] + heights[3] / 2
    rail = card_x + card_w + 22
    chip_y = branch_top
    out += [
        '<path d="M%s %sH%sV%s" fill="none" stroke="%s" stroke-width="2" stroke-dasharray="4 4"/>'
        % (num(card_x + card_w + 3), num(alert_y), num(rail), num(chip_y + 20), AMBER),
        '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" stroke-dasharray="4 4" marker-end="url(#ara)"/>'
        % (num(rail), num(chip_y + 20), num(card_x + card_w + 3), num(chip_y + 20), AMBER),
        '<rect x="%s" y="%s" width="%s" height="40" rx="20" fill="%s" stroke="%s"/>'
        % (num(card_x), num(chip_y), num(card_w), PANEL_2, AMBER),
        text(card_x + 18, chip_y + 25, "You ask", "s sb am", 12.5),
        text(card_x + 74, chip_y + 25, FLOW_ASK, "s tx", 13.5),
    ]
    sy = chip_y + 66
    out += [
        '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="2" stroke-dasharray="4 4" marker-end="url(#ara)"/>'
        % (num(card_x + 26), num(chip_y + 43), num(card_x + 26), num(sy - 3), AMBER),
        '<rect x="%s" y="%s" width="%s" height="104" rx="12" fill="%s" stroke="%s" stroke-dasharray="5 4"/>'
        % (num(card_x), num(sy), num(card_w), PANEL, AMBER),
        text(card_x + 18, sy + 28, FLOW_STRUCTURING[0], "s b tx", 14.5),
        text(card_x + 150, sy + 28, FLOW_STRUCTURING[1], "s sb am", 12),
    ]
    out += lines(card_x + 18, sy + 52, wrap(FLOW_STRUCTURING[2] + " You still decide in your broker.", 40), "s mu", 12, 17)
    out += lines(20, sy + 140, wrap(FLOW_NOTE, 52), "s mu", 11.5, 17)
    out.append("</svg>")
    return "\n".join(out) + "\n"


def demo_output() -> str:
    """The exact stdout of ``python3 scripts/opening_bell.py --demo`` from this checkout (it writes nothing)."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("TRADIER_API_TOKEN", None)
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "opening_bell.py"), "--demo"], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    if proc.returncode != 0:
        raise SystemExit("opening_bell.py --demo exited %d: %s" % (proc.returncode, (proc.stdout + proc.stderr).strip()))
    return proc.stdout


def demo_card(output: str) -> str:
    columns, size, leading = 96, 12.5, 18.5
    out_lines = output.rstrip("\n").split("\n")
    rows: List[Tuple[str, str]] = []
    for index, line in enumerate(out_lines):
        cls = "am" if index == 0 else ("fa" if index == len(out_lines) - 1 else "tx")
        rows += [(part, cls) for part in wrap(line, columns)]
    width = 800
    body_top = 96
    height = int(body_top + leading * len(rows) + 30)
    out = svg_open(width, height, "Aegis Desk demo output",
                   "A terminal running " + DEMO_COMMAND + " (synthetic sample data from the desk's test fixtures). "
                   "Its exact output:\n" + output.rstrip("\n"))
    out += [
        '<rect x="1" y="1" width="%d" height="40" rx="17" fill="%s"/>' % (width - 2, INK_2),
        '<rect x="1" y="24" width="%d" height="17" fill="%s"/>' % (width - 2, INK_2),
        '<line x1="1" y1="41" x2="%d" y2="41" stroke="%s"/>' % (width - 1, EDGE),
        '<rect x="20" y="13" width="15" height="15" rx="4" fill="none" stroke="%s" stroke-width="1.5"/>' % AZURE,
        '<path d="M24 17.5l3 3-3 3" fill="none" stroke="%s" stroke-width="1.5" stroke-linecap="round"/>' % AZURE,
        text(44, 25.5, "aegis-grok-desk", "s sb mu", 12.5),
        text(780, 25.5, "synthetic sample data", "s sb am", 12, "end"),
        text(24, 72, "$", "m az", size),
        text(40, 72, DEMO_COMMAND, "m tx b", size),
    ]
    for index, (value, cls) in enumerate(rows):
        out.append(text(24, body_top + index * leading, value, "m " + cls, size))
    cursor_y = body_top + len(rows) * leading - 10
    out.append('<rect x="24" y="%s" width="8" height="14" fill="%s" class="pulse"/>' % (num(cursor_y), AZURE))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def validate(name: str, document: str) -> List[str]:
    """Problems with one rendered SVG: well-formed XML, the size limit, ``role="img"``, a ``<title>`` and a ``<desc>``."""
    problems: List[str] = []
    data = document.encode("utf-8")
    if len(data) >= MAX_BYTES:
        problems.append("%s: %d bytes, the limit is under %d" % (name, len(data), MAX_BYTES))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return problems + ["%s: not well-formed XML: %s" % (name, exc)]
    if root.tag != "{%s}svg" % SVG_NS:
        problems.append("%s: the root element is %s, not svg" % (name, root.tag))
    if root.get("role") != "img":
        problems.append('%s: the svg element lacks role="img"' % name)
    for child in ("title", "desc"):
        found = root.find("{%s}%s" % (SVG_NS, child))
        if found is None or not (found.text or "").strip():
            problems.append("%s: no <%s>" % (name, child))
    return problems


def render() -> Dict[str, str]:
    return {
        "hero.svg": hero_desktop(),
        "hero-mobile.svg": hero_mobile(),
        "desk-flow.svg": flow_desktop(),
        "desk-flow-mobile.svg": flow_mobile(),
        "demo-card.svg": demo_card(demo_output()),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed files; write nothing")
    args = parser.parse_args(argv)
    rendered = render()
    problems = [problem for name, document in rendered.items() for problem in validate(name, document)]
    if problems:
        for problem in problems:
            print("error: %s" % problem)
        return 1
    stale: List[str] = []
    for name, document in rendered.items():
        path = os.path.join(ASSETS, name)
        if args.check:
            try:
                with open(path, encoding="utf-8") as handle:
                    if handle.read() != document:
                        stale.append(name)
            except OSError:
                stale.append(name)
            continue
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
    if stale:
        print("stale: %s (run python3 assets/src/build_graphics.py)" % ", ".join(stale))
        return 1
    for name, document in rendered.items():
        print("%s %s (%d bytes)" % ("ok" if args.check else "wrote", "assets/" + name, len(document.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
