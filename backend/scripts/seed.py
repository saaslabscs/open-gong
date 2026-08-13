"""Seed the database: built-in skills, the Call Summarizer agent, the
orchestrator, and the five sample calls (re-processed through the real
agent path — see
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md
Known risks #1 for why this requires live API keys).

Idempotent: re-running updates in place (keyed on Call.external_id / Skill
name / Agent name).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import Base, engine, get_session
from app.jobs import enqueue, run_due_jobs
from app.models import Agent, AgentSkill, Call, Orchestrator, Run, Skill, Transcript

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "samples"

BUILTIN_SKILLS = [
    {
        "name": "summary-and-next-steps",
        "description": "Summarizes the call and extracts committed next steps",
        "when_to_use": "Always — every call needs a summary",
        "fields": {"claims": ["summary", "next_steps"]},
        "body_md": (
            "Extract a factual summary (3-5 bullets covering what happened on the "
            "call) and the concrete next steps committed to, with an owner for "
            "each. Every claim must cite a verbatim quote with its line number. "
            "Never invent or embellish."
        ),
    },
    {
        "name": "sales-scorecard",
        "description": "Scores discovery quality and MEDDIC coverage on sales calls",
        "when_to_use": "The call is a sales conversation — discovery, demo, pricing, or negotiation",
        "fields": {
            "checks": [
                "recording_disclosure", "budget_discussed", "decision_process_identified",
                "timeline_identified", "next_step_secured",
            ],
            "scores": [
                {"name": "discovery_quality", "max": 5},
                {"name": "objection_handling", "max": 5},
            ],
        },
        "body_md": (
            "Score this call against a sales rubric. Checks (true only with "
            "supporting evidence, false/null otherwise): was the call recording "
            "disclosed; was budget discussed; were other decision makers "
            "identified; was a timeline established; was a concrete next step "
            "secured. Scores (1-5, with justification and evidence): "
            "discovery_quality — how well did the rep uncover pain, urgency, "
            "buying process, and budget through open questions rather than "
            "pitching; objection_handling — how directly and credibly were "
            "objections answered."
        ),
    },
    {
        "name": "support-scorecard",
        "description": "Scores issue resolution and empathy on support calls",
        "when_to_use": "The call is a support conversation — existing customer issues, billing, cancellations",
        "fields": {
            "checks": [
                "recording_disclosure", "issue_identified", "resolution_provided",
                "timeline_communicated", "churn_risk_flagged",
            ],
            "scores": [
                {"name": "empathy_and_tone", "max": 5},
                {"name": "resolution_quality", "max": 5},
            ],
        },
        "body_md": (
            "Score this call against a support rubric. Checks (true only with "
            "supporting evidence, false/null otherwise): was the call recording "
            "disclosed; was the customer's issue clearly identified; was a "
            "resolution provided or concretely promised; was a resolution "
            "timeline communicated; did the customer signal cancellation or "
            "churn risk. Scores (1-5, with justification and evidence): "
            "empathy_and_tone — did the agent acknowledge frustration and stay "
            "helpful under pressure; resolution_quality — was the root cause "
            "found and fully addressed with clear next-step communication."
        ),
    },
    {
        "name": "compliance-check",
        "description": "Flags compliance risk: recording disclosure, unsubstantiated claims, pressure tactics, PII exposure",
        "when_to_use": "Always — every call should be checked for compliance risk",
        "fields": {
            "checks": ["recording_disclosure_given"],
            "claims": ["compliance_findings"],
        },
        "body_md": (
            "Check this call for compliance risk. recording_disclosure_given: "
            "true only if someone explicitly disclosed the call is recorded, "
            "with the disclosing quote as evidence; false/null if not (you "
            "cannot cite evidence for an absence, so leave evidence empty in "
            "that case). compliance_findings: list any of the following, each "
            "with a verbatim quote and line number as evidence — an "
            "unsubstantiated guaranteed-outcome claim; high-pressure or "
            "artificial-urgency tactics; sensitive personal data (SSN, full "
            "card number, health details) spoken aloud unnecessarily. Never "
            "invent a finding; if nothing applies, return an empty list."
        ),
    },
    {
        "name": "follow-up-email",
        "description": "Drafts a follow-up email grounded in what was agreed on the call",
        "when_to_use": "Always — every call benefits from a drafted follow-up",
        "fields": {"claims": ["email_draft"]},
        "body_md": (
            "Draft a short, professional follow-up email from the company rep "
            "to the customer, grounded ONLY in what was agreed on this call. "
            "Do not promise anything not discussed. Plain text, no placeholders "
            "like [Name] — use the actual names from the transcript. Return it "
            "as a single email_draft claim whose text is 'Subject: ...\\n\\n"
            "<body>' and whose evidence cites the next-step commitments it "
            "draws from."
        ),
    },
]


def seed_agents_and_skills() -> None:
    with get_session() as session:
        if session.scalars(select(Orchestrator)).first() is None:
            session.add(Orchestrator(system_prompt=(
                "Decide which agents this call needs. There is currently one "
                "agent, Call Summarizer — dispatch it for every call that has "
                "a transcript, unless the transcript is too short to say "
                "anything meaningful about."
            )))

        skill_rows = {}
        for spec in BUILTIN_SKILLS:
            row = session.scalars(select(Skill).where(Skill.name == spec["name"])).first()
            if row is None:
                row = Skill(name=spec["name"], source="ui")
                session.add(row)
            row.description = spec["description"]
            row.when_to_use = spec["when_to_use"]
            row.fields = spec["fields"]
            row.body_md = spec["body_md"]
            session.flush()
            skill_rows[spec["name"]] = row

        agent = session.scalars(select(Agent).where(Agent.name == "Call Summarizer")).first()
        if agent is None:
            agent = Agent(name="Call Summarizer", description="", system_prompt="")
            session.add(agent)
        agent.description = "Summarizes calls and scores them against sales/support/compliance rubrics"
        agent.system_prompt = (
            "Always run summary-and-next-steps and compliance-check. Use "
            "sales-scorecard for sales calls (discovery, demo, pricing, "
            "negotiation) and support-scorecard for support calls (existing "
            "customer issues, billing, cancellations) — not both. Always run "
            "follow-up-email last."
        )
        session.flush()

        existing_links = {
            l.skill_id for l in session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent.id)).all()
        }
        for skill_name, row in skill_rows.items():
            if row.id not in existing_links:
                session.add(AgentSkill(agent_id=agent.id, skill_id=row.id))

        session.commit()


def seed_sample_calls() -> int:
    count = 0
    with get_session() as session:
        for path in sorted(SAMPLES_DIR.glob("*.json")):
            data = json.loads(path.read_text())
            c = data["call"]
            external_id = f"sample:{c['id']}"

            call = session.scalars(select(Call).where(Call.external_id == external_id)).first()
            if call is None:
                call = Call(id=c["id"], external_id=external_id)
                session.add(call)
            call.title = c["title"]
            call.source = c["source"]
            call.duration_s = c["duration_s"]
            audio = SAMPLES_DIR / "audio" / f"{c['id']}.wav"
            call.audio_path = str(audio) if audio.exists() else None

            if call.transcript is None:
                call.transcript = Transcript(call_id=call.id, lines=[])
            call.transcript.language = data["transcript"]["language"]
            call.transcript.lines = data["transcript"]["lines"]

            run = session.scalars(select(Run).where(Run.call_id == call.id)).first()
            if run is None:
                run = Run(call_id=call.id)
                session.add(run)
            run.status = "running"
            run.stages = [{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}]

            count += 1
        session.commit()

    for path in sorted(SAMPLES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        enqueue("run_insights", {"call_id": data["call"]["id"]})
    run_due_jobs()
    return count


def seed() -> int:
    Base.metadata.create_all(engine)
    seed_agents_and_skills()
    return seed_sample_calls()


if __name__ == "__main__":
    n = seed()
    print(f"seeded {n} sample call(s)")
