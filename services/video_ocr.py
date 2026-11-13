"""OCR sampled video frames and retain bounded, source-linked text evidence."""

from __future__ import annotations

import json
from typing import Any

from services import document_parser
from services.video_parser import EphemeralKeyframe, extract_video

MAX_VIDEO_OCR_TEXT_CHARS = 30_000
MAX_VIDEO_OCR_SEGMENTS = 600
MAX_VIDEO_OCR_WORDS_PER_FRAME = 5_000
MAX_VIDEO_OCR_BOXES_PER_SEGMENT = document_parser.MAX_OCR_BOXES_PER_LINE
MAX_VIDEO_OCR_METADATA_BYTES = 512 * 1024
_METADATA_FINALIZATION_RESERVE = 16 * 1024
_MAX_OCR_WARNINGS = 40


class VideoOCRError(ValueError):
    """Raised when bounded video OCR evidence cannot be represented safely."""


def _metadata_size(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _add_warning(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        if len(warnings) < _MAX_OCR_WARNINGS - 1:
            warnings.append(warning)
        elif "Additional OCR warnings were omitted." not in warnings:
            warnings.append("Additional OCR warnings were omitted.")


def parse_video_evidence(filename: str, data: bytes) -> dict[str, Any]:
    """Extract sampled frames, OCR them synchronously, and return bounded evidence.

    The decoder's worker deadline does not cover OCR work inside the callback.
    """
    frames: list[dict[str, Any]] = []
    text_parts: list[str] = []
    text_length = 0
    segment_count = 0
    sampling_warnings: list[str] = []
    ocr_warnings: list[str] = []
    ocr_complete = True
    stop_ocr = False

    media_evidence: dict[str, Any] = {
        "schema_version": 1,
        "source": {"source_type": "video"},
        "sampling": {
            "status": "INCOMPLETE",
            "complete": False,
            "duration_ms": 0,
            "sample_interval_ms": 1_000,
            "warnings": [],
        },
        "ocr": {"status": "INCOMPLETE", "complete": False, "warnings": []},
        "frames": frames,
    }

    def consume_frame(sample: EphemeralKeyframe) -> None:
        nonlocal text_length, segment_count, ocr_complete, stop_ocr
        metadata = sample.metadata.to_dict()
        frame_record = {**metadata, "segments": []}
        frames.append(frame_record)
        if stop_ocr:
            return

        try:
            ocr_lines = document_parser.ocr_pil_image(
                sample.image,
                max_words=MAX_VIDEO_OCR_WORDS_PER_FRAME,
                max_text_chars=MAX_VIDEO_OCR_TEXT_CHARS,
                max_boxes_per_line=MAX_VIDEO_OCR_BOXES_PER_SEGMENT,
            )
        except Exception as exc:
            ocr_complete = False
            message = (
                str(exc) if isinstance(exc, document_parser.ParseError)
                else f"OCR failed ({type(exc).__name__})."
            )
            _add_warning(
                ocr_warnings,
                f"Frame {metadata['frame_index'] + 1}: {message}",
            )
            if document_parser._is_tesseract_unavailable(exc):
                stop_ocr = True
            return

        frame_confidence = document_parser._aggregate_ocr_confidence(ocr_lines)
        if not all(line.get("complete", True) for line in ocr_lines):
            ocr_complete = False
            _add_warning(
                ocr_warnings,
                f"Frame {metadata['frame_index'] + 1}: OCR output reached a per-frame text or word limit.",
            )
        frame_texts = [line["text"] for line in ocr_lines if line["text"]]
        if frame_texts:
            for warning in document_parser._ocr_completeness_warnings(
                "\n".join(frame_texts), frame_confidence
            ):
                ocr_complete = False
                _add_warning(
                    ocr_warnings,
                    f"Frame {metadata['frame_index'] + 1}: {warning}",
                )
        if frame_confidence < 0.55 and frame_texts:
            _add_warning(
                ocr_warnings,
                f"Frame {metadata['frame_index'] + 1}: OCR confidence is low; verify the text against the original.",
            )

        for line in ocr_lines:
            line_text = line["text"]
            if not line_text:
                continue
            if segment_count >= MAX_VIDEO_OCR_SEGMENTS:
                ocr_complete = False
                stop_ocr = True
                _add_warning(
                    ocr_warnings,
                    f"OCR stopped at the {MAX_VIDEO_OCR_SEGMENTS}-segment limit.",
                )
                break

            separator = "\n" if text_parts else ""
            start_char = text_length + len(separator)
            end_char = start_char + len(line_text)
            if end_char > MAX_VIDEO_OCR_TEXT_CHARS:
                ocr_complete = False
                stop_ocr = True
                _add_warning(
                    ocr_warnings,
                    f"OCR stopped at the {MAX_VIDEO_OCR_TEXT_CHARS}-character text limit.",
                )
                break

            boxes = line["text_boxes"]
            boxes_complete = (
                len(boxes) <= MAX_VIDEO_OCR_BOXES_PER_SEGMENT
                and line.get("text_boxes_complete", True)
            )
            if not boxes_complete:
                boxes = boxes[:MAX_VIDEO_OCR_BOXES_PER_SEGMENT]
                ocr_complete = False
                _add_warning(
                    ocr_warnings,
                    f"Frame {metadata['frame_index'] + 1}: bounding-box data exceeded its per-segment limit.",
                )
            segment = {
                "segment_id": f"S{segment_count + 1:04d}",
                "text": line_text,
                "start_char": start_char,
                "end_char": end_char,
                "ocr_confidence": line["confidence"],
                "text_boxes": boxes,
                "text_boxes_complete": boxes_complete,
            }
            frame_record["segments"].append(segment)
            if _metadata_size(media_evidence) > (
                MAX_VIDEO_OCR_METADATA_BYTES - _METADATA_FINALIZATION_RESERVE
            ):
                frame_record["segments"].pop()
                ocr_complete = False
                stop_ocr = True
                _add_warning(
                    ocr_warnings,
                    f"OCR stopped at the {MAX_VIDEO_OCR_METADATA_BYTES}-byte metadata limit.",
                )
                break

            text_parts.append(line_text)
            text_length = end_char
            segment_count += 1

    extraction = extract_video(filename, data, on_keyframe=consume_frame)
    sampling_warnings.extend(extraction.warnings)
    if not extraction.sampling_complete:
        sampling_warnings.append("Video keyframe sampling is incomplete.")
    if extraction.frames and ocr_complete and not text_parts:
        _add_warning(
            ocr_warnings,
            "No readable text was detected in the sampled video frames.",
        )

    media_evidence["sampling"] = {
        "status": "COMPLETE" if extraction.sampling_complete else "INCOMPLETE",
        "complete": extraction.sampling_complete,
        "duration_ms": extraction.duration_ms,
        "sample_interval_ms": 1_000,
        "warnings": list(dict.fromkeys(sampling_warnings)),
    }
    media_evidence["ocr"] = {
        "status": "COMPLETE" if ocr_complete else "INCOMPLETE",
        "complete": ocr_complete,
        "warnings": ocr_warnings,
    }
    if _metadata_size(media_evidence) > MAX_VIDEO_OCR_METADATA_BYTES:
        raise VideoOCRError(
            "Video OCR evidence exceeded its configured metadata-size limit."
        )

    raw_text = "\n".join(text_parts)
    return {
        "text": raw_text,
        "page_map": {},
        "source_type": "video",
        "word_count": len(raw_text.split()),
        "media_evidence": media_evidence,
        "sampling_warnings": list(media_evidence["sampling"]["warnings"]),
        "ocr_warnings": list(ocr_warnings),
        "sampling_complete": extraction.sampling_complete,
        "ocr_complete": ocr_complete,
    }


def find_video_evidence_for_span(
    visual_metadata: Any, start_char: Any, end_char: Any
) -> list[dict[str, Any]]:
    """Return all frames with segments intersecting a zero-based half-open span."""
    if isinstance(visual_metadata, str):
        try:
            visual_metadata = json.loads(visual_metadata)
        except json.JSONDecodeError:
            return []
    if not isinstance(visual_metadata, dict):
        return []
    media_evidence = visual_metadata.get("media_evidence")
    if not isinstance(media_evidence, dict):
        return []
    evidence_frames = media_evidence.get("frames")
    if not isinstance(evidence_frames, list):
        return []
    try:
        start, end = int(start_char), int(end_char)
    except (TypeError, ValueError):
        return []
    if start < 0 or end <= start:
        return []

    matches: list[dict[str, Any]] = []
    for frame in evidence_frames:
        if not isinstance(frame, dict):
            continue
        frame_segments = frame.get("segments")
        if not isinstance(frame_segments, list):
            continue
        segments = []
        for segment in frame_segments:
            if not isinstance(segment, dict):
                continue
            try:
                segment_start = int(segment["start_char"])
                segment_end = int(segment["end_char"])
            except (KeyError, TypeError, ValueError):
                continue
            if segment_start < end and segment_end > start:
                segments.append(segment)
        if segments:
            matches.append(
                {
                    "frame_index": frame.get("frame_index"),
                    "timestamp_ms": frame.get("timestamp_ms"),
                    "segments": segments,
                }
            )
    return matches
