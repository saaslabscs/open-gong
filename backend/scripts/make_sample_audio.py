"""Generate sample-call audio from the fixture transcripts (macOS only).

Uses the built-in `say` TTS with a distinct voice per speaker, converts each
line to WAV via `afconvert`, and concatenates with the stdlib `wave` module.
Output: backend/fixtures/samples/audio/<call_id>.wav

These are synthetic stand-ins so the repo ships runnable audio with zero
licensing concerns; M8 replaces the precomputed results with real PyAI output
from these same files.

Usage: python scripts/make_sample_audio.py [fixture.json ...]
       (no args = all fixtures in fixtures/samples/)
"""

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "samples"
AUDIO_DIR = SAMPLES_DIR / "audio"

# Rotate through built-in voices; assigned per speaker in order of appearance.
VOICES = ["Samantha", "Daniel", "Karen", "Fred"]

# 400ms of silence between turns, at say's default 22050 Hz mono 16-bit.
GAP_FRAMES = int(0.4 * 22050)


def synthesize(fixture_path: Path) -> Path:
    data = json.loads(fixture_path.read_text())
    call_id = data["call"]["id"]
    lines = data["transcript"]["lines"]

    speakers = list(dict.fromkeys(l["speaker"] for l in lines))
    voice_for = {s: VOICES[i % len(VOICES)] for i, s in enumerate(speakers)}

    AUDIO_DIR.mkdir(exist_ok=True)
    out_path = AUDIO_DIR / f"{call_id}.wav"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        out = wave.open(str(out_path), "wb")
        params_set = False
        for l in lines:
            aiff = tmp_dir / f"{l['line']}.aiff"
            piece = tmp_dir / f"{l['line']}.wav"
            subprocess.run(
                ["say", "-v", voice_for[l["speaker"]], "-o", str(aiff), l["text"]],
                check=True,
            )
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@22050", "-c", "1", str(aiff), str(piece)],
                check=True,
            )
            with wave.open(str(piece), "rb") as w:
                if not params_set:
                    out.setparams(w.getparams())
                    params_set = True
                out.writeframes(w.readframes(w.getnframes()))
                out.writeframes(b"\x00\x00" * GAP_FRAMES)
        out.close()
    return out_path


if __name__ == "__main__":
    targets = [Path(p) for p in sys.argv[1:]] or sorted(SAMPLES_DIR.glob("*.json"))
    for path in targets:
        print(f"synthesizing {path.name} ...", flush=True)
        out = synthesize(path)
        print(f"  → {out.relative_to(SAMPLES_DIR.parent.parent)}")
