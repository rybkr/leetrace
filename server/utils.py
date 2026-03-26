"""Shared utility functions for the LeetRace server."""

import re

# Maps ASCII digits to their Unicode superscript equivalents.
_SUP_DIGITS = str.maketrans(
    "0123456789", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"
)

# Maps ASCII subscript index letters to Unicode subscript equivalents.
_SUB_LETTERS = str.maketrans("ij", "\u1d62\u2c7c")


def fix_exponents(text: str) -> str:
    """Convert exponent notation in problem descriptions to Unicode superscripts.

    Handles two forms that appear after scraping LeetCode HTML:
    - Explicit caret:  '10^5'  → '10⁵',  '2^31' → '2³¹'
    - Collapsed form:  '105'   → '10⁵'   (standalone base-10 exponents 4–9)
    - Collapsed 2^31/32: '231' → '2³¹',  '232'  → '2³²'
    """
    # Explicit carets: 10^5 → 10⁵
    text = re.sub(
        r"(\d+)\^(\d+)",
        lambda m: m.group(1) + m.group(2).translate(_SUP_DIGITS),
        text,
    )
    # Collapsed base-10 exponents: standalone 10[4-9] → 10⁴..10⁹
    text = re.sub(
        r"(?<!\d)(-?)10([4-9])(?!\d)",
        lambda m: m.group(1) + "10" + m.group(2).translate(_SUP_DIGITS),
        text,
    )
    # Collapsed 2^31 / 2^32: standalone 231 or 232 → 2³¹ or 2³²
    text = re.sub(
        r"(?<!\d)(-?)2(3[12])(?!\d)",
        lambda m: m.group(1) + "2" + m.group(2).translate(_SUP_DIGITS),
        text,
    )
    return text


def _sub(suffix: str) -> str:
    """Convert 'i' or 'j' to Unicode subscript."""
    return suffix.translate(_SUB_LETTERS)


def fix_subscripts(text: str) -> str:
    """Restore collapsed subscripts from scraped LeetCode HTML.

    When HTML like ``a<sub>i</sub>`` is scraped to plain text, subscript
    indices collapse into the variable name (``ai``).  This function
    re-inserts the visual separation using Unicode subscript letters.

    Patterns handled:
    - Bracket definitions:  ``[arrivali, timei]`` → ``[arrivalᵢ, timeᵢ]``
    - Multi-letter (4+ chars) standalone: ``arrivali`` → ``arrivalᵢ``
    - Two-char (``ai``, ``bj``) in math/constraint contexts
    """

    # --- Pass 1: bracket definitions [wordi, wordj, ...] ----
    # Match content inside [...] that follows '= ' (definition context)
    def _fix_bracket(m: re.Match) -> str:
        inner = m.group(1)
        # Replace words ending in i/j inside the bracket
        fixed = re.sub(
            r"\b([a-zA-Z]+?)([ij])\b",
            lambda w: w.group(1) + _sub(w.group(2)),
            inner,
        )
        return "[" + fixed + "]"

    text = re.sub(r"\[([a-zA-Z]+[ij](?:,\s*[a-zA-Z]+[ij])*)\]", _fix_bracket, text)

    # --- Pass 2: multi-letter collapsed subscripts (4+ chars) ---
    # e.g. arrivali → arrivalᵢ, pointsi → pointsᵢ
    # Skip content inside double quotes to avoid mangling string literals.
    _STOP_WORDS = frozenset(
        {
            "mini",
            "maxi",
            "taxi",
            "semi",
            "anti",
            "multi",
            "ascii",
            "wiki",
            "khaki",
            "alibi",
            "alumni",
            "bikini",
            "chili",
            "safari",
            "sushi",
        }
    )

    def _fix_multi(m: re.Match) -> str:
        if m.group(0).lower() in _STOP_WORDS:
            return m.group(0)
        return m.group(1) + _sub(m.group(2))

    # Split on quoted strings so we only transform outside them
    parts = re.split(r'("(?:[^"\\]|\\.)*")', text)
    for idx, part in enumerate(parts):
        if idx % 2 == 0:  # outside quotes
            part = re.sub(r"\b([a-zA-Z]{3,})([ij])\b", _fix_multi, part)
        parts[idx] = part
    text = "".join(parts)

    # --- Pass 3: two-char subscripts (ai, bi, xj...) in math contexts ---
    # Only transform when adjacent to comparison operators
    text = re.sub(
        r"(?<=<=\s)([a-zA-Z])([ij])\b", lambda m: m.group(1) + _sub(m.group(2)), text
    )
    text = re.sub(
        r"\b([a-zA-Z])([ij])(?=\s*<=)", lambda m: m.group(1) + _sub(m.group(2)), text
    )
    text = re.sub(
        r"\b([a-zA-Z])([ij])(?=\s*!=)", lambda m: m.group(1) + _sub(m.group(2)), text
    )
    text = re.sub(
        r"(?<=!=\s)([a-zA-Z])([ij])\b", lambda m: m.group(1) + _sub(m.group(2)), text
    )

    return text
