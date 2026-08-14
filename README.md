# Open Gong

**Open-source conversation intelligence. Every claim has a receipt.**

Upload a call recording or paste a link. Get a speaker-labeled transcript, summary, objections, intent, next steps, and a follow-up email draft — where **every claim links to the exact line in the call that proves it**. No proof in the transcript, no claim in the notes.

![A call's notes beside its transcript. Every claim carries a "❝ proof" chip; clicking one scrolls the transcript to the exact line that proves it and flashes it.](docs/images/call-detail.png)

Click any **❝ proof** chip and the transcript jumps to the line that backs that claim. A claim with two supporting quotes says so (**proof ·2**). Nothing reaches the notes without one.

![The call log: date, call, source, length, status and agent columns, with search, date-range, source, status and agent filters.](docs/images/call-log.png)

## Demo in seconds

Five sample calls ship in the repo with precomputed results — **no API keys needed**:

```bash
git clone https://github.com/YOURORG/open-gong && cd open-gong
make demo
# → http://localhost:3000
```

Requires [uv](https://docs.astral.sh/uv/) and Node 20+. That's it.

## Process your own calls

```bash
open-gong init   # mints a PyAI sandbox key (or walks you through pasting one)
```

Then upload a recording or paste a URL in the UI. Calls are transcribed via [PyAI](https://docs.pyai.com) and analyzed with Claude.

<!-- TODO(M7): publish measured cost-per-call here -->

## What you get per call

- **Transcript** with speaker names, numbered lines
- **Summary, objections, intent (sales/support), next steps, follow-up email**
- **Receipts**: every claim carries `{quote, line}` evidence — click any claim to jump to the exact moment in the call
- **Scorecards**: built-in sales & support packs, or describe your own in plain English ("score against MEDDIC, flag churn risk") and Open Gong compiles it into a pack
- **Exports**: Markdown, JSON, or a revocable share link

## The harness (guardrails, not vibes)

- **Bad JSON gets caught, never shipped** — schema validation with one repair retry
- **No proof, no claim** — every claim's quote is verified against its cited line; unverifiable claims are dropped and logged
- **Failed parts retry with the reason attached** — capped retries per stage
- **Every run ends with a clear status**: `shipped`, `partial`, or `failed` — no zombie states
- **Budgets** — per-run cost cap; breach means a clean stop, not a surprise bill

## Data retention

Sample audio ships with the repo. Audio you upload stays on your machine (SQLite + local files by default) and is deletable via a single endpoint. This is a tool that flags compliance issues — it shouldn't hoard your recordings.

## Status

Early. Phase 1 (this repo): the evidence-cited notes loop, telephony recordings only. On the roadmap: CRM sync with human approval before every write, web-meeting bots, live in-call coaching, cross-call memory. See [the plan](#) for scope decisions.

## License

MIT
