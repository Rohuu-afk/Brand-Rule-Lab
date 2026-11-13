from __future__ import annotations

import json

from PIL import Image

from services import document_parser, video_ocr
from services.video_parser import (
    EphemeralKeyframe,
    KeyframeMetadata,
    VideoExtractionResult,
)


def _install_fake_video(
    monkeypatch,
    *,
    sampling_complete=True,
    warnings=(),
):
    def fake_extract(filename, data, *, on_keyframe):
        samples = []
        for index, timestamp in enumerate((125, 1_125)):
            metadata = KeyframeMetadata(index, timestamp, 200, 100)
            samples.append(metadata)
            image = Image.new("RGB", (200, 100), (index, 0, 0))
            try:
                on_keyframe(EphemeralKeyframe(metadata, image))
            finally:
                image.close()
        return VideoExtractionResult(
            duration_ms=2_000,
            frames=tuple(samples),
            decoded_frame_count=2,
            sampling_complete=sampling_complete,
            warnings=tuple(warnings),
        )

    monkeypatch.setattr(video_ocr, "extract_video", fake_extract)
    monkeypatch.setattr(document_parser, "_ocr_completeness_warnings", lambda *_: [])


def test_video_ocr_builds_exact_unicode_offsets_and_resolves_multiple_frames(monkeypatch):
    _install_fake_video(monkeypatch)
    line_by_frame = {
        0: {
            "text": "Claim café",
            "confidence": 0.91,
            "confidence_count": 1,
            "text_boxes": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}],
        },
        1: {
            "text": "second claim",
            "confidence": 0.87,
            "confidence_count": 1,
            "text_boxes": [{"x": 0.4, "y": 0.5, "width": 0.2, "height": 0.1}],
        },
    }
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [
            line_by_frame[image.getpixel((0, 0))[0]] | {"complete": True}
        ],
    )

    parsed = video_ocr.parse_video_evidence("sample.mp4", b"raw-video-bytes")
    evidence = parsed["media_evidence"]

    assert parsed["text"] == "Claim café\nsecond claim"
    assert parsed["page_map"] == {}
    assert parsed["source_type"] == "video"
    assert parsed["sampling_complete"] is True
    assert parsed["ocr_complete"] is True
    assert evidence["schema_version"] == 1
    assert evidence["source"] == {"source_type": "video"}
    assert evidence["sampling"]["status"] == "COMPLETE"
    assert evidence["ocr"]["status"] == "COMPLETE"
    assert evidence["ocr"]["warnings"] == []
    assert evidence["frames"][0]["timestamp_ms"] == 125
    assert evidence["frames"][1]["timestamp_ms"] == 1_125
    assert evidence["frames"][0]["width_px"] == 200
    assert evidence["frames"][0]["height_px"] == 100

    first, second = (
        evidence["frames"][0]["segments"][0],
        evidence["frames"][1]["segments"][0],
    )
    assert (first["start_char"], first["end_char"]) == (0, len("Claim café"))
    assert (second["start_char"], second["end_char"]) == (
        len("Claim café\n"),
        len(parsed["text"]),
    )
    assert parsed["text"][first["start_char"]:first["end_char"]] == first["text"]
    assert parsed["text"][second["start_char"]:second["end_char"]] == second["text"]
    assert first["ocr_confidence"] == 0.91
    assert first["text_boxes"] == [
        {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}
    ]

    matches = video_ocr.find_video_evidence_for_span(
        {"media_evidence": evidence},
        4,
        len("Claim café\nsec"),
    )
    assert [frame["frame_index"] for frame in matches] == [0, 1]
    assert [frame["timestamp_ms"] for frame in matches] == [125, 1_125]
    assert "raw-video-bytes" not in json.dumps(evidence)
    assert len(json.dumps(evidence).encode("utf-8")) <= video_ocr.MAX_VIDEO_OCR_METADATA_BYTES


def test_video_ocr_offsets_multiple_segments_within_and_across_frames(monkeypatch):
    _install_fake_video(monkeypatch)
    lines_by_frame = {
        0: [
            {"text": "évidence", "confidence": 0.9, "text_boxes": []},
            {"text": "second", "confidence": 0.8, "text_boxes": []},
        ],
        1: [{"text": "終わり", "confidence": 0.95, "text_boxes": []}],
    }

    def fake_ocr(image, **kwargs):
        frame_index = image.getpixel((0, 0))[0]
        return [
            line | {"confidence_count": 1, "complete": True}
            for line in lines_by_frame[frame_index]
        ]

    monkeypatch.setattr(document_parser, "ocr_pil_image", fake_ocr)
    parsed = video_ocr.parse_video_evidence("sample.mp4", b"video")
    segments = [
        segment
        for frame in parsed["media_evidence"]["frames"]
        for segment in frame["segments"]
    ]

    assert parsed["text"] == "évidence\nsecond\n終わり"
    assert [
        parsed["text"][segment["start_char"]:segment["end_char"]]
        for segment in segments
    ] == ["évidence", "second", "終わり"]
    assert [(segment["start_char"], segment["end_char"]) for segment in segments] == [
        (0, len("évidence")),
        (len("évidence\n"), len("évidence\nsecond")),
        (len("évidence\nsecond\n"), len(parsed["text"])),
    ]
    matches = video_ocr.find_video_evidence_for_span(
        {"media_evidence": parsed["media_evidence"]},
        len("évidence"),
        len("évidence\nsecond\n") + 1,
    )
    assert [frame["frame_index"] for frame in matches] == [0, 1]
    assert [len(frame["segments"]) for frame in matches] == [1, 1]


def test_per_frame_ocr_incompleteness_is_reported_with_recovered_text(monkeypatch):
    _install_fake_video(monkeypatch)
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [
            {
                "text": "Recovered text",
                "confidence": 0.9,
                "confidence_count": 2,
                "text_boxes": [],
                "complete": False,
            }
        ],
    )

    parsed = video_ocr.parse_video_evidence("sample.mp4", b"video")

    assert parsed["text"] == "Recovered text\nRecovered text"
    assert parsed["ocr_complete"] is False
    assert parsed["media_evidence"]["ocr"]["status"] == "INCOMPLETE"
    assert any("per-frame text or word limit" in warning for warning in parsed["ocr_warnings"])


def test_video_ocr_keeps_incomplete_zero_line_status(monkeypatch):
    _install_fake_video(monkeypatch)
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [
            {
                "record_type": "status",
                "status": "INCOMPLETE",
                "text": "",
                "confidence": None,
                "confidence_count": 0,
                "text_boxes": [],
                "text_boxes_complete": True,
                "complete": False,
            }
        ],
    )

    parsed = video_ocr.parse_video_evidence("sample.mp4", b"video")

    assert parsed["text"] == ""
    assert parsed["ocr_complete"] is False
    assert parsed["media_evidence"]["ocr"]["status"] == "INCOMPLETE"
    assert any("per-frame text or word limit" in warning for warning in parsed["ocr_warnings"])


def test_incomplete_sampling_and_frame_ocr_errors_remain_explicit(monkeypatch):
    _install_fake_video(
        monkeypatch,
        sampling_complete=False,
        warnings=("A sampling interval had no decoded frame.",),
    )
    calls = 0

    def ocr(image, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("decoder-independent OCR failure")
        return [
            {
                "text": "A complete claim line with enough words to avoid a sparse warning",
                "confidence": 0.9,
                "confidence_count": 1,
                "text_boxes": [],
                "complete": True,
            }
        ]

    monkeypatch.setattr(document_parser, "ocr_pil_image", ocr)
    parsed = video_ocr.parse_video_evidence("sample.mov", b"video")

    assert parsed["sampling_complete"] is False
    assert parsed["media_evidence"]["sampling"]["status"] == "INCOMPLETE"
    assert "A sampling interval had no decoded frame." in parsed["sampling_warnings"]
    assert parsed["ocr_complete"] is False
    assert parsed["media_evidence"]["ocr"]["status"] == "INCOMPLETE"
    assert any("Frame 2: OCR failed" in item for item in parsed["ocr_warnings"])


def test_empty_ocr_frames_warn_without_marking_ocr_incomplete(monkeypatch):
    _install_fake_video(monkeypatch)
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [],
    )

    parsed = video_ocr.parse_video_evidence("empty-frames.mp4", b"video")
    evidence = parsed["media_evidence"]

    assert parsed["text"] == ""
    assert len(evidence["frames"]) == 2
    assert [
        (frame["frame_index"], frame["timestamp_ms"], frame["width_px"], frame["height_px"])
        for frame in evidence["frames"]
    ] == [(0, 125, 200, 100), (1, 1_125, 200, 100)]
    assert all(frame["segments"] == [] for frame in evidence["frames"])
    assert evidence["sampling"]["status"] == "COMPLETE"
    assert evidence["sampling"]["complete"] is True
    assert evidence["ocr"]["status"] == "COMPLETE"
    assert evidence["ocr"]["complete"] is True
    assert evidence["ocr"]["warnings"] == [
        "No readable text was detected in the sampled video frames."
    ]


def test_text_and_box_limits_are_reported_without_silent_truncation(monkeypatch):
    _install_fake_video(monkeypatch)
    monkeypatch.setattr(video_ocr, "MAX_VIDEO_OCR_TEXT_CHARS", 5)
    monkeypatch.setattr(video_ocr, "MAX_VIDEO_OCR_BOXES_PER_SEGMENT", 1)
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [
            {
                "text": "Too long text",
                "confidence": 0.9,
                "confidence_count": 1,
                "text_boxes": [
                    {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
                    {"x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1},
                ],
                "complete": True,
            }
        ],
    )

    parsed = video_ocr.parse_video_evidence("sample.mp4", b"video")

    assert parsed["text"] == ""
    assert parsed["ocr_complete"] is False
    assert any("5-character text limit" in item for item in parsed["ocr_warnings"])
    assert len(json.dumps(parsed["media_evidence"]).encode("utf-8")) <= (
        video_ocr.MAX_VIDEO_OCR_METADATA_BYTES
    )

    monkeypatch.setattr(video_ocr, "MAX_VIDEO_OCR_TEXT_CHARS", 100)
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [
            {
                "text": "Text",
                "confidence": 0.9,
                "confidence_count": 1,
                "text_boxes": [
                    {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
                    {"x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1},
                ],
                "complete": True,
            }
        ],
    )
    parsed_with_box_limit = video_ocr.parse_video_evidence("sample.mp4", b"video")
    segment = parsed_with_box_limit["media_evidence"]["frames"][0]["segments"][0]
    assert segment["text"] == "Text"
    assert segment["text_boxes_complete"] is False
    assert len(segment["text_boxes"]) == 1
    assert parsed_with_box_limit["ocr_complete"] is False
    assert any("bounding-box data" in item for item in parsed_with_box_limit["ocr_warnings"])


def test_segment_and_metadata_limits_report_incomplete_ocr(monkeypatch):
    _install_fake_video(monkeypatch)
    monkeypatch.setattr(video_ocr, "MAX_VIDEO_OCR_SEGMENTS", 1)
    monkeypatch.setattr(
        document_parser,
        "ocr_pil_image",
        lambda image, **kwargs: [
            {
                "text": "A complete claim with sufficient words for this evidence segment",
                "confidence": 0.9,
                "confidence_count": 1,
                "text_boxes": [],
                "complete": True,
            },
            {
                "text": "A second segment beyond the configured cap",
                "confidence": 0.9,
                "confidence_count": 1,
                "text_boxes": [],
                "complete": True,
            },
        ],
    )
    parsed = video_ocr.parse_video_evidence("sample.mp4", b"video")

    assert len(parsed["media_evidence"]["frames"][0]["segments"]) == 1
    assert parsed["text"] == parsed["media_evidence"]["frames"][0]["segments"][0]["text"]
    assert parsed["ocr_complete"] is False
    assert any("1-segment limit" in item for item in parsed["ocr_warnings"])

    monkeypatch.setattr(video_ocr, "MAX_VIDEO_OCR_SEGMENTS", 600)
    monkeypatch.setattr(video_ocr, "MAX_VIDEO_OCR_METADATA_BYTES", 16_300)
    parsed_at_metadata_limit = video_ocr.parse_video_evidence("sample.mp4", b"video")
    assert parsed_at_metadata_limit["text"] == ""
    assert parsed_at_metadata_limit["ocr_complete"] is False
    assert any("metadata limit" in item for item in parsed_at_metadata_limit["ocr_warnings"])
