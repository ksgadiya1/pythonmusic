# 🎵 MP3 Trim & Merge

A small Python + Streamlit app to upload an MP3, cut out one or more time
ranges, reorder/merge those pieces back into a single track, preview it, and
download the result — with the original audio quality preserved.

## How trimming/merging works

- You pick a **from → to** time range (e.g. `0:30` to `1:15`).
- Clicking **Add Trimmed Piece** cuts that exact slice out of the song and
  adds it to an ordered list ("pieces").
- Repeat for as many ranges as you like (e.g. cut the intro and the chorus
  out of a song as two separate pieces).
- Reorder pieces with the ⬆️/⬇️ buttons or remove them with 🗑️.
- **Merge Pieces** stitches all kept pieces back together, in order, into one
  file. An optional crossfade (in ms) can be applied for a smoother join; the
  default is a hard cut with zero quality loss.
- Preview the merged track and hit **Download Final MP3**.

## Quality preservation

- Slicing audio in memory (trimming) is a lossless PCM operation — pydub
  decodes the file once, and cutting raw PCM doesn't re-encode anything.
- The only re-encoding happens at export time. Every export in this project
  automatically detects and matches the **original file's bitrate, sample
  rate, and channel count**, so the exported MP3 quality matches your
  uploaded file.

## Project structure

```
mp3_editor/
├── app.py            # Streamlit UI
├── audio_utils.py     # Core trim/merge/export functions (reusable, UI-independent)
├── requirements.txt
└── README.md
```

## Setup

Good to make venv first.

1. **Install ffmpeg** (required by pydub to decode/encode MP3):
   - macOS: `brew install ffmpeg`
   - Ubuntu/Debian: `sudo apt install ffmpeg`
   - Windows: download from https://ffmpeg.org/download.html and add it to your PATH

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the app:**
   ```bash
   streamlit run app.py
   ```
   This opens the UI in your browser (usually at `http://localhost:8501`).

## Using `audio_utils.py` without the UI

The core functions are UI-independent, so you can script trims directly:

```python
from audio_utils import load_audio, trim_audio, merge_segments, export_audio, get_source_bitrate, ms_from_timestr

src = "song.mp3"
audio = load_audio(src)
bitrate = get_source_bitrate(src)

# Keep 0:00-0:30 and 1:10-1:40, cut out everything else, then merge those two pieces
piece1 = trim_audio(audio, ms_from_timestr("0:00"), ms_from_timestr("0:30"))
piece2 = trim_audio(audio, ms_from_timestr("1:10"), ms_from_timestr("1:40"))

merged = merge_segments([piece1, piece2])
export_audio(merged, "output.mp3", bitrate=bitrate)
```
