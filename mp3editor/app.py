"""
app.py
------
Small Streamlit UI for the MP3 trim & merge tool.

Workflow:
1. Upload an MP3.
2. Enter a "from - to" time range and click "Add Trimmed Piece" — that
   slice is cut out and added to a list of kept pieces (original quality
   preserved, see audio_utils.py).
3. Repeat step 2 for as many pieces as you want (e.g. cut 0:00-0:30 and
   1:10-1:40 out of a song).
4. Reorder / remove pieces as needed.
5. Click "Merge Pieces" to stitch them back into one track.
6. Preview and download the final MP3.

Run with:
    streamlit run app.py
"""

import os
import tempfile

import streamlit as st

from audio_utils import (
    export_audio,
    get_source_bitrate,
    load_audio,
    merge_segments,
    ms_from_timestr,
    ms_to_timestr,
    trim_audio,
)

st.set_page_config(page_title="MP3 Trim & Merge", page_icon="🎵", layout="centered")

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "source_path" not in st.session_state:
    st.session_state.source_path = None
if "source_audio" not in st.session_state:
    st.session_state.source_audio = None
if "source_bitrate" not in st.session_state:
    st.session_state.source_bitrate = None
if "source_name" not in st.session_state:
    st.session_state.source_name = None
if "pieces" not in st.session_state:
    # each item: {"start_ms": int, "end_ms": int, "path": str}
    st.session_state.pieces = []
if "merged_path" not in st.session_state:
    st.session_state.merged_path = None

WORKDIR = tempfile.mkdtemp(prefix="mp3_editor_")


def save_piece_to_disk(audio, index: int) -> str:
    """Export a trimmed piece to a temp file and return its path."""
    path = os.path.join(WORKDIR, f"piece_{index}.mp3")
    export_audio(audio, path, bitrate=st.session_state.source_bitrate)
    return path


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🎵 MP3 Trim & Merge")
st.caption(
    "Upload a song, cut out the parts you want by time range, "
    "reorder them, then merge and download — original audio quality preserved."
)

# ---------------------------------------------------------------------------
# 1. Upload
# ---------------------------------------------------------------------------
st.header("1. Upload your MP3")
uploaded_file = st.file_uploader("Choose an MP3 file", type=["mp3", "wav", "m4a"])

if uploaded_file is not None and uploaded_file.name != st.session_state.source_name:
    # New file uploaded -> reset everything
    source_path = os.path.join(WORKDIR, uploaded_file.name)
    with open(source_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    with st.spinner("Loading audio..."):
        audio = load_audio(source_path)
        bitrate = get_source_bitrate(source_path)

    st.session_state.source_path = source_path
    st.session_state.source_audio = audio
    st.session_state.source_bitrate = bitrate
    st.session_state.source_name = uploaded_file.name
    st.session_state.pieces = []
    st.session_state.merged_path = None

if st.session_state.source_audio is not None:
    audio = st.session_state.source_audio
    duration_ms = len(audio)

    st.success(f"Loaded **{st.session_state.source_name}**")
    col1, col2, col3 = st.columns(3)
    col1.metric("Duration", ms_to_timestr(duration_ms))
    col2.metric("Bitrate", st.session_state.source_bitrate)
    col3.metric("Channels", audio.channels)

    st.audio(st.session_state.source_path)

    # -----------------------------------------------------------------
    # 2. Trim
    # -----------------------------------------------------------------
    st.header("2. Cut a piece by time range")
    st.caption("Format: seconds, mm:ss, or hh:mm:ss (e.g. 30, 1:15, 0:01:15)")

    trim_col1, trim_col2, trim_col3 = st.columns([1, 1, 1])
    start_str = trim_col1.text_input("From", value="0:00", key="start_input")
    end_str = trim_col2.text_input("To", value=ms_to_timestr(min(duration_ms, 30000)), key="end_input")

    with trim_col3:
        st.write("")
        st.write("")
        add_clicked = st.button("✂️ Add Trimmed Piece", use_container_width=True)

    if add_clicked:
        try:
            start_ms = ms_from_timestr(start_str)
            end_ms = ms_from_timestr(end_str)
            trimmed = trim_audio(audio, start_ms, end_ms)
            piece_path = save_piece_to_disk(trimmed, len(st.session_state.pieces))
            st.session_state.pieces.append(
                {"start_ms": start_ms, "end_ms": end_ms, "path": piece_path}
            )
            st.session_state.merged_path = None
            st.success(
                f"Added piece {ms_to_timestr(start_ms)} → {ms_to_timestr(end_ms)}"
            )
        except ValueError as e:
            st.error(str(e))

    # -----------------------------------------------------------------
    # 3. Kept pieces list (reorder / remove)
    # -----------------------------------------------------------------
    if st.session_state.pieces:
        st.header("3. Pieces to merge (in order)")
        for i, piece in enumerate(st.session_state.pieces):
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 2, 1, 1, 1])
                c1.write(
                    f"**Piece {i + 1}:** "
                    f"{ms_to_timestr(piece['start_ms'])} → {ms_to_timestr(piece['end_ms'])}"
                )
                c2.audio(piece["path"])
                if c3.button("⬆️", key=f"up_{i}", disabled=(i == 0)):
                    st.session_state.pieces[i - 1], st.session_state.pieces[i] = (
                        st.session_state.pieces[i],
                        st.session_state.pieces[i - 1],
                    )
                    st.session_state.merged_path = None
                    st.rerun()
                if c4.button(
                    "⬇️", key=f"down_{i}",
                    disabled=(i == len(st.session_state.pieces) - 1),
                ):
                    st.session_state.pieces[i + 1], st.session_state.pieces[i] = (
                        st.session_state.pieces[i],
                        st.session_state.pieces[i + 1],
                    )
                    st.session_state.merged_path = None
                    st.rerun()
                if c5.button("🗑️", key=f"del_{i}"):
                    st.session_state.pieces.pop(i)
                    st.session_state.merged_path = None
                    st.rerun()

        # ---------------------------------------------------------
        # 4. Merge
        # ---------------------------------------------------------
        st.header("4. Merge & Download")
        crossfade = st.slider(
            "Crossfade between pieces (ms) — 0 = clean hard cut",
            min_value=0,
            max_value=2000,
            value=0,
            step=50,
        )

        if st.button("🔗 Merge Pieces", type="primary"):
            with st.spinner("Merging..."):
                piece_audios = [load_audio(p["path"]) for p in st.session_state.pieces]
                merged = merge_segments(piece_audios, crossfade_ms=crossfade)
                out_name = f"merged_{st.session_state.source_name}"
                out_path = os.path.join(WORKDIR, out_name)
                export_audio(merged, out_path, bitrate=st.session_state.source_bitrate)
                st.session_state.merged_path = out_path
            st.success("Merged!")

        if st.session_state.merged_path:
            st.subheader("Result")
            st.audio(st.session_state.merged_path)
            with open(st.session_state.merged_path, "rb") as f:
                st.download_button(
                    "⬇️ Download Final MP3",
                    data=f.read(),
                    file_name=os.path.basename(st.session_state.merged_path),
                    mime="audio/mpeg",
                    type="primary",
                )
    else:
        st.info("Add at least one trimmed piece above to enable merging.")
else:
    st.info("Upload an MP3 file to get started.")
