"""M9: compile prose → valid pack, review/edit/activate, active pack wins."""

import jsonschema
from fastapi.testclient import TestClient

import app.pack_compiler as pc
from app.main import app
from app.packs import assemble_pack

COMPILER_OUT = {
    "name": "meddic-sales",
    "applies_to": "sales",
    "deterministic_fields": [
        {"name": "economic_buyer_identified", "description": "Was the economic buyer identified?"},
        {"name": "competitor_mentioned", "description": "Did a competitor come up?"},
    ],
    "judgment_fields": [
        {"name": "meddic_completeness", "max_score": 5, "instructions": "How fully was MEDDIC covered?"},
    ],
}


def test_assemble_pack_is_always_valid_structure():
    pack = assemble_pack(
        "x",
        [{"name": "budget_confirmed", "description": "budget?"}],
        [{"name": "quality", "max_score": 5, "instructions": "how good?"}],
    )
    # the json_schema is itself a valid JSON Schema and has common + custom fields
    jsonschema.Draft202012Validator.check_schema(pack["json_schema"])
    props = pack["json_schema"]["properties"]
    assert "summary" in props and "budget_confirmed" in props
    assert pack["scoring_spec"]["deterministic"] == ["budget_confirmed"]
    assert pack["scoring_spec"]["judgment"][0]["name"] == "quality"


def test_compile_produces_runnable_pack(monkeypatch):
    monkeypatch.setattr(pc, "complete_json", lambda *a, **k: (COMPILER_OUT, 0.004))
    pack, cost = pc.compile_pack("Sales team, score against MEDDIC, flag competitors")
    assert cost == 0.004
    jsonschema.Draft202012Validator.check_schema(pack["json_schema"])
    assert "economic_buyer_identified" in pack["json_schema"]["properties"]
    assert "meddic_completeness" in [j["name"] for j in pack["scoring_spec"]["judgment"]]


def test_compile_review_edit_activate_flow(monkeypatch):
    monkeypatch.setattr(pc, "complete_json", lambda *a, **k: (COMPILER_OUT, 0.004))
    with TestClient(app) as c:
        # compile → draft
        draft = c.post("/api/packs/compile", json={"instructions": "MEDDIC please"}).json()
        assert draft["status"] == "draft"
        pid = draft["id"]

        # edit the draft (human renames a field via scoring_spec)
        spec = draft["scoring_spec"]
        spec["deterministic"].append("security_asked")
        r = c.patch(f"/api/packs/{pid}", json={"scoring_spec": spec, "name": "meddic-v2"})
        assert r.json()["name"] == "meddic-v2"

        # activate
        act = c.post(f"/api/packs/{pid}/activate")
        assert act.json()["status"] == "active"

        # listed and active
        packs = c.get("/api/packs").json()
        assert any(p["id"] == pid and p["status"] == "active" for p in packs)


def test_active_pack_overrides_builtin(monkeypatch):
    monkeypatch.setattr(pc, "complete_json", lambda *a, **k: (COMPILER_OUT, 0.004))
    with TestClient(app) as c:
        pid = c.post("/api/packs/compile", json={"instructions": "x"}).json()["id"]
        c.post(f"/api/packs/{pid}/activate")

    from app.pipeline import select_pack
    pack = select_pack("support")  # even for support intent, active custom pack wins
    assert pack["name"] == "meddic-sales"

    # deactivate → back to built-ins
    with TestClient(app) as c:
        c.post("/api/packs/deactivate")
    assert select_pack("support")["name"] == "support-default"


def test_activating_one_retires_others(monkeypatch):
    monkeypatch.setattr(pc, "complete_json", lambda *a, **k: (COMPILER_OUT, 0.004))
    with TestClient(app) as c:
        a = c.post("/api/packs/compile", json={"instructions": "a"}).json()["id"]
        b = c.post("/api/packs/compile", json={"instructions": "b"}).json()["id"]
        c.post(f"/api/packs/{a}/activate")
        c.post(f"/api/packs/{b}/activate")
        packs = {p["id"]: p["status"] for p in c.get("/api/packs").json()}
        assert packs[b] == "active"
        assert a not in packs or packs.get(a) != "active"  # a retired (filtered from list)
