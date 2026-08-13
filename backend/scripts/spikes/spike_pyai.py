"""M7 risk-gate spike — verifies the real PyAI API surface Open Gong depends on.

Reproduces the checks recorded in FINDINGS.md against the live API. Reads
PYAI_API_KEY from backend/.env. Safe to re-run (transcribes one sample file).

    uv run python scripts/spikes/spike_pyai.py
"""

import time
from pathlib import Path

import httpx

BASE = "https://api.pyai.com/v1"
SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "samples" / "audio" / "sample-01.wav"


def _key() -> str:
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text().splitlines():
        if line.startswith("PYAI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("PYAI_API_KEY not in backend/.env")


def main() -> None:
    h = {"Authorization": f"Bearer {_key()}"}
    with httpx.Client(base_url=BASE, headers=h, timeout=60) as c:
        # auth + surface
        assert c.get("/models").status_code == 200, "auth failed"
        print("[ok] auth + /models")

        # A1 + A6: batch file upload with mono diarization
        with SAMPLE.open("rb") as f:
            r = c.post(
                "/transcription/jobs",
                files={"audio": (SAMPLE.name, f, "audio/wav")},
                data={"diarize": "true", "numerals": "true"},
            )
        assert r.status_code == 202, f"submit failed: {r.status_code} {r.text[:200]}"
        jid = r.json()["job_id"]
        print(f"[ok] A1 batch upload accepted → job {jid}")

        for _ in range(45):
            time.sleep(4)
            jr = c.get(f"/transcription/jobs/{jid}").json()
            if jr["status"] in ("completed", "failed", "cancelled"):
                break
        assert jr["status"] == "completed", f"job {jr['status']}: {jr.get('error')}"
        res = jr["result"]
        speakers = {s["speaker"] for s in res["segments"]}
        print(f"[ok] A6 mono diarization → {res['speakers']} speakers, "
              f"{len(res['segments'])} segments, {res['audio_seconds']}s audio")
        assert len(speakers) >= 2, "expected multiple speakers"

        # A6t: Trace config + rule-packs present (running it needs the paid add-on)
        tc = c.get("/trace/config").json()
        packs = c.get("/trace/rule-packs").json().get("data", [])
        print(f"[ok] Trace wired: mode={tc.get('mode')}, {len(packs)} rule-packs "
              f"(running on batch needs the Trace add-on — 402 otherwise)")

    print("\nSee FINDINGS.md for the full verdict table. Extraction stays in-house "
          "(no PyAI extraction endpoint) — that's what makes the evidence gate possible.")


if __name__ == "__main__":
    main()
