"""Real PyAI adapter — batch transcription via the verified API (see
scripts/spikes/FINDINGS.md).

submit → POST /v1/transcription/jobs (multipart, diarize=true), returns job_id.
fetch → GET /v1/transcription/jobs/{id}: completed → normalized lines;
        queued/running → None; failed → raise.

Poll-based so it works without a public webhook URL. Large results are
offloaded to result_url, which we follow.
"""

import os

import httpx

from ...transcription import segments_to_lines

BASE = os.environ.get("PYAI_API_BASE", "https://api.pyai.com/v1")


class TranscriptionFailed(Exception):
    pass


class RealPyAI:
    def _headers(self) -> dict:
        key = os.environ.get("PYAI_API_KEY")
        if not key:
            raise TranscriptionFailed("PYAI_API_KEY not set")
        return {"Authorization": f"Bearer {key}"}

    def submit_recording(self, call_id: str, audio_path: str) -> str:
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{BASE}/transcription/jobs",
                headers=self._headers(),
                files={"audio": (os.path.basename(audio_path), f, "audio/wav")},
                data={"diarize": "true", "numerals": "true"},
                timeout=60,
            )
        if resp.status_code != 202:
            raise TranscriptionFailed(f"submit HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()["job_id"]

    def fetch_transcript(self, job_ref: str) -> dict | None:
        resp = httpx.get(f"{BASE}/transcription/jobs/{job_ref}", headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise TranscriptionFailed(f"poll HTTP {resp.status_code}: {resp.text[:200]}")
        job = resp.json()
        status = job.get("status")
        if status in ("queued", "running"):
            return None
        if status != "completed":
            raise TranscriptionFailed(f"job {status}: {job.get('error', 'unknown')}")

        result = job.get("result")
        if result is None and job.get("result_url"):
            result = httpx.get(job["result_url"], timeout=30).json()
        segments = (result or {}).get("segments", [])
        if not segments:
            raise TranscriptionFailed("completed job had no segments")
        return {"language": "en", "lines": segments_to_lines(segments)}
