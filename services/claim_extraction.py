"""Source-preserving claim candidate generation for AI-assisted extraction."""

from __future__ import annotations

import re
from typing import Any


_ABBREVIATIONS = {"dr", "e.g", "i.e", "mr", "mrs", "ms", "vs"}
_CLAUSE_START = re.compile(
    r"\b(?:and|but)\s+(?=(?:(?:our|this|it|they|we)\s+)?"
    r"(?:(?:clinically|visibly|gently|effectively)\s+)*"
    r"(?:is|are|has|have|contains|includes|guarantees?|provides?|offers?|"
    r"delivers?|reduces?|clears?|improves?|increases?|removes?|prevents?|"
    r"treats?|cures?|brightens?|hydrates?|fades?|works?)\b)",
    re.IGNORECASE,
)
_PREDICATE = re.compile(
    r"\b(?:is|are|has|have|contains|includes|guarantees?|provides?|offers?|"
    r"delivers?|reduces?|clears?|improves?|increases?|removes?|prevents?|"
    r"treats?|cures?|brightens?|hydrates?|fades?|works?)\b",
    re.IGNORECASE,
)


def _trim_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?](?=\s|$)|\n+", text):
        if match.group().startswith(("\n", "\r")):
            end = match.start()
        else:
            prefix = text[start:match.start()]
            final_word = re.search(r"([A-Za-z]+)$", prefix)
            if final_word and final_word.group(1).casefold() in _ABBREVIATIONS:
                continue
            if (
                match.start() > 0
                and match.end() < len(text)
                and text[match.start() - 1].isdigit()
                and text[match.end()].isdigit()
            ):
                continue
            end = match.end()

        trimmed = _trim_span(text, start, end)
        if trimmed:
            spans.append(trimmed)
        start = match.end()

    trimmed = _trim_span(text, start, len(text))
    if trimmed:
        spans.append(trimmed)
    return spans


def _clause_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    sentence = text[start:end]
    split_points: list[tuple[int, int]] = [
        (match.start(), match.end())
        for match in re.finditer(r";", sentence)
    ]
    for match in _CLAUSE_START.finditer(sentence):
        left = sentence[:match.start()]
        right_start = match.end()
        if _PREDICATE.search(left) and _PREDICATE.search(sentence[right_start:]):
            split_points.append((match.start(), match.end()))

    if not split_points:
        return [(start, end)]

    pieces: list[tuple[int, int]] = []
    piece_start = 0
    for split_start, split_end in sorted(split_points):
        left = _trim_span(text, start + piece_start, start + split_start)
        if left:
            pieces.append(left)
        piece_start = split_end
    right = _trim_span(text, start + piece_start, end)
    if right:
        pieces.append(right)
    return pieces if len(pieces) > 1 else [(start, end)]


def build_claim_candidates(text: str) -> list[dict[str, Any]]:
    """Split draft copy conservatively and retain exact Python source offsets."""
    spans = [
        piece
        for start, end in _sentence_spans(text)
        for piece in _clause_spans(text, start, end)
    ]
    candidates: list[dict[str, Any]] = []
    previous_end = -1
    for start, end in spans:
        if start < previous_end:
            continue
        if not text[start:end].strip():
            continue
        candidates.append(
            {
                "candidate_id": f"C{len(candidates) + 1:04d}",
                "text": text[start:end],
                "start_char": start,
                "end_char": end,
            }
        )
        previous_end = end
    return candidates
