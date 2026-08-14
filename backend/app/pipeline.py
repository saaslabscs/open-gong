"""Pipeline job handlers.

`process_call`: submit audio to PyAI (mock by default); transcript returns via
the signed webhook, which enqueues `run_insights`.

`run_insights`: orchestrator dispatch -> per-agent AgentRun -> skill router ->
skill execution. Every AgentRun finishes shipped | partial | failed, and the
call's Run status aggregates across all its AgentRuns. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4, §5.
"""

import re
from datetime import datetime, timezone

from sqlalchemy import delete, select

from . import insights as insights_mod
from . import orchestrator as orchestrator_mod
from . import skill_router as skill_router_mod
from .adapters.pyai.base import get_adapter
from .agent_runtime import AgentRunState, new_agent_run_state
from .db import get_session
from .entry_rules import resolve_entry_rule
from .evidence import validate_extraction
from .insights import prettify_transcript
from .jobs import enqueue, handler
from .models import Agent, AgentRun, AgentSkill, Call, Run, Skill
from .run_state import BudgetExceeded, CRITICAL_STAGES, STAGES, StageFailed, max_cost_per_run
from .skills import executor as executor_mod
from .transcription import deliver_transcript, update_transcript_lines

# Module-qualified references (not `from .orchestrator import dispatch`) so
# tests can monkeypatch orchestrator.dispatch / skill_router.route_skills /
# skills.executor.run_skill per-test — a plain `from X import Y` would bind
# the function once at pipeline's first import and never see later patches.

POLL_INTERVAL_S = 5
MAX_POLLS = 120


def _update_stage(run: Run, name: str, **updates) -> None:
    stages = [dict(s) for s in run.stages]
    for s in stages:
        if s["name"] == name:
            s.update(updates)
    run.stages = stages


@handler("process_call")
def process_call(payload: dict) -> None:
    call_id = payload["call_id"]
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None or not call.audio_path:
            raise ValueError(f"call {call_id} missing or has no audio")
        run = session.scalars(
            select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
        ).first()
        _update_stage(run, "transcribe", attempts=1)
        session.commit()
        audio_path = call.audio_path

    job_ref = get_adapter().submit_recording(call_id, audio_path)
    enqueue("poll_transcription", {"call_id": call_id, "job_ref": job_ref, "polls": 0})


@handler("poll_transcription")
def poll_transcription(payload: dict) -> None:
    """Poll the transcription job; deliver when ready, else re-enqueue.

    Works for both adapters: the mock returns a transcript immediately, the
    real one returns None until PyAI finishes. Terminal failure marks the run
    failed (no zombie 'running' state) rather than retrying forever.
    """
    call_id, job_ref, polls = payload["call_id"], payload["job_ref"], payload.get("polls", 0)
    try:
        transcript = get_adapter().fetch_transcript(job_ref)
    except Exception as e:  # noqa: BLE001 — terminal transcription failure
        _fail_transcribe(call_id, str(e))
        return

    if transcript is None:
        if polls >= MAX_POLLS:
            _fail_transcribe(call_id, f"transcription {job_ref} did not complete in time")
            return
        enqueue(
            "poll_transcription",
            {"call_id": call_id, "job_ref": job_ref, "polls": polls + 1},
            delay_s=POLL_INTERVAL_S,
        )
        return
    deliver_transcript(call_id, transcript)


def _fail_transcribe(call_id: str, reason: str) -> None:
    with get_session() as session:
        run = session.scalars(
            select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
        ).first()
        if run is None:
            return
        stages = [dict(s) for s in run.stages]
        for s in stages:
            if s["name"] == "transcribe":
                s["status"] = "failed"
                s["error"] = reason
        run.stages = stages
        run.status = "failed"
        session.commit()


def _agent_dict(agent: Agent) -> dict:
    return {"id": agent.id, "name": agent.name, "description": agent.description}


def _skill_dict(skill: Skill) -> dict:
    return {
        "id": skill.id, "name": skill.name, "description": skill.description,
        "when_to_use": skill.when_to_use, "body_md": skill.body_md, "fields": skill.fields,
    }


def _run_one_agent(agent: Agent, skills: list[Skill], call_id: str, run_id: str, lines: list[dict]) -> float:
    """Runs one agent's routed skills to a terminal AgentRun status. Returns
    the cost actually spent, so the caller can accumulate the run's aggregate
    budget directly rather than re-querying (a re-query by insertion order is
    unreliable across databases and corruptible by retry duplicates — see
    Task 9 review finding #3).

    Any unexpected failure (e.g. skill routing itself raising, not just a
    routed skill's execution) still finalizes this AgentRun to "failed"
    rather than leaving it stuck "running" — a zombie AgentRun would wedge
    the aggregate status and give no signal a retry is needed. Sibling
    agents are unaffected since this never re-raises. See Task 9 review
    finding #2.
    """
    with get_session() as session:
        agent_run = AgentRun(call_id=call_id, run_id=run_id, agent_id=agent.id, status="running", steps=[])
        session.add(agent_run)
        session.commit()
        agent_run_id = agent_run.id

    try:
        skill_ids, router_reasoning, router_cost = skill_router_mod.route_skills(
            lines, agent.system_prompt, [_skill_dict(s) for s in skills]
        )
        routed_skills = [s for s in skills if s.id in skill_ids]

        rs = new_agent_run_state([s.name for s in routed_skills])
        output: dict = {}
        total_cost = router_cost

        for skill in routed_skills:
            def run_one(sk=skill):
                cleaned, dropped, cost = executor_mod.run_skill(_skill_dict(sk), lines)
                rs.charge(sk.name, cost)
                rs._get(sk.name).dropped_claims = len(dropped)
                return cleaned

            try:
                output[skill.name] = rs.execute(skill.name, run_one)
            except BudgetExceeded:
                rs.skip_remaining(skill.name)
                break
            except StageFailed:
                continue

        total_cost += sum(s.cost_usd for s in rs.steps)

        with get_session() as session:
            agent_run = session.get(AgentRun, agent_run_id)
            agent_run.steps = rs.as_dicts()
            agent_run.routing_reasoning = router_reasoning
            agent_run.status = rs.final_status()
            agent_run.output = output or None
            agent_run.cost_usd = round(total_cost, 4)
            agent_run.finished_at = datetime.now(timezone.utc)
            session.commit()
        return total_cost
    except Exception as e:  # noqa: BLE001 — reason recorded on the AgentRun, not swallowed
        with get_session() as session:
            agent_run = session.get(AgentRun, agent_run_id)
            agent_run.status = "failed"
            agent_run.error = str(e)
            agent_run.finished_at = datetime.now(timezone.utc)
            session.commit()
        return 0.0


_ID_TITLE = re.compile(r"^(RE|CA)?[0-9a-f]{16,}$", re.I)


def _derive_title(current: str, baseline: dict) -> str | None:
    """A bare recording id tells a reader nothing. Use the first summary claim."""
    if not _ID_TITLE.match(current or ""):
        return None
    claims = baseline.get("summary") or []
    if not claims:
        return None
    text = claims[0]["text"].strip()
    return text[:70].rstrip(" .,;:") if text else None


def _persist_baseline(run_id: str, rs: AgentRunState, baseline: dict, dropped: list[dict]) -> None:
    """Write the baseline stages and insights onto the Run.

    Unions into Run.stages by name: updates entries that exist (so `transcribe`,
    already ok, survives) and appends the ones that don't. Appending matters —
    a Run built before this change, or by a test, carries only `transcribe`, and
    an update-only merge would silently drop the baseline stages.
    """
    with get_session() as session:
        run = session.get(Run, run_id)
        by_name = {s["name"]: s for s in rs.as_dicts()}
        merged = [dict(s, **by_name.pop(s["name"], {})) for s in run.stages]
        merged.extend(by_name[n] for n in STAGES if n in by_name)
        run.stages = merged
        run.insights = baseline or None
        run.cost_usd = round((run.cost_usd or 0.0) + rs.spent, 4)
        new_title = _derive_title(run.call.title, baseline)
        if new_title:
            run.call.title = new_title
        session.commit()


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "shipped"
    if all(s == "shipped" for s in statuses):
        return "shipped"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "partial"


@handler("run_insights")
def run_insights(payload: dict) -> None:
    call_id = payload["call_id"]
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None or call.transcript is None:
            raise ValueError(f"call {call_id} has no transcript")
        run = session.scalars(select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())).first()
        run_id = run.id

        # A retry (via POST /api/calls/{id}/retry) or a re-seed re-enters this
        # handler for a Run that may already carry AgentRuns from a prior
        # attempt. Clear them before dispatching again — otherwise duplicates
        # accumulate: doubled exports, a stale "failed" row that keeps the
        # aggregate status stuck "partial" forever even after a clean retry,
        # and double-counted cost. See Task 9 review finding #1.
        #
        # But human edits must survive a retry: before this delete existed,
        # retry never touched edited_output at all. Carry each agent's edit
        # forward by agent_id so it can be re-attached to that agent's fresh
        # AgentRun below. An agent that isn't dispatched again simply has
        # nowhere to land its edit — that's accepted.
        preserved_edits = {
            ar.agent_id: ar.edited_output
            for ar in session.scalars(select(AgentRun).where(AgentRun.run_id == run_id)).all()
            if ar.edited_output is not None
        }
        session.execute(delete(AgentRun).where(AgentRun.run_id == run_id))
        # Every cost write below this point is additive (baseline, dispatch,
        # agents) — zero the total once here, on entry, or a retry re-enters
        # this handler and piles the new attempt's cost on top of the old
        # one. POST /api/calls/{id}/retry resets stage bookkeeping and
        # run.status but never touches run.cost_usd, so this is the only
        # place a retry's cost total gets reset.
        run.cost_usd = 0.0
        session.commit()

        lines = call.transcript.lines

    # The guaranteed baseline. Runs before dispatch so agents never gate it.
    rs = new_agent_run_state(["summarize", "compose_email"], critical=CRITICAL_STAGES)
    baseline: dict = {}
    dropped: list[dict] = []

    def do_summarize():
        raw, cost = insights_mod.summarize(lines)
        rs.charge("summarize", cost)
        cleaned, drops = validate_extraction(raw, lines)
        dropped.extend(drops)
        rs._get("summarize").dropped_claims = len(drops)
        return cleaned

    try:
        baseline = rs.execute("summarize", do_summarize)
    except (StageFailed, BudgetExceeded):
        rs.skip_remaining("summarize")
        _persist_baseline(run_id, rs, baseline, dropped)
        # A failed critical stage stops the chain here — agents never dispatch
        # over a call with no summary. Still must land in a terminal state, or
        # the Run is stuck "running" forever with no retry signal.
        with get_session() as session:
            run = session.get(Run, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
        return

    def do_email():
        email, cost = insights_mod.compose_email(lines, baseline)
        rs.charge("compose_email", cost)
        return email

    try:
        baseline["follow_up_email"] = rs.execute("compose_email", do_email)
    except (StageFailed, BudgetExceeded):
        pass  # non-critical: the summary still ships

    baseline["dropped_claims"] = len(dropped)
    _persist_baseline(run_id, rs, baseline, dropped)

    try:
        _dispatch_and_run(call_id, run_id, preserved_edits, rs)
    except Exception as e:  # noqa: BLE001 — reason recorded on the Run, not swallowed
        # An unexpected failure anywhere in dispatch/routing/execution (e.g.
        # malformed LLM JSON the orchestrator itself doesn't guard against)
        # must still leave the Run in a terminal state. Left "running", the
        # call would be stuck AND un-retryable — POST /retry 409s on
        # anything that isn't "failed" or "partial". See Task 9 review
        # finding #2.
        with get_session() as session:
            run = session.get(Run, run_id)
            if run is not None:
                note = f"run_insights failed unexpectedly: {e}"
                run.status = "failed"
                run.orchestrator_reasoning = (
                    f"{run.orchestrator_reasoning}\n\n{note}" if run.orchestrator_reasoning else note
                )
                run.finished_at = datetime.now(timezone.utc)
                session.commit()


def _dispatch_and_run(
    call_id: str, run_id: str, preserved_edits: dict[str, dict] | None, rs: AgentRunState
) -> None:
    with get_session() as session:
        call = session.get(Call, call_id)
        run = session.get(Run, run_id)
        lines = call.transcript.lines

        try:
            pretty, fmt_cost = prettify_transcript(lines)
            if pretty is not lines:
                update_transcript_lines(call_id, pretty)
                lines = pretty
        except Exception:
            fmt_cost = 0.0

        entry_agent_id = resolve_entry_rule(session, call)
        enabled_agents = session.scalars(select(Agent).where(Agent.enabled.is_(True))).all()
        # The agent flagged is_orchestrator (at most one, enforced in api/agents.py)
        # supplies the dispatch prompt and is excluded from the candidates dispatch()
        # can select — its job is routing, not producing call notes.
        orchestrator_agent = next((a for a in enabled_agents if a.is_orchestrator), None)
        dispatchable_agents = [a for a in enabled_agents if not a.is_orchestrator]

        if entry_agent_id:
            selected_ids, reasoning, dispatch_cost = [entry_agent_id], "Entry rule pinned this agent.", 0.0
        else:
            orch_prompt = (
                orchestrator_agent.system_prompt if orchestrator_agent else "Decide which agents this call needs."
            )
            selected_ids, reasoning, dispatch_cost = orchestrator_mod.dispatch(
                lines, [_agent_dict(a) for a in dispatchable_agents], orch_prompt
            )

        selected_agents = [a for a in enabled_agents if a.id in selected_ids]
        agent_skills: dict[str, list[Skill]] = {}
        for agent in selected_agents:
            links = session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent.id)).all()
            skill_ids = [l.skill_id for l in links]
            agent_skills[agent.id] = (
                session.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all() if skill_ids else []
            )

        run.orchestrator_reasoning = reasoning
        # Additive, not a reset: the guaranteed baseline (summarize/compose_email)
        # already persisted its own cost onto this Run before dispatch started.
        run.cost_usd = round((run.cost_usd or 0.0) + fmt_cost + dispatch_cost, 4)
        session.commit()

        agents_to_run = [(a, agent_skills[a.id]) for a in selected_agents]
        spent_so_far = round(fmt_cost + dispatch_cost, 4)
        budget = max_cost_per_run()

    budget_note = None
    for agent, skills in agents_to_run:
        if spent_so_far > budget:
            budget_note = (
                f"run budget ${budget:.2f} exceeded before agent {agent.name!r} could run "
                f"(spent ${spent_so_far:.4f}) — remaining agents skipped"
            )
            break
        # Accumulate directly from _run_one_agent's return value — a
        # re-query "pick the last row" is order-dependent (unreliable on
        # Postgres, and wrong under retry duplicates). See Task 9 review
        # finding #3.
        spent_so_far = round(spent_so_far + _run_one_agent(agent, skills, call_id, run_id, lines), 4)

    with get_session() as session:
        run = session.get(Run, run_id)
        agent_runs = session.scalars(select(AgentRun).where(AgentRun.run_id == run_id)).all()
        # Re-attach any human edit captured before the pre-dispatch wipe, so a
        # retry re-runs the AI without silently discarding reviewed output.
        for ar in agent_runs:
            if preserved_edits and ar.agent_id in preserved_edits:
                ar.edited_output = preserved_edits[ar.agent_id]
        run.status = _aggregate_status([rs.final_status()] + [ar.status for ar in agent_runs])
        run.cost_usd = round(run.cost_usd + sum(ar.cost_usd for ar in agent_runs), 4)
        if budget_note:
            run.orchestrator_reasoning = f"{run.orchestrator_reasoning}\n\n{budget_note}"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
