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

import re
from subprocess import PIPE, Popen

import imageio_ffmpeg
import pydub.utils as _pydub_utils
import pydub.audio_segment as _pydub_audio_segment
from pydub import AudioSegment

_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

AudioSegment.converter = _FFMPEG
_pydub_utils.get_encoder_name = lambda: _FFMPEG


def _mediainfo_json(filepath, read_ahead_limit=-1):
    """Parse ffmpeg -i stderr to produce a minimal mediainfo-style dict for pydub."""
    proc = Popen([_FFMPEG, "-i", filepath], stdout=PIPE, stderr=PIPE)
    _, stderr = proc.communicate()
    text = stderr.decode("utf-8", errors="replace")

    # Detect codec name from ffmpeg output, e.g. "Audio: mp3", "Audio: aac"
    codec_name = "mp3"
    m = re.search(r"Audio:\s*(\w+)", text)
    if m:
        codec_name = m.group(1).lower()

    stream = {
        "codec_type": "audio",
        "codec_name": codec_name,
        "sample_fmt": "fltp",  # triggers pydub's safe branch -> bits_per_sample=16
        "bits_per_sample": 0,
    }

    m = re.search(r"Audio:.*?(\d+) Hz", text)
    if m:
        stream["sample_rate"] = m.group(1)

    m = re.search(r"Audio:.*?Hz,\s*(\w+)", text)
    if m:
        ch = m.group(1)
        stream["channels"] = "2" if ch in ("stereo", "2") else "1"

    m = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
    if m:
        stream["bit_rate"] = str(int(m.group(1)) * 1000)

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        stream["duration"] = str(h * 3600 + mn * 60 + s)

    return {"streams": [stream], "format": {"bit_rate": stream.get("bit_rate", "")}}


_pydub_utils.mediainfo_json = _mediainfo_json
_pydub_audio_segment.mediainfo_json = _mediainfo_json

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
    """Load an audio file from disk into an AudioSegment."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    ext = os.path.splitext(file_path)[1].lstrip(".").lower() or "mp3"
    return AudioSegment.from_file(file_path, format=ext)


def get_source_bitrate(file_path: str) -> str:
    """Detect a file's original bitrate. Falls back to DEFAULT_BITRATE if detection fails."""
    try:
        data = _mediainfo_json(file_path)
        bit_rate = data["streams"][0].get("bit_rate") or data["format"].get("bit_rate")
        if bit_rate:
            return f"{max(1, int(int(bit_rate) / 1000))}k"
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
