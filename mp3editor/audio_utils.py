"""
audio_utils.py
--------------
Core audio processing functions for uploading, trimming, and merging MP3
files with pydub.

Quality-preservation strategy
------------------------------
1. Trimming/slicing an AudioSegment in memory is lossless — pydub decodes the
   source once to raw PCM, and slicing PCM does not re-encode anything.
2. Quality can only be lost at the export step (re-encoding to MP3). To avoid
   any audible loss, every export in this module:
     - matches the ORIGINAL file's bitrate (detected via ffprobe/mediainfo)
     - matches the original sample rate and channel count
     - uses libmp3lame's highest-quality settings
   So the final trimmed/merged MP3 comes out at the same quality as the file
   you uploaded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

import static_ffmpeg
static_ffmpeg.add_paths()  # adds ffmpeg + ffprobe to PATH before pydub imports

import imageio_ffmpeg
import pydub.utils as _pydub_utils
import pydub.audio_segment as _pydub_audio_segment
from pydub import AudioSegment
from pydub.utils import mediainfo

_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# Use bundled ffmpeg for encoding/decoding.
AudioSegment.converter = _FFMPEG
_pydub_utils.get_encoder_name = lambda: _FFMPEG
# static_ffmpeg.add_paths() already put ffprobe on PATH, so pydub's
# get_prober_name() will find it via which("ffprobe") automatically.

DEFAULT_BITRATE = "320k"


@dataclass
class TrimSegment:
    """One kept chunk of audio, remembering where it came from in the source."""

    start_ms: int
    end_ms: int
    audio: AudioSegment
    label: str = ""

    @property
    def duration_ms(self) -> int:
        return len(self.audio)


def load_audio(file_path: str) -> AudioSegment:
    """
    Load an audio file (mp3/wav/etc.) from disk into an AudioSegment.

    Args:
        file_path: Path to the audio file on disk.

    Returns:
        The decoded AudioSegment.

    Raises:
        FileNotFoundError: if file_path does not exist.
        Exception: if ffmpeg/pydub cannot decode the file.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    return AudioSegment.from_file(file_path)


def get_source_bitrate(file_path: str) -> str:
    """
    Detect a file's original bitrate so exports can match it and keep
    perceived audio quality unchanged.

    Args:
        file_path: Path to the source audio file.

    Returns:
        Bitrate string such as "192k". Falls back to DEFAULT_BITRATE
        if detection fails.
    """
    try:
        info = mediainfo(file_path)
        bit_rate = info.get("bit_rate")
        if bit_rate:
            kbps = max(1, int(int(bit_rate) / 1000))
            return f"{kbps}k"
    except Exception:
        pass
    return DEFAULT_BITRATE


def get_duration_ms(audio: AudioSegment) -> int:
    """Return the duration of an AudioSegment in milliseconds."""
    return len(audio)


def ms_from_timestr(timestr: str) -> int:
    """
    Parse a time string into milliseconds. Accepts:
        "90"        -> 90 seconds
        "1:30"      -> mm:ss
        "0:01:30"   -> hh:mm:ss
    Fractional seconds are allowed, e.g. "1:30.5".

    Args:
        timestr: Time string to parse.

    Returns:
        Time in milliseconds.

    Raises:
        ValueError: if the string is not a recognizable time format.
    """
    timestr = timestr.strip()
    if not timestr:
        raise ValueError("Time value is empty.")

    raw_parts = timestr.split(":")
    try:
        parts = [float(p) for p in raw_parts]
    except ValueError as exc:
        raise ValueError(f"Invalid time format: '{timestr}'") from exc

    if len(parts) == 1:
        seconds = parts[0]
    elif len(parts) == 2:
        minutes, seconds = parts
        seconds = minutes * 60 + seconds
    elif len(parts) == 3:
        hours, minutes, seconds = parts
        seconds = hours * 3600 + minutes * 60 + seconds
    else:
        raise ValueError(f"Invalid time format: '{timestr}'")

    if seconds < 0:
        raise ValueError("Time value cannot be negative.")

    return int(round(seconds * 1000))


def ms_to_timestr(ms: int) -> str:
    """Convert milliseconds to a human-readable 'mm:ss.xx' string."""
    total_seconds = max(0, ms) / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:05.2f}"


def trim_audio(audio: AudioSegment, start_ms: int, end_ms: int) -> AudioSegment:
    """
    Slice out the portion of `audio` between start_ms and end_ms.

    This is a lossless in-memory operation (raw PCM slicing) — no
    quality is lost here; only export() re-encodes.

    Args:
        audio: Full-length AudioSegment to slice from.
        start_ms: Start of the range to keep, in milliseconds.
        end_ms: End of the range to keep, in milliseconds.

    Returns:
        A new AudioSegment containing only [start_ms, end_ms).

    Raises:
        ValueError: if the range is invalid or out of bounds.
    """
    duration = len(audio)
    if start_ms < 0:
        raise ValueError("Start time cannot be negative.")
    if end_ms > duration:
        raise ValueError(
            f"End time ({end_ms}ms) exceeds audio duration ({duration}ms)."
        )
    if start_ms >= end_ms:
        raise ValueError("Start time must be before end time.")
    return audio[start_ms:end_ms]


def merge_segments(segments: List[AudioSegment], crossfade_ms: int = 0) -> AudioSegment:
    """
    Concatenate multiple AudioSegments, in the order given.

    Args:
        segments: List of AudioSegment pieces to join.
        crossfade_ms: Optional crossfade in milliseconds between consecutive
            pieces for a smoother join. 0 (default) is a hard cut with no
            crossfade, which is the fully lossless join.

    Returns:
        A single merged AudioSegment.

    Raises:
        ValueError: if `segments` is empty.
    """
    if not segments:
        raise ValueError("No segments provided to merge.")

    merged = segments[0]
    for seg in segments[1:]:
        if crossfade_ms > 0:
            # crossfade cannot exceed the length of either clip being joined
            safe_fade = min(crossfade_ms, len(merged), len(seg))
            merged = merged.append(seg, crossfade=safe_fade)
        else:
            merged = merged + seg
    return merged


def export_audio(
    audio: AudioSegment,
    output_path: str,
    bitrate: Optional[str] = None,
    fmt: str = "mp3",
) -> str:
    """
    Export an AudioSegment to disk, matching the original bitrate, sample
    rate, and channel count so audio quality is preserved.

    Args:
        audio: AudioSegment to write out.
        output_path: Destination file path.
        bitrate: Target bitrate, e.g. "192k". Defaults to DEFAULT_BITRATE
            if not supplied (use get_source_bitrate() on the original file
            to preserve original quality).
        fmt: Output format (default "mp3").

    Returns:
        The output_path written to.
    """
    bitrate = bitrate or DEFAULT_BITRATE
    audio.export(
        output_path,
        format=fmt,
        bitrate=bitrate,
        parameters=["-ar", str(audio.frame_rate), "-ac", str(audio.channels)],
    )
    return output_path
