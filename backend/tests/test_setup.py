"""M6 onboarding: env read/patch, readiness status, sandbox-mint fallbacks."""

import app.setup_env as se


def test_set_env_upserts_preserving_other_lines(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# header\nLLM_PROVIDER=anthropic\nPYAI_ADAPTER=mock\n")
    monkeypatch.setattr(se, "ENV_FILE", env)
    se.set_env({"PYAI_ADAPTER": "real", "PYAI_API_KEY": "pyai_abc"})
    text = env.read_text()
    assert "# header" in text
    assert "PYAI_ADAPTER=real" in text
    assert "PYAI_API_KEY=pyai_abc" in text
    assert "LLM_PROVIDER=anthropic" in text
    assert text.count("PYAI_ADAPTER=") == 1  # upsert, not duplicate


def test_status_mock_needs_only_llm_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=sk-or-x\nPYAI_ADAPTER=mock\n")
    monkeypatch.setattr(se, "ENV_FILE", env)
    for k in ("PYAI_API_KEY", "PYAI_ADAPTER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = se.status()
    assert s["llm"]["ok"] is True
    assert s["transcription"]["adapter"] == "mock"
    assert s["can_process_uploads"] is True  # mock + llm key is enough


def test_status_real_without_pyai_key_not_ready(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=sk-or-x\nPYAI_ADAPTER=real\n")
    monkeypatch.setattr(se, "ENV_FILE", env)
    for k in ("PYAI_API_KEY", "PYAI_ADAPTER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = se.status()
    assert s["transcription"]["ok"] is False
    assert s["can_process_uploads"] is False


def test_status_missing_llm_key_blocks_processing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=anthropic\nPYAI_ADAPTER=mock\n")
    monkeypatch.setattr(se, "ENV_FILE", env)
    for k in ("PYAI_API_KEY", "PYAI_ADAPTER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = se.status()
    assert s["llm"]["ok"] is False
    assert s["can_process_uploads"] is False


def test_mint_sandbox_quota_reached(monkeypatch):
    import httpx

    def fake_post(url, **kw):
        return httpx.Response(429, json={"detail": "limit"})
    monkeypatch.setattr(se.httpx, "post", fake_post)
    res = se.mint_sandbox_key()
    assert res["ok"] is False and "quota" in res["reason"]


def test_mint_sandbox_success(monkeypatch):
    import httpx

    def fake_post(url, **kw):
        return httpx.Response(201, json={"api_key": "pyai_test_xyz"})
    monkeypatch.setattr(se.httpx, "post", fake_post)
    res = se.mint_sandbox_key()
    assert res["ok"] is True and res["key"] == "pyai_test_xyz"
