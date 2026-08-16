import io
import os
import tempfile
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from pydub import AudioSegment
from mutagen import File as MutagenFile
from supabase import create_client

load_dotenv()

st.set_page_config(
    page_title="ConsultBae Audio Submission",
    page_icon="🎙️",
    layout="centered",
)

DATABASE_URL = os.getenv("DATABASE_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("SUPABASE_URL and SUPABASE_KEY are not configured.")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def normalize_phone(phone):
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else None


def find_person(phone):
    normalized_phone = normalize_phone(phone)

    if not normalized_phone:
        return None

    result = (
        supabase.table("people")
        .select("person_id, full_name, phone")
        .eq("phone", normalized_phone)
        .execute()
    )

    if len(result.data) == 1:
        return result.data[0]

    return None


def get_audio_metadata(uploaded_file):
    audio_bytes = uploaded_file.getvalue()

    with tempfile.NamedTemporaryFile(
        suffix=os.path.splitext(uploaded_file.name)[1],
        delete=False,
    ) as temp_file:
        temp_file.write(audio_bytes)
        temp_path = temp_file.name

    try:
        audio = AudioSegment.from_file(temp_path)

        duration_sec = len(audio) / 1000
        sample_rate_hz = audio.frame_rate

        # ---------------------------------------------------------
        # Extract bitrate using Mutagen.
        # This avoids the FFprobe/GLIBC issue in Replit.
        # ---------------------------------------------------------
        bitrate_kbps = None

        try:
            media_info = MutagenFile(temp_path)

            if media_info and getattr(media_info, "info", None):
                bitrate = getattr(media_info.info, "bitrate", None)

                if bitrate:
                    bitrate_kbps = round(bitrate / 1000, 2)

        except Exception:
            bitrate_kbps = None

        # ---------------------------------------------------------
        # For uncompressed PCM audio such as WAV, calculate the
        # bitrate from sample rate, sample width and channels.
        # ---------------------------------------------------------
        if bitrate_kbps is None:
            try:
                if audio.sample_width and audio.channels:
                    bitrate_kbps = round(
                        (audio.frame_rate * audio.sample_width * audio.channels * 8)
                        / 1000,
                        2,
                    )
            except Exception:
                bitrate_kbps = None

        # dBFS is used as a simple loudness indicator.
        loudness_db = audio.dBFS

        return {
            "duration_sec": round(duration_sec, 2),
            "sample_rate_hz": sample_rate_hz,
            "bitrate_kbps": bitrate_kbps,
            "loudness_db": round(loudness_db, 2),
        }

    finally:
        os.unlink(temp_path)


st.title("ConsultBae Audio Submission")
st.caption("Submit a short audio response for your candidate profile.")

st.subheader("Candidate details")

name = st.text_input("Full name")
phone = st.text_input("Phone number")

st.subheader("Audio")

audio_file = st.file_uploader(
    "Upload an audio file",
    type=["wav", "mp3", "m4a", "ogg", "webm"],
)

if audio_file:
    st.audio(audio_file)

    try:
        metadata = get_audio_metadata(audio_file)

        st.write("### Audio metadata")

        metadata_df = pd.DataFrame([metadata])

        st.dataframe(
            metadata_df,
            use_container_width=True,
            hide_index=True,
        )

    except Exception as e:
        st.error(f"Could not process this audio file: {e}")
        metadata = None
else:
    metadata = None


if st.button("Submit audio", type="primary"):
    if not name.strip():
        st.error("Please enter your full name.")

    elif not normalize_phone(phone):
        st.error("Please enter a valid phone number.")

    elif not audio_file:
        st.error("Please upload an audio file.")

    elif not metadata:
        st.error("Audio metadata could not be extracted.")

    else:
        with st.spinner("Submitting audio..."):
            person = find_person(phone)

            if person:
                person_id = person["person_id"]
                st.info(
                    f"Matched existing profile: {person['full_name']} "
                    f"(person_id={person_id})"
                )
            else:
                person_id = None
                st.warning(
                    "No existing person matched this phone number. "
                    "The submission will be stored without a person_id."
                )

            file_bytes = audio_file.getvalue()

            file_name = (
                f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                f"_{normalize_phone(phone)}_{audio_file.name}"
            )

            try:
                supabase.storage.from_("audio-submissions").upload(
                    file_name,
                    file_bytes,
                    {"content-type": audio_file.type},
                )

                supabase.table("audio_submissions").insert(
                    {
                        "person_id": person_id,
                        "name": name.strip(),
                        "phone": normalize_phone(phone),
                        "file_path": file_name,
                        "duration_sec": metadata["duration_sec"],
                        "sample_rate_hz": metadata["sample_rate_hz"],
                        "bitrate_kbps": metadata["bitrate_kbps"],
                        "loudness_db": metadata["loudness_db"],
                        "quality_note": "Audio metadata extracted successfully",
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).execute()

                st.success("Audio submitted successfully.")

            except Exception as e:
                st.error(f"Submission failed: {e}")


st.divider()

st.subheader("Recent submissions")

try:
    submissions = (
        supabase.table("audio_submissions")
        .select(
            "submission_id, name, phone, file_path, "
            "duration_sec, sample_rate_hz, bitrate_kbps, "
            "loudness_db, submitted_at"
        )
        .order("submission_id", desc=True)
        .limit(20)
        .execute()
    )

    rows = [dict(row) for row in (submissions.data or [])]

    if rows:
        for submission in rows:
            st.markdown(
                f"### Submission #{submission['submission_id']} — {submission['name']}"
            )

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.write(f"**Duration**  \n{submission['duration_sec']} sec")

            with col2:
                sample_rate = submission["sample_rate_hz"]

                if sample_rate:
                    st.write(f"**Sample rate**  \n{sample_rate / 1000:.1f} kHz")
                else:
                    st.write("**Sample rate**  \nN/A")

            with col3:
                bitrate = submission["bitrate_kbps"]

                if bitrate is not None:
                    st.write(f"**Bitrate**  \n{bitrate} kbps")
                else:
                    st.write("**Bitrate**  \nN/A")

            with col4:
                loudness = submission["loudness_db"]

                if loudness is not None:
                    st.write(f"**Loudness**  \n{loudness} dB")
                else:
                    st.write("**Loudness**  \nN/A")

            try:
                signed_url_response = supabase.storage.from_(
                    "audio-submissions"
                ).create_signed_url(
                    submission["file_path"],
                    3600,
                )

                signed_url = signed_url_response.get("signedURL")

                if signed_url:
                    st.audio(signed_url)
                else:
                    st.warning("Could not generate audio playback URL.")

            except Exception as e:
                st.warning(f"Could not load audio: {e}")

            st.caption(
                f"Phone: {submission['phone']} | "
                f"Submitted: {submission['submitted_at']}"
            )

            st.divider()

    else:
        st.info("No audio submissions yet.")

except Exception as e:
    st.warning(f"Could not load submissions: {e}")
