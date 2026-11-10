import os
import json
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv
import assemblyai as aai

load_dotenv()

YOUTUBE_URL = "https://www.youtube.com/watch?v=2u4ERdJiEcg"
DATA_DIR = Path("data")
OUTPUT_UTTERANCES = DATA_DIR / "custom-ai-signals.json"
OUTPUT_SENTENCES = DATA_DIR / "custom-ai-signals-sentences.json"
TRANSCRIPT_ID_FILE = DATA_DIR / ".transcript_id"


def download_audio(url: str, out_dir: str) -> str:
    out_template = os.path.join(out_dir, "audio.%(ext)s")
    # Download best audio-only stream without post-processing (no ffmpeg needed)
    subprocess.run(
        [
            "yt-dlp",
            "-f", "bestaudio",
            "--no-post-overwrites",
            "-o", out_template,
            url,
        ],
        check=True,
    )
    matches = list(Path(out_dir).glob("audio.*"))
    if not matches:
        raise FileNotFoundError("yt-dlp did not produce an audio file")
    return str(matches[0])


def get_or_create_transcript() -> aai.Transcript:
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]

    if TRANSCRIPT_ID_FILE.exists():
        transcript_id = TRANSCRIPT_ID_FILE.read_text().strip()
        print(f"Reusing existing transcript {transcript_id} ...")
        transcript = aai.Transcript.get_by_id(transcript_id)
        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"Cached transcript errored: {transcript.error}")
        return transcript

    with tempfile.TemporaryDirectory() as tmp:
        print(f"Downloading audio from {YOUTUBE_URL} ...")
        audio_path = download_audio(YOUTUBE_URL, tmp)
        print(f"Audio saved to {audio_path}")

        print("Sending to AssemblyAI for transcription with speaker labels ...")
        config = aai.TranscriptionConfig(speaker_labels=True)
        transcript = aai.Transcriber().transcribe(audio_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Transcription failed: {transcript.error}")

    TRANSCRIPT_ID_FILE.write_text(transcript.id)
    print(f"Transcript ID saved: {transcript.id}")
    return transcript


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    transcript = get_or_create_transcript()

    # Utterances (already saved on first run, but keep it idempotent)
    utterances = [
        {"speaker": u.speaker, "start": u.start, "end": u.end, "text": u.text}
        for u in transcript.utterances
    ]
    OUTPUT_UTTERANCES.write_text(json.dumps(utterances, indent=2))

    # Sentences
    sentences = [
        {"speaker": s.speaker, "start": s.start, "end": s.end, "text": s.text}
        for s in transcript.get_sentences()
    ]
    OUTPUT_SENTENCES.write_text(json.dumps(sentences, indent=2))
    print(f"Saved {len(sentences)} sentences to {OUTPUT_SENTENCES}")

    duration_min = transcript.audio_duration / 60
    print(f"Total video length: {duration_min:.2f} minutes")
    print(f"Number of sentences: {len(sentences)}")


if __name__ == "__main__":
    main()
