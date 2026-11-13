"""Bounded MP4/MOV keyframe sampling without OCR or compliance analysis."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
import math
import multiprocessing
import time
from pathlib import Path
from multiprocessing.connection import Connection, wait
from typing import Any, Callable, Iterable

from PIL import Image

MAX_VIDEO_BYTES = 10 * 1024 * 1024
MAX_VIDEO_DURATION_MS = 30_000
MAX_VIDEO_WIDTH_PX = 1_920
MAX_VIDEO_HEIGHT_PX = 1_920
MAX_VIDEO_FRAME_PIXELS = 4_000_000
MAX_SAMPLED_FRAMES = 30
SAMPLE_INTERVAL_MS = 1_000
MAX_DECODED_FRAMES = 1_800
WORKER_TIMEOUT_SECONDS = 15.0
WORKER_CLEANUP_GRACE_SECONDS = 1.0
_SUPPORTED_EXTENSIONS = {".mp4", ".mov"}
_SUPPORTED_CONTAINER_NAMES = {"mov", "mp4"}


class VideoParseError(ValueError):
    """Base error for video validation and extraction failures."""


class UnsupportedVideoError(VideoParseError):
    """Raised when the file extension, container, or stream is unsupported."""


class InvalidVideoError(VideoParseError):
    """Raised when the input is corrupt or required metadata is invalid."""


class VideoResourceLimitError(VideoParseError):
    """Raised when the input exceeds a hard video resource limit."""


class VideoDecodeError(VideoParseError):
    """Raised when decoding fails before a useful partial result is available."""


class VideoDecoderUnavailable(VideoParseError):
    """Raised when the declared PyAV dependency cannot be imported."""


class VideoTimeoutError(VideoParseError):
    """Raised when the worker deadline expires before valid metadata is available."""


class VideoWorkerError(VideoParseError):
    """Raised when a supervised video worker fails unexpectedly."""


@dataclass(frozen=True)
class KeyframeMetadata:
    frame_index: int
    timestamp_ms: int
    width_px: int
    height_px: int

    def to_dict(self) -> dict[str, int]:
        return {
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "width_px": self.width_px,
            "height_px": self.height_px,
        }


@dataclass(frozen=True)
class EphemeralKeyframe:
    """A sampled image valid only during the synchronous frame callback."""

    metadata: KeyframeMetadata
    image: Image.Image


@dataclass(frozen=True)
class VideoExtractionResult:
    duration_ms: int
    frames: tuple[KeyframeMetadata, ...]
    decoded_frame_count: int
    sampling_complete: bool
    warnings: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        """Return bounded JSON-safe metadata; image/video bytes are never included."""
        return {
            "schema_version": 1,
            "source_type": "video",
            "duration_ms": self.duration_ms,
            "sample_interval_ms": SAMPLE_INTERVAL_MS,
            "sampling_status": "COMPLETE" if self.sampling_complete else "INCOMPLETE",
            "sampling_complete": self.sampling_complete,
            "decoded_frame_count": self.decoded_frame_count,
            "warnings": list(self.warnings),
            "frames": [frame.to_dict() for frame in self.frames],
        }


def _fraction(value: Any) -> Fraction | None:
    if value is None:
        return None
    try:
        return Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _duration_seconds(container: Any, stream: Any, av: Any) -> Fraction:
    durations: list[Fraction] = []
    container_duration = getattr(container, "duration", None)
    if container_duration is not None and container_duration > 0:
        durations.append(Fraction(container_duration, av.time_base))

    stream_duration = getattr(stream, "duration", None)
    stream_time_base = _fraction(getattr(stream, "time_base", None))
    if stream_duration is not None and stream_duration > 0 and stream_time_base:
        durations.append(Fraction(stream_duration) * stream_time_base)

    if not durations:
        raise InvalidVideoError("Video duration could not be determined.")
    return max(durations)


def _validate_dimensions(width: Any, height: Any) -> tuple[int, int]:
    try:
        width_px, height_px = int(width), int(height)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidVideoError("Video dimensions are missing or invalid.") from exc

    if width_px <= 0 or height_px <= 0:
        raise InvalidVideoError("Video dimensions are missing or invalid.")
    if width_px > MAX_VIDEO_WIDTH_PX or height_px > MAX_VIDEO_HEIGHT_PX:
        raise VideoResourceLimitError(
            f"Video dimensions {width_px} x {height_px} exceed the "
            f"{MAX_VIDEO_WIDTH_PX} x {MAX_VIDEO_HEIGHT_PX} limit."
        )
    if width_px * height_px > MAX_VIDEO_FRAME_PIXELS:
        raise VideoResourceLimitError(
            f"Video frame has {width_px * height_px:,} pixels; "
            f"the limit is {MAX_VIDEO_FRAME_PIXELS:,}."
        )
    return width_px, height_px


def _frame_time(frame: Any, stream: Any) -> Fraction:
    pts = getattr(frame, "pts", None)
    time_base = _fraction(
        getattr(frame, "time_base", None) or getattr(stream, "time_base", None)
    )
    if pts is None or time_base is None or time_base <= 0:
        raise InvalidVideoError("Video frame is missing a valid presentation timestamp.")
    return Fraction(pts) * time_base


def extract_video(
    filename: str,
    data: bytes,
    *,
    on_keyframe: Callable[[EphemeralKeyframe], None] | None = None,
) -> VideoExtractionResult:
    """Validate a video and sample the first decoded frame in each one-second interval.

    Sample images are delivered synchronously to ``on_keyframe`` and closed as soon
    as it returns. Only their bounded metadata is retained in the result. The
    15-second supervisor deadline covers worker startup, validation, and decoding;
    it cannot preempt a synchronous ``on_keyframe`` callback running in the parent
    process, so callback work is not covered by a hard end-to-end deadline.
    """
    _validate_request(filename, data)
    return _run_supervised_worker(
        _video_worker,
        (filename, data, on_keyframe is not None),
        on_keyframe=on_keyframe,
        timeout_seconds=WORKER_TIMEOUT_SECONDS,
    )


def _validate_request(filename: str, data: bytes) -> None:
    if not isinstance(data, bytes) or not data:
        raise InvalidVideoError("Video upload is empty or is not bytes.")
    if len(data) > MAX_VIDEO_BYTES:
        raise VideoResourceLimitError(
            f"Video upload exceeds the {MAX_VIDEO_BYTES // (1024 * 1024)} MiB limit."
        )

    extension = Path(filename).suffix.lower()
    if extension not in _SUPPORTED_EXTENSIONS:
        raise UnsupportedVideoError(
            f"Video type '{extension}' is not supported. Accepted: .mp4, .mov."
        )


def _extract_video_core(
    filename: str,
    data: bytes,
    *,
    on_keyframe: Callable[[EphemeralKeyframe], None] | None = None,
    _on_validated: Callable[[int, int], None] | None = None,
    _on_sample: Callable[[KeyframeMetadata, Image.Image | None, int], None] | None = None,
    _include_frame_images: bool = True,
) -> VideoExtractionResult:
    try:
        import av
    except ImportError as exc:
        raise VideoDecoderUnavailable("PyAV is required for video extraction.") from exc

    decode_errors = (av.error.FFmpegError, OSError, EOFError)
    source = BytesIO(data)
    container = None
    try:
        try:
            container = av.open(source, mode="r")
        except decode_errors as exc:
            raise InvalidVideoError(
                f"Video container could not be opened ({type(exc).__name__})."
            ) from exc

        format_name = getattr(getattr(container, "format", None), "name", "")
        aliases = {name.strip().lower() for name in str(format_name).split(",")}
        if not aliases.intersection(_SUPPORTED_CONTAINER_NAMES):
            raise UnsupportedVideoError("Video container is not MP4 or MOV.")

        streams = getattr(container, "streams", None)
        video_streams = getattr(streams, "video", ()) if streams is not None else ()
        stream = next(iter(video_streams), None)
        if stream is None:
            raise UnsupportedVideoError("Video file does not contain a video stream.")

        width_px, height_px = _validate_dimensions(
            getattr(stream, "width", None), getattr(stream, "height", None)
        )
        codec_context = getattr(stream, "codec_context", None)
        if codec_context is not None:
            codec_context.thread_count = 1
        duration_seconds = _duration_seconds(container, stream, av)
        if duration_seconds > Fraction(MAX_VIDEO_DURATION_MS, 1_000):
            raise VideoResourceLimitError(
                f"Video duration exceeds the {MAX_VIDEO_DURATION_MS // 1_000}-second limit."
            )
        duration_ms = math.ceil(duration_seconds * 1_000)
        expected_samples = min(
            MAX_SAMPLED_FRAMES,
            math.ceil(duration_seconds * 1_000 / SAMPLE_INTERVAL_MS),
        )
        if _on_validated is not None:
            _on_validated(duration_ms, expected_samples)

        samples: list[KeyframeMetadata] = []
        warnings: list[str] = []
        decoded_frame_count = 0
        next_bucket = 0
        origin_time: Fraction | None = None
        previous_time: Fraction | None = None
        decode_incomplete = False

        try:
            decoded_frames = iter(container.decode(video=stream.index))
            while decoded_frame_count < MAX_DECODED_FRAMES:
                try:
                    frame = next(decoded_frames)
                except StopIteration:
                    break
                decoded_frame_count += 1

                try:
                    frame_time = _frame_time(frame, stream)
                    if origin_time is None:
                        origin_time = frame_time
                    if previous_time is not None and frame_time < previous_time:
                        raise InvalidVideoError(
                            "Video presentation timestamps are not monotonic."
                        )
                    previous_time = frame_time
                    elapsed = frame_time - origin_time
                    elapsed_ms = elapsed * 1_000
                    if elapsed > Fraction(MAX_VIDEO_DURATION_MS, 1_000):
                        raise VideoResourceLimitError(
                            "Decoded video timestamps exceed the 30-second limit."
                        )
                    if elapsed >= duration_seconds:
                        warnings.append(
                            "Decoded frames extend beyond the declared video duration."
                        )
                        decode_incomplete = True
                        break

                    frame_width, frame_height = _validate_dimensions(
                        getattr(frame, "width", width_px),
                        getattr(frame, "height", height_px),
                    )
                    bucket = int(elapsed_ms // SAMPLE_INTERVAL_MS)
                    if bucket < next_bucket:
                        continue
                    if bucket > next_bucket:
                        warnings.append(
                            f"No decoded frame was available for "
                            f"{bucket - next_bucket} sampling interval(s)."
                        )
                        decode_incomplete = True
                    if len(samples) >= MAX_SAMPLED_FRAMES:
                        warnings.append(
                            f"Sampling stopped at the {MAX_SAMPLED_FRAMES}-frame limit."
                        )
                        decode_incomplete = True
                        break

                    metadata = KeyframeMetadata(
                        frame_index=len(samples),
                        timestamp_ms=round(elapsed_ms),
                        width_px=frame_width,
                        height_px=frame_height,
                    )
                    if on_keyframe is not None or (
                        _on_sample is not None and _include_frame_images
                    ):
                        image = frame.to_image()
                        try:
                            ephemeral = EphemeralKeyframe(metadata, image)
                            if on_keyframe is not None:
                                on_keyframe(ephemeral)
                            if _on_sample is not None:
                                _on_sample(metadata, image, decoded_frame_count)
                        finally:
                            image.close()
                    elif _on_sample is not None:
                        _on_sample(metadata, None, decoded_frame_count)
                    samples.append(metadata)
                    next_bucket = bucket + 1
                except VideoResourceLimitError:
                    raise
                except InvalidVideoError as exc:
                    if not samples:
                        raise VideoDecodeError(
                            f"Video decoding failed before a keyframe was extracted "
                            f"({type(exc).__name__})."
                        ) from exc
                    warnings.append(
                        f"Video decoding stopped early ({type(exc).__name__}); "
                        "sampling is incomplete."
                    )
                    decode_incomplete = True
                    break
                except decode_errors as exc:
                    if not samples:
                        raise VideoDecodeError(
                            f"Video decoding failed before a keyframe was extracted "
                            f"({type(exc).__name__})."
                        ) from exc
                    warnings.append(
                        f"Video decoding stopped early ({type(exc).__name__}); "
                        "sampling is incomplete."
                    )
                    decode_incomplete = True
                    break

            if not decode_incomplete and decoded_frame_count == MAX_DECODED_FRAMES:
                warnings.append(
                    f"Video decoding reached the {MAX_DECODED_FRAMES}-frame "
                    "processing limit before EOF could be verified; sampling is incomplete."
                )
                decode_incomplete = True
        except (av.error.FFmpegError, OSError, EOFError) as exc:
            if not samples:
                raise VideoDecodeError(
                    f"Video decoding failed before a keyframe was extracted "
                    f"({type(exc).__name__})."
                ) from exc
            warnings.append(
                f"Video decoding stopped early ({type(exc).__name__}); "
                "sampling is incomplete."
            )
            decode_incomplete = True

        if origin_time is None:
            raise VideoDecodeError("Video stream contains no decodable frames.")
        if next_bucket < expected_samples:
            warnings.append(
                f"No decoded frame was available for "
                f"{expected_samples - next_bucket} final sampling interval(s)."
            )
            decode_incomplete = True

        return VideoExtractionResult(
            duration_ms=duration_ms,
            frames=tuple(samples),
            decoded_frame_count=decoded_frame_count,
            sampling_complete=not decode_incomplete,
            warnings=tuple(warnings),
        )
    finally:
        try:
            if container is not None:
                container.close()
        finally:
            source.close()


def _video_worker(
    filename: str,
    data: bytes,
    include_frame_images: bool,
    sender: Connection,
    acknowledgment: Any,
) -> None:
    """Decode in a child process and send one ephemeral frame per pipe message."""

    def send_header(duration_ms: int, expected_samples: int) -> None:
        sender.send(("header", duration_ms, expected_samples))

    def send_sample(
        metadata: KeyframeMetadata,
        image: Image.Image | None,
        decoded_frame_count: int,
    ) -> None:
        image_bytes = None
        if image is not None:
            image_buffer = BytesIO()
            image.save(image_buffer, format="PNG")
            image_bytes = image_buffer.getvalue()
        sender.send(
            (
                "frame",
                metadata.to_dict(),
                decoded_frame_count,
                image_bytes,
            )
        )
        acknowledgment.acquire()

    try:
        result = _extract_video_core(
            filename,
            data,
            _on_validated=send_header,
            _on_sample=send_sample,
            _include_frame_images=include_frame_images,
        )
        sender.send(
            (
                "result",
                result.duration_ms,
                result.decoded_frame_count,
                result.sampling_complete,
                list(result.warnings),
            )
        )
    except VideoParseError as exc:
        sender.send(("error", type(exc).__name__, str(exc)))
    except Exception as exc:
        sender.send(("error", "VideoWorkerError", type(exc).__name__))
    finally:
        sender.close()


def _cleanup_worker(process: Any, *, terminate: bool) -> None:
    if process is None:
        return
    try:
        if terminate and process.is_alive():
            process.terminate()
        process.join(WORKER_CLEANUP_GRACE_SECONDS)
        if process.is_alive():
            process.kill()
            process.join()
    finally:
        process.close()


def _partial_result(
    duration_ms: int,
    frames: list[KeyframeMetadata],
    decoded_frame_count: int,
    warnings: Iterable[str],
) -> VideoExtractionResult:
    return VideoExtractionResult(
        duration_ms=duration_ms,
        frames=tuple(frames),
        decoded_frame_count=decoded_frame_count,
        sampling_complete=False,
        warnings=tuple(warnings),
    )


def _worker_error(error_type: str, message: str) -> VideoParseError:
    error_types: dict[str, type[VideoParseError]] = {
        "InvalidVideoError": InvalidVideoError,
        "UnsupportedVideoError": UnsupportedVideoError,
        "VideoResourceLimitError": VideoResourceLimitError,
        "VideoDecodeError": VideoDecodeError,
        "VideoDecoderUnavailable": VideoDecoderUnavailable,
        "VideoTimeoutError": VideoTimeoutError,
        "VideoWorkerError": VideoWorkerError,
    }
    exception_type = error_types.get(error_type, VideoWorkerError)
    return exception_type(message)


def _run_supervised_worker(
    target: Callable[..., None],
    args: tuple[Any, ...],
    *,
    on_keyframe: Callable[[EphemeralKeyframe], None] | None,
    timeout_seconds: float,
) -> VideoExtractionResult:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    acknowledgment = None
    process = None
    started = False
    keep_worker = False
    timed_out = False
    deadline = time.monotonic() + timeout_seconds
    frames: list[KeyframeMetadata] = []
    duration_ms: int | None = None
    decoded_frame_count = 0
    result: VideoExtractionResult | None = None
    failure: BaseException | None = None

    try:
        acknowledgment = context.Semaphore(0)
        process = context.Process(
            target=target,
            args=(*args, sender, acknowledgment),
        )
        process.start()
        started = True
        sender.close()

        while result is None and failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                if duration_ms is None:
                    failure = VideoTimeoutError(
                        f"Video decoding exceeded the {timeout_seconds:g}-second deadline "
                        "before metadata was validated."
                    )
                else:
                    result = _partial_result(
                        duration_ms,
                        frames,
                        decoded_frame_count,
                        (f"Video decoding exceeded the {timeout_seconds:g}-second deadline.",),
                    )
                break

            ready = wait((receiver, process.sentinel), timeout=remaining)
            if receiver in ready or receiver.poll():
                try:
                    message = receiver.recv()
                except (EOFError, OSError):
                    if duration_ms is not None:
                        result = _partial_result(
                            duration_ms,
                            frames,
                            decoded_frame_count,
                            ("Video worker exited before completing extraction.",),
                        )
                    else:
                        failure = VideoWorkerError(
                            "Video worker exited without returning extraction metadata."
                        )
                    break

                kind = message[0]
                if kind == "header":
                    _, duration_ms, _expected_samples = message
                elif kind == "frame":
                    _, frame_data, decoded_frame_count, image_bytes = message
                    metadata = KeyframeMetadata(**frame_data)
                    frames.append(metadata)
                    if image_bytes is not None:
                        if on_keyframe is None:
                            raise VideoWorkerError(
                                "Video worker sent a frame image without a consumer."
                            )
                        with Image.open(BytesIO(image_bytes)) as decoded_image:
                            decoded_image.load()
                            ephemeral_image = decoded_image.copy()
                        try:
                            on_keyframe(EphemeralKeyframe(metadata, ephemeral_image))
                        finally:
                            ephemeral_image.close()
                    elif on_keyframe is not None:
                        raise VideoWorkerError(
                            "Video worker omitted a requested frame image."
                        )
                    try:
                        acknowledgment.release()
                    except (OSError, ValueError):
                        raise VideoWorkerError(
                            "Video worker frame acknowledgement failed."
                        )
                elif kind == "result":
                    _, final_duration, decoded_frame_count, complete, warnings = message
                    result = VideoExtractionResult(
                        duration_ms=final_duration,
                        frames=tuple(frames),
                        decoded_frame_count=decoded_frame_count,
                        sampling_complete=complete,
                        warnings=tuple(warnings),
                    )
                elif kind == "error":
                    _, error_type, error_message = message
                    failure = _worker_error(error_type, error_message)
                else:
                    failure = VideoWorkerError("Video worker sent an invalid response.")
            elif process.sentinel in ready:
                if receiver.poll():
                    continue
                if duration_ms is not None:
                    result = _partial_result(
                        duration_ms,
                        frames,
                        decoded_frame_count,
                        ("Video worker exited before completing extraction.",),
                    )
                else:
                    failure = VideoWorkerError(
                        f"Video worker exited with code {process.exitcode} before "
                        "returning extraction metadata."
                    )

        if failure is not None:
            raise failure
        if result is None:
            raise VideoWorkerError("Video worker returned no extraction result.")
        keep_worker = True
        return result
    finally:
        receiver.close()
        sender.close()
        if process is not None and not started and getattr(process, "pid", None) is not None:
            started = True
        if started:
            _cleanup_worker(process, terminate=not keep_worker or timed_out)
        elif process is not None:
            process.close()
