"""Exact-quote matching against parsed PDF text, shared by both report pipelines.

Only PDF typography may differ: compatibility glyphs, curly quotes, line-wrapped
hyphens and, as a fallback, word spacing lost or added by text extraction.
Numbers, symbols, negation and in-line hyphens stay literal.
"""

from __future__ import annotations

import re

_WRAPPED_HYPHEN = "\x00"
# Spacing-only fallback is too permissive for very short quotes.
_MIN_SPACING_FALLBACK_CHARS = 20
_DIGIT_RUN = re.compile(r"\d+")


def _quote_index(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Typography-only comparison with offsets back into the unchanged source.

    PDF ligatures and curly quotes are compatibility glyphs. A line-ending
    hyphen can either wrap a word or be part of a compound. Mark each occurrence
    separately; ordinary in-line hyphens and mathematical symbols stay literal.
    """
    chars: list[tuple[str, int, int]] = []
    replacements = {
        "’": "'", "“": '"', "”": '"', "‐": "-", "‑": "-",
        "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    }
    for position, char in enumerate(text):
        if char == "­":
            continue
        for lowered in replacements.get(char, char).lower():
            chars.append((lowered, position, position + 1))
    out: list[str] = []
    offsets: list[tuple[int, int]] = []
    index = 0
    while index < len(chars):
        char, start, end = chars[index]
        if char == "-" and index > 0 and chars[index - 1][0].isalpha():
            following = index + 1
            while following < len(chars) and chars[following][0].isspace():
                following += 1
            wrapped = any(c[0] in {"\n", "\r"} for c in chars[index + 1:following])
            if wrapped and following < len(chars) and chars[following][0].isalpha():
                before = index - 1
                while before >= 0 and chars[before][0].isalpha():
                    before -= 1
                after = following
                while after < len(chars) and chars[after][0].isalpha():
                    after += 1
                # Single-letter operands can be an ASCII subtraction across
                # lines (x-\ny), so their hyphen must never be optional.
                may_join = index - before - 1 > 1 and after - following > 1
                out.append(_WRAPPED_HYPHEN if may_join else char)
                offsets.append((start, end))
                index = following
                continue
        if char.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                offsets.append((start, end))
        else:
            out.append(char)
            offsets.append((start, end))
        index += 1
    if out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def _pattern(needle: str) -> str:
    pattern: list[str] = []
    for index, char in enumerate(needle):
        if index and needle[index - 1].isalpha() and char.isalpha():
            pattern.append(f"{_WRAPPED_HYPHEN}?")
        if char == _WRAPPED_HYPHEN:
            pattern.append(f"[-{_WRAPPED_HYPHEN}]?")
        elif char == "-":
            pattern.append(f"[-{_WRAPPED_HYPHEN}]")
        else:
            pattern.append(re.escape(char))
    return "".join(pattern)


def _without_spaces(text: str, offsets: list[tuple[int, int]]) -> tuple[str, list[tuple[int, int]]]:
    kept = [(char, offset) for char, offset in zip(text, offsets, strict=True) if char != " "]
    return "".join(char for char, _ in kept), [offset for _, offset in kept]


def find_source_quote(source: str, excerpt: str, *, allow_spacing_variants: bool = True) -> str | None:
    """Return the actual source substring after typography-only exact matching.

    Anchor corrections pass ``allow_spacing_variants=False``: moving a quote to
    another source needs a strict match, while verifying it in its own source
    may also absorb extraction spacing.
    """
    # The marker is internal to this comparison, never accepted as source data.
    if _WRAPPED_HYPHEN in source or _WRAPPED_HYPHEN in excerpt:
        return None
    needle, _ = _quote_index(excerpt)
    if not needle:
        return None
    haystack, offsets = _quote_index(source)
    match = re.search(_pattern(needle), haystack)
    if match is not None:
        return source[offsets[match.start()][0]:offsets[match.end() - 1][1]]
    if not allow_spacing_variants:
        return None
    return _find_spacing_variant(source, excerpt, needle, haystack, offsets)


def _find_spacing_variant(
    source: str, excerpt: str, needle: str, haystack: str, offsets: list[tuple[int, int]],
) -> str | None:
    """Fallback for extraction that glued or split words: ignore spaces only.

    A space between digits is meaningful ("10 5" is not "105"), so every
    candidate must keep the excerpt's digit runs unchanged.
    """
    compact_needle = needle.replace(" ", "")
    if len(compact_needle) < _MIN_SPACING_FALLBACK_CHARS:
        return None
    compact, compact_offsets = _without_spaces(haystack, offsets)
    digit_runs = _DIGIT_RUN.findall(excerpt)
    for match in re.finditer(_pattern(compact_needle), compact):
        quote = source[compact_offsets[match.start()][0]:compact_offsets[match.end() - 1][1]]
        if _DIGIT_RUN.findall(quote) == digit_runs:
            return quote
    return None
