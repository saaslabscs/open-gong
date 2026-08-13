"""Skill .md parser (Task 2 of the agent-skill architecture plan).
See docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

import pytest

from app.skills.loader import SkillParseError, parse_skill_md

FULL_SKILL = """---
name: sales-scorecard
description: Scores discovery quality and MEDDIC coverage on sales calls
when_to_use: The call is a sales conversation - discovery, demo, pricing, or negotiation
fields:
  checks:
    - budget_discussed
    - economic_buyer_identified
  scores:
    - name: discovery_quality
      max: 5
  claims:
    - summary
    - next_steps
---

Score this call against MEDDIC. For each check, cite the exact line.
"""

NARRATIVE_ONLY_SKILL = """---
name: plain-summary
description: Summarizes the call in plain prose
when_to_use: Always, as a fallback when no other skill applies
---

Write a short plain-English summary of this call.
"""


def test_parses_full_frontmatter_and_fields():
    result = parse_skill_md(FULL_SKILL)
    assert result["name"] == "sales-scorecard"
    assert result["description"] == "Scores discovery quality and MEDDIC coverage on sales calls"
    assert result["when_to_use"].startswith("The call is a sales conversation")
    assert result["fields"]["checks"] == ["budget_discussed", "economic_buyer_identified"]
    assert result["fields"]["scores"] == [{"name": "discovery_quality", "max": 5}]
    assert result["fields"]["claims"] == ["summary", "next_steps"]
    assert result["body"].strip().startswith("Score this call against MEDDIC")


def test_parses_narrative_only_skill_with_no_fields():
    result = parse_skill_md(NARRATIVE_ONLY_SKILL)
    assert result["name"] == "plain-summary"
    assert result["fields"] is None
    assert "plain-English summary" in result["body"]


def test_raises_when_frontmatter_missing():
    with pytest.raises(SkillParseError, match="no YAML frontmatter"):
        parse_skill_md("Just a body, no frontmatter at all.")


def test_raises_when_required_key_missing():
    bad = "---\nname: incomplete\n---\n\nBody text.\n"
    with pytest.raises(SkillParseError, match="missing required frontmatter key"):
        parse_skill_md(bad)


def test_raises_on_malformed_yaml():
    bad = "---\nname: [unterminated\n---\n\nBody.\n"
    with pytest.raises(SkillParseError, match="invalid YAML frontmatter"):
        parse_skill_md(bad)
