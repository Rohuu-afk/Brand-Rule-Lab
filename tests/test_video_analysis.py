from __future__ import annotations

from pathlib import Path

import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
from PIL import Image, ImageDraw

import services.document_parser as parser


def _synthetic_video(path: Path) -> bytes:
    writer = imageio_ffmpeg.write_frames(
        str(path), (640, 360), fps=2, codec="mpeg4", macro_block_size=1
    )
    writer.send(None)
    for _ in range(8):
        image = Image.new("RGB", (640, 360), "white")
        ImageDraw.Draw(image).text((80, 130), "SAVE 20 PERCENT", fill="black")
        writer.send(image.tobytes())
    writer.close()
    return path.read_bytes()


def test_mp4_keyframes_are_sampled_and_ocr_text_keeps_frame_locations(
    monkeypatch, tmp_path
):
    import pytesseract

    created_dirs = []
    real_temp_directory = parser.tempfile.TemporaryDirectory

    def track_temp_directory(*args, **kwargs):
        temp_directory = real_temp_directory(*args, **kwargs)
        created_dirs.append(temp_directory)
        return temp_directory

    monkeypatch.setattr(parser.tempfile, "TemporaryDirectory", track_temp_directory)
    video = _synthetic_video(tmp_path / "synthetic.mp4")
    monkeypatch.setattr(parser, "_configure_tesseract", lambda _module: "")
    monkeypatch.setattr(
        pytesseract,
        "image_to_data",
        lambda *_args, **_kwargs: {
            "text": ["SAVE", "20", "PERCENT"],
            "conf": ["90", "95", "85"],
        },
    )

    parsed = parser.parse_file("campaign.mp4", video)

    assert parsed["source_type"] == "video"
    assert parsed["word_count"] == 15
    assert "SAVE 20 PERCENT" in parsed["text"]
    assert parsed["video_metadata"]["duration_seconds"] == pytest.approx(4.0, abs=0.1)
    assert 2 <= parsed["video_metadata"]["sampled_keyframes"] <= parser.MAX_VIDEO_KEYFRAMES
    assert parsed["page_map"] == {0: 1, 16: 2, 32: 3, 48: 4, 64: 5}
    assert parsed["page_records"][0]["timestamp"] == "00:00.00"
    assert parsed["page_records"][1]["timestamp_seconds"] > 0
    assert "unsampled" in parsed["video_metadata"]["coverage_note"].lower()
    assert created_dirs and all(not Path(item.name).exists() for item in created_dirs)


def test_video_signature_is_checked_before_decoding():
    with pytest.raises(parser.ParseError, match="does not match its extension"):
        parser.parse_file("fake.mp4", b"not a real mp4")


def test_video_upload_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(parser, "MAX_VIDEO_BYTES", 8)
    with pytest.raises(parser.ResourceLimitError, match="configured .* MB limit"):
        parser.parse_file("large.mp4", b"\x00\x00\x00\x18ftypmp42" + b"x" * 12)


def test_video_duration_limit_is_enforced_before_keyframe_extraction(
    monkeypatch, tmp_path
):
    class _Probe:
        def __next__(self):
            return {"source_size": (640, 360), "duration": 121.0}

        def close(self):
            return None

    monkeypatch.setattr(
        imageio_ffmpeg, "read_frames", lambda *_args, **_kwargs: _Probe()
    )
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    video = video_path.read_bytes()
    with pytest.raises(parser.ResourceLimitError, match="limit is 120 seconds"):
        parser.parse_file("long.mp4", video)
