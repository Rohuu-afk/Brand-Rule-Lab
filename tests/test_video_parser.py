import json
import multiprocessing
import os
import sys
from fractions import Fraction
from io import BytesIO
from types import SimpleNamespace
import time

import pytest
from PIL import Image

from services import video_parser


class FakeFFmpegError(Exception):
    pass


def _test_png_bytes():
    image = Image.new("RGB", (4, 3), "blue")
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _worker_wait_without_metadata(sender, acknowledgment):
    time.sleep(30)


def _worker_timeout_after_one_frame(sender, acknowledgment):
    sender.send(("header", 2_000, 2))
    sender.send(
        (
            "frame",
            {"frame_index": 0, "timestamp_ms": 0, "width_px": 4, "height_px": 3},
            1,
            _test_png_bytes(),
        )
    )
    acknowledgment.acquire()
    time.sleep(30)


def _worker_send_error(sender, acknowledgment):
    sender.send(("error", "VideoWorkerError", "RuntimeError"))


def _worker_crash(sender, acknowledgment):
    os._exit(23)


def _worker_send_two_frames(sender, acknowledgment):
    sender.send(("header", 2_000, 2))
    for frame_index in range(2):
        sender.send(
            (
                "frame",
                {
                    "frame_index": frame_index,
                    "timestamp_ms": frame_index * 1_000,
                    "width_px": 4,
                    "height_px": 3,
                },
                frame_index + 1,
                _test_png_bytes(),
            )
        )
        acknowledgment.acquire()
    sender.send(("result", 2_000, 2, True, []))


class FakeFrame:
    def __init__(self, pts, width=64, height=48, time_base=Fraction(1, 1_000)):
        self.pts = pts
        self.width = width
        self.height = height
        self.time_base = time_base
        self.rendered_image = None

    def to_image(self):
        self.rendered_image = Image.new("RGB", (self.width, self.height), "white")
        return self.rendered_image


class FakeContainer:
    def __init__(
        self,
        frames=(),
        *,
        duration_ms=3_000,
        width=64,
        height=48,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        video_stream=True,
        decode_error=None,
    ):
        self.format = SimpleNamespace(name=format_name)
        self.duration = duration_ms * 1_000 if duration_ms is not None else None
        self.streams = SimpleNamespace(
            video=(
                [
                    SimpleNamespace(
                        width=width,
                        height=height,
                        duration=duration_ms,
                        time_base=Fraction(1, 1_000),
                        index=7,
                    )
                ]
                if video_stream
                else []
            )
        )
        self.frames = list(frames)
        self.decode_error = decode_error
        self.closed = False
        self.decode_arguments = None

    def decode(self, **kwargs):
        self.decode_arguments = kwargs
        yield from self.frames
        if self.decode_error is not None:
            raise self.decode_error

    def close(self):
        self.closed = True


def _install_fake_av(monkeypatch, container=None, open_error=None):
    captured = {}

    def fake_open(source, mode):
        captured["source"] = source
        captured["mode"] = mode
        if open_error is not None:
            raise open_error
        captured["container"] = container
        return container

    fake_av = SimpleNamespace(
        error=SimpleNamespace(FFmpegError=FakeFFmpegError),
        time_base=1_000_000,
        open=fake_open,
    )
    monkeypatch.setitem(sys.modules, "av", fake_av)
    return captured


@pytest.mark.parametrize("filename", ["campaign.mp4", "campaign.MOV"])
def test_extract_video_samples_pts_and_delivers_ephemeral_images(monkeypatch, filename):
    frames = [
        FakeFrame(0),
        FakeFrame(333),
        FakeFrame(1_001),
        FakeFrame(1_550),
        FakeFrame(2_510),
    ]
    container = FakeContainer(frames, duration_ms=3_000)
    captured = _install_fake_av(monkeypatch, container)

    def observe_frame(item):
        callback_items.append((item.metadata, item.image.getpixel((0, 0))))

    callback_items = []

    result = video_parser._extract_video_core(
        filename,
        b"synthetic-video",
        on_keyframe=observe_frame,
    )

    assert result.sampling_complete
    assert result.duration_ms == 3_000
    assert [frame.frame_index for frame in result.frames] == [0, 1, 2]
    assert [frame.timestamp_ms for frame in result.frames] == [0, 1_001, 2_510]
    assert [(frame.width_px, frame.height_px) for frame in result.frames] == [
        (64, 48),
        (64, 48),
        (64, 48),
    ]
    assert [item[0] for item in callback_items] == list(result.frames)
    assert all(item[1] == (255, 255, 255) for item in callback_items)
    for frame in frames:
        if frame.rendered_image is not None:
            with pytest.raises(ValueError, match="closed"):
                frame.rendered_image.getpixel((0, 0))
    assert captured["source"].closed
    assert container.closed
    assert container.decode_arguments == {"video": 7}


def test_rejects_unsupported_extension_before_opening_decoder(monkeypatch):
    monkeypatch.setattr(
        video_parser.multiprocessing,
        "get_context",
        lambda method: pytest.fail("A worker must not be created for an unsupported extension."),
    )

    with pytest.raises(video_parser.UnsupportedVideoError, match="not supported"):
        video_parser.extract_video("campaign.avi", b"video")


def test_rejects_non_mp4_mov_container_and_missing_video_stream(monkeypatch):
    wrong_container = FakeContainer(format_name="matroska,webm")
    _install_fake_av(monkeypatch, wrong_container)
    with pytest.raises(video_parser.UnsupportedVideoError, match="container"):
        video_parser._extract_video_core("campaign.mp4", b"video")
    assert wrong_container.closed

    no_video = FakeContainer(video_stream=False)
    _install_fake_av(monkeypatch, no_video)
    with pytest.raises(video_parser.UnsupportedVideoError, match="video stream"):
        video_parser._extract_video_core("campaign.mov", b"video")
    assert no_video.closed


def test_invalid_container_is_reported_as_corrupt_and_closed(monkeypatch):
    captured = _install_fake_av(monkeypatch, open_error=FakeFFmpegError("corrupt"))

    with pytest.raises(video_parser.InvalidVideoError, match="could not be opened"):
        video_parser._extract_video_core("campaign.mp4", b"corrupt")

    assert captured["source"].closed


def test_upload_size_duration_and_dimension_limits(monkeypatch):
    container = FakeContainer()
    _install_fake_av(monkeypatch, container)
    with pytest.raises(video_parser.VideoResourceLimitError, match="10 MiB"):
        video_parser.extract_video(
            "large.mp4", b"x" * (video_parser.MAX_VIDEO_BYTES + 1)
        )
    assert not container.closed

    too_long = FakeContainer(duration_ms=video_parser.MAX_VIDEO_DURATION_MS + 1)
    _install_fake_av(monkeypatch, too_long)
    with pytest.raises(video_parser.VideoResourceLimitError, match="duration"):
        video_parser._extract_video_core("long.mp4", b"video")
    assert too_long.closed

    too_wide = FakeContainer(width=video_parser.MAX_VIDEO_WIDTH_PX + 1)
    _install_fake_av(monkeypatch, too_wide)
    with pytest.raises(video_parser.VideoResourceLimitError, match="dimensions"):
        video_parser._extract_video_core("wide.mp4", b"video")
    assert too_wide.closed

    too_tall_frame = FakeContainer(
        [FakeFrame(0, height=video_parser.MAX_VIDEO_HEIGHT_PX + 1)]
    )
    _install_fake_av(monkeypatch, too_tall_frame)
    with pytest.raises(video_parser.VideoResourceLimitError, match="dimensions"):
        video_parser._extract_video_core("tall-frame.mp4", b"video")
    assert too_tall_frame.closed

    unknown_duration = FakeContainer(duration_ms=None)
    _install_fake_av(monkeypatch, unknown_duration)
    with pytest.raises(video_parser.InvalidVideoError, match="duration"):
        video_parser._extract_video_core("unknown-duration.mp4", b"video")
    assert unknown_duration.closed

    too_many_pixels = FakeContainer(width=11, height=10)
    _install_fake_av(monkeypatch, too_many_pixels)
    monkeypatch.setattr(video_parser, "MAX_VIDEO_FRAME_PIXELS", 100)
    with pytest.raises(video_parser.VideoResourceLimitError, match="pixels"):
        video_parser._extract_video_core("pixels.mp4", b"video")
    assert too_many_pixels.closed


def test_sampling_gaps_and_midstream_decoder_failure_are_incomplete(monkeypatch):
    gap_container = FakeContainer(
        [FakeFrame(0), FakeFrame(2_100)],
        duration_ms=3_000,
    )
    _install_fake_av(monkeypatch, gap_container)
    gap_result = video_parser._extract_video_core("gaps.mp4", b"video")
    assert not gap_result.sampling_complete
    assert gap_result.to_metadata()["sampling_status"] == "INCOMPLETE"
    assert any("sampling interval" in warning for warning in gap_result.warnings)

    broken_container = FakeContainer(
        [FakeFrame(0)],
        duration_ms=3_000,
        decode_error=FakeFFmpegError("truncated stream"),
    )
    _install_fake_av(monkeypatch, broken_container)
    broken_result = video_parser._extract_video_core("broken.mp4", b"video")
    assert not broken_result.sampling_complete
    assert any("stopped early" in warning for warning in broken_result.warnings)
    assert broken_container.closed


def test_decoder_frame_limit_returns_explicit_incomplete_result(monkeypatch):
    container = FakeContainer(
        [FakeFrame(0), FakeFrame(100), FakeFrame(200)],
        duration_ms=1_000,
    )
    _install_fake_av(monkeypatch, container)
    monkeypatch.setattr(video_parser, "MAX_DECODED_FRAMES", 2)

    result = video_parser._extract_video_core("dense.mp4", b"video")

    assert not result.sampling_complete
    assert result.decoded_frame_count == 2
    assert any("processing limit" in warning for warning in result.warnings)
    assert container.closed


def test_sample_frame_cap_is_enforced(monkeypatch):
    duration_ms = 30_000
    frames = [FakeFrame(index * 1_000) for index in range(30)]
    container = FakeContainer(frames, duration_ms=duration_ms)
    _install_fake_av(monkeypatch, container)

    result = video_parser._extract_video_core("thirty-seconds.mp4", b"video")

    assert len(result.frames) == video_parser.MAX_SAMPLED_FRAMES
    assert [frame.frame_index for frame in result.frames] == list(range(30))
    assert result.sampling_complete
    assert container.closed

    monkeypatch.setattr(video_parser, "MAX_SAMPLED_FRAMES", 2)
    limited_container = FakeContainer(
        [FakeFrame(0), FakeFrame(1_000), FakeFrame(2_000)],
        duration_ms=3_000,
    )
    _install_fake_av(monkeypatch, limited_container)
    limited_result = video_parser._extract_video_core("sample-limit.mp4", b"video")
    assert len(limited_result.frames) == 2
    assert not limited_result.sampling_complete
    assert any("frame limit" in warning for warning in limited_result.warnings)
    assert limited_container.closed


def test_invalid_timestamps_and_decoder_failures_are_reported(monkeypatch):
    no_pts = FakeContainer([FakeFrame(None)], duration_ms=1_000)
    _install_fake_av(monkeypatch, no_pts)
    with pytest.raises(video_parser.VideoDecodeError, match="before a keyframe"):
        video_parser._extract_video_core("no-pts.mp4", b"video")
    assert no_pts.closed

    corrupt_stream = FakeContainer(
        [FakeFrame(0), FakeFrame(500), FakeFrame(250)],
        duration_ms=1_000,
    )
    _install_fake_av(monkeypatch, corrupt_stream)
    result = video_parser._extract_video_core("non-monotonic.mp4", b"video")
    assert not result.sampling_complete
    assert any("stopped early" in warning for warning in result.warnings)
    assert corrupt_stream.closed

    fail_before_frame = FakeContainer([], duration_ms=1_000, decode_error=FakeFFmpegError())
    _install_fake_av(monkeypatch, fail_before_frame)
    with pytest.raises(video_parser.VideoDecodeError, match="before a keyframe"):
        video_parser._extract_video_core("decode-error.mp4", b"video")
    assert fail_before_frame.closed


def test_metadata_is_bounded_json_safe_and_callback_failure_cleans_up(monkeypatch):
    frame = FakeFrame(0)
    container = FakeContainer([frame], duration_ms=1_000)
    _install_fake_av(monkeypatch, container)

    def fail_callback(_):
        raise RuntimeError("consumer failed")

    with pytest.raises(RuntimeError, match="consumer failed"):
        video_parser._extract_video_core(
            "callback.mp4",
            b"private-video-bytes",
            on_keyframe=fail_callback,
        )
    with pytest.raises(ValueError, match="closed"):
        frame.rendered_image.getpixel((0, 0))
    assert container.closed

    complete_container = FakeContainer([FakeFrame(0)], duration_ms=1_000)
    _install_fake_av(monkeypatch, complete_container)
    result = video_parser._extract_video_core("metadata.mp4", b"private-video-bytes")
    metadata = result.to_metadata()
    serialized = json.dumps(metadata)
    assert "private-video-bytes" not in serialized
    assert "image_bytes" not in metadata
    assert metadata["frames"] == [
        {
            "frame_index": 0,
            "timestamp_ms": 0,
            "width_px": 64,
            "height_px": 48,
        }
    ]
    assert complete_container.closed


def test_worker_startup_failure_closes_both_pipe_ends(monkeypatch):
    class StartFailProcess:
        def __init__(self, *, target, args):
            self.closed = False

        def start(self):
            raise OSError("process creation denied")

        def close(self):
            self.closed = True

    class StartFailContext:
        def __init__(self):
            self.real_context = multiprocessing.get_context("spawn")
            self.receiver, self.sender = self.real_context.Pipe(duplex=False)
            self.process = None

        def Pipe(self, *, duplex):
            assert not duplex
            return self.receiver, self.sender

        def Semaphore(self, value):
            return self.real_context.Semaphore(value)

        def Process(self, *, target, args):
            self.process = StartFailProcess(target=target, args=args)
            return self.process

    context = StartFailContext()
    monkeypatch.setattr(
        video_parser.multiprocessing, "get_context", lambda method: context
    )

    with pytest.raises(OSError, match="process creation denied"):
        video_parser._run_supervised_worker(
            _worker_wait_without_metadata,
            (),
            on_keyframe=None,
            timeout_seconds=1,
        )

    assert context.receiver.closed
    assert context.sender.closed
    assert context.process.closed


def test_worker_semaphore_setup_failure_closes_pipe_ends(monkeypatch):
    class SetupFailContext:
        def __init__(self):
            self.receiver, self.sender = multiprocessing.get_context("spawn").Pipe(
                duplex=False
            )

        def Pipe(self, *, duplex):
            assert not duplex
            return self.receiver, self.sender

        def Semaphore(self, value):
            raise OSError("semaphore creation denied")

        def Process(self, **kwargs):
            pytest.fail("A process must not be created without its acknowledgement semaphore.")

    context = SetupFailContext()
    monkeypatch.setattr(
        video_parser.multiprocessing, "get_context", lambda method: context
    )

    with pytest.raises(OSError, match="semaphore creation denied"):
        video_parser._run_supervised_worker(
            _worker_wait_without_metadata,
            (),
            on_keyframe=None,
            timeout_seconds=1,
        )

    assert context.receiver.closed
    assert context.sender.closed


def test_cleanup_terminates_joins_kills_if_needed_and_closes_process():
    class StubbornProcess:
        def __init__(self):
            self.alive = True
            self.events = []

        def is_alive(self):
            self.events.append("is_alive")
            return self.alive

        def terminate(self):
            self.events.append("terminate")

        def join(self, timeout=None):
            self.events.append(("join", timeout))

        def kill(self):
            self.events.append("kill")
            self.alive = False

        def close(self):
            self.events.append("close")

    process = StubbornProcess()
    video_parser._cleanup_worker(process, terminate=True)
    assert process.events == [
        "is_alive",
        "terminate",
        ("join", video_parser.WORKER_CLEANUP_GRACE_SECONDS),
        "is_alive",
        "kill",
        ("join", None),
        "close",
    ]


def test_oversized_input_is_rejected_before_worker_creation(monkeypatch):
    monkeypatch.setattr(
        video_parser.multiprocessing,
        "get_context",
        lambda method: pytest.fail("A worker must not be created for oversized input."),
    )

    with pytest.raises(video_parser.VideoResourceLimitError, match="10 MiB"):
        video_parser.extract_video(
            "oversized.mp4", b"x" * (video_parser.MAX_VIDEO_BYTES + 1)
        )


def test_worker_timeout_without_metadata_raises_and_leaves_no_child():
    before = {child.pid for child in multiprocessing.active_children()}

    with pytest.raises(video_parser.VideoTimeoutError, match="before metadata"):
        video_parser._run_supervised_worker(
            _worker_wait_without_metadata,
            (),
            on_keyframe=None,
            timeout_seconds=0.25,
        )

    assert {child.pid for child in multiprocessing.active_children()} == before


def test_partial_worker_timeout_returns_incomplete_and_cleans_child():
    before = {child.pid for child in multiprocessing.active_children()}
    delivered = []

    result = video_parser._run_supervised_worker(
        _worker_timeout_after_one_frame,
        (),
        on_keyframe=lambda sample: delivered.append(sample.metadata),
        timeout_seconds=0.5,
    )

    assert not result.sampling_complete
    assert result.to_metadata()["sampling_status"] == "INCOMPLETE"
    assert result.frames == tuple(delivered)
    assert result.decoded_frame_count == 1
    assert any("deadline" in warning for warning in result.warnings)
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.parametrize(
    ("worker", "args", "message"),
    [
        (_worker_send_error, (), "RuntimeError"),
        (video_parser._video_worker, ("valid.mp4", 1, False), "TypeError"),
        (_worker_crash, (), "exited"),
    ],
)
def test_worker_exception_and_crash_raise_and_leave_no_child(worker, args, message):
    before = {child.pid for child in multiprocessing.active_children()}

    with pytest.raises(video_parser.VideoWorkerError, match=message):
        video_parser._run_supervised_worker(
            worker,
            args,
            on_keyframe=None,
            timeout_seconds=2,
        )

    assert {child.pid for child in multiprocessing.active_children()} == before


def test_callback_failure_terminates_and_joins_worker():
    before = {child.pid for child in multiprocessing.active_children()}

    def fail_callback(_):
        raise RuntimeError("consumer failed")

    with pytest.raises(RuntimeError, match="consumer failed"):
        video_parser._run_supervised_worker(
            _worker_timeout_after_one_frame,
            (),
            on_keyframe=fail_callback,
            timeout_seconds=5,
        )

    assert {child.pid for child in multiprocessing.active_children()} == before


def test_frame_messages_are_delivered_one_at_a_time_and_closed():
    callback_images = []
    callback_indexes = []

    def callback(sample):
        assert callback_indexes == list(range(sample.metadata.frame_index))
        callback_indexes.append(sample.metadata.frame_index)
        callback_images.append(sample.image)
        assert sample.image.getpixel((0, 0)) == (0, 0, 255)

    result = video_parser._run_supervised_worker(
        _worker_send_two_frames,
        (),
        on_keyframe=callback,
        timeout_seconds=5,
    )

    assert result.sampling_complete
    assert callback_indexes == [0, 1]
    for image in callback_images:
        with pytest.raises(ValueError, match="closed"):
            image.getpixel((0, 0))


def _make_synthetic_mp4():
    import av

    output = BytesIO()
    writer = av.open(output, mode="w", format="mp4")
    stream = writer.add_stream("mpeg4", rate=1)
    stream.width = 64
    stream.height = 48
    stream.pix_fmt = "yuv420p"
    for index in range(3):
        frame = av.VideoFrame(64, 48, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts = index
        frame.time_base = Fraction(1, 1)
        for packet in stream.encode(frame):
            writer.mux(packet)
    for packet in stream.encode():
        writer.mux(packet)
    writer.close()
    return output.getvalue()


def test_public_api_works_with_spawn_context_and_pyav(monkeypatch):
    original_get_context = multiprocessing.get_context
    requested_contexts = []

    def get_context(method=None):
        requested_contexts.append(method)
        return original_get_context(method)

    monkeypatch.setattr(multiprocessing, "get_context", get_context)
    result = video_parser.extract_video("synthetic.mp4", _make_synthetic_mp4())

    assert requested_contexts == ["spawn"]
    assert result.sampling_complete
    assert [frame.timestamp_ms for frame in result.frames] == [0, 1_000, 2_000]
