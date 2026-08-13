# M7 — PyAI Spike Findings

Run against the live API (`https://api.pyai.com/v1`) on 2026-08-13 with a
pay-as-you-go key. Source of truth: `GET https://api.pyai.com/openapi.json`
(90 endpoints).

## Verdicts

| # | Assumption | Verdict | Evidence |
|---|---|---|---|
| A1 | Batch recording import outside a live session | ✅ **CONFIRMED** | `POST /v1/transcription/jobs` accepts `multipart/form-data` (file) **or** `application/json` (`audio_url`). Uploaded `sample-01.wav` → `202` → job `completed`. |
| A2 | Sandbox key self-mints on first run | ✅ **CONFIRMED (mechanism)** | `POST /v1/sandbox/keys` needs **no auth**, returns a `pyai_test_...` key. This network was over its sandbox quota (`429`), so M6 keeps the guided-paste fallback. |
| A3 | Speaker names | ⚠️ **Partial** | Diarization returns generic labels `speaker_1`, `speaker_2` — not names. Name assignment is our job (later, optional). |
| A6 | Mono diarization | ✅ **CONFIRMED** | `diarize=true` on the mono `sample-01.wav` returned `speakers: 2`, 21 segments matching the 21 turns exactly. (`channel=stereo` also available for dual-channel.) |
| A4/A5 | PyAI structured extraction with line-anchored quotes / per-team schemas | ❌ **NOT OFFERED** | No extraction-schema endpoint exists. PyAI gives transcript + Recap only. **Our in-house extractor (already built in M3) is the correct and only path** — this retires the risk in our favor. |
| A6t | Trace scores batch calls | ⚠️ **Gated** | Trace exists and is wired for batch (`trace=true` param on jobs; `/v1/trace/config` returns `mode: warn`; `/v1/trace/rule-packs` lists built-ins). But `trace=true` → `402 trace_not_enabled` (paid add-on, not on this org). So Trace stays **optional with graceful degradation** (plan already assumed this); demo compliance panel uses the fixture. |
| A7 | Cost is cents not dollars | ✅ **Plausible** | PAYG plan; job reports `audio_seconds` (per-second Hear billing). ~2.9-min call transcribed fine. Exact per-second rate: pricing page. |

## Real endpoints we depend on (verified)

- **Transcribe (batch):** `POST /v1/transcription/jobs`
  - multipart: `audio` (binary), `diarize=true|false`, `channel=true|false|stereo`, `numerals`, `output_formats`, `webhook_url`, `trace`
  - or JSON: `{audio_url, model, diarize, channel, webhook_url}`
  - `model` default `pyai-hear-telephony`
  - → `202 TranscriptionJob` (async)
- **Poll:** `GET /v1/transcription/jobs/{id}` → `TranscriptionJob`
  - `result.segments[] = {id, start, end, text, speaker, channel}`, `result.speakers`, `result.audio_seconds`
  - large results offloaded to `result_url`
- **Webhook secret:** `GET/POST /v1/webhooks/signing-secret` (for `webhook_url` HMAC)
- **Recap (free summary/actions):** `GET/POST /v1/recap/calls/{call_id}`, `GET/PUT /v1/recap/config`
- **Trace (optional):** `GET/PUT /v1/trace/config`, `GET /v1/trace/interactions[/{id}]`, `GET /v1/trace/rule-packs`
- **Sandbox key:** `POST /v1/sandbox/keys` (no auth)

## Consequences for the build

1. **M8 (real adapter):** submit to `POST /v1/transcription/jobs` with `diarize=true` + `webhook_url` pointing at our `/webhooks/pyai`; translate `result.segments` → our numbered `{line, speaker, text}` lines. No polling needed if we use `webhook_url`, but keep a poll fallback.
2. **Extraction stays in-house** (M3) — PyAI has no structured-extraction endpoint. Good: it's what makes the evidence gate possible.
3. **Trace is opt-in** (M10) — enable only when the org has the entitlement; degrade gracefully otherwise.
4. **M6 onboarding:** try `POST /v1/sandbox/keys`; on `429`/limit, fall back to guided paste.
5. Speaker labels are `speaker_N`; map to names later if wanted (not required for the loop).
