"""Parse a skill .md file into a structured dict.

Format: YAML frontmatter (name, description, when_to_use, optional fields:
with checks/scores/claims sub-keys) + a prose body. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

import yaml

REQUIRED_KEYS = ("name", "description", "when_to_use")


class SkillParseError(Exception):
    pass


def parse_skill_md(text: str) -> dict:
    """Returns {"name", "description", "when_to_use", "fields", "body"}.

    "fields" is the raw {"checks": [...], "scores": [...], "claims": [...]}
    dict (any subset, any absent) or None if the skill declares no fields at
    all (narrative-only output)."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise SkillParseError("no YAML frontmatter found (must start with '---')")

    rest = stripped[3:]
    end = rest.find("\n---")
    if end == -1:
        raise SkillParseError("no YAML frontmatter found (missing closing '---')")

    raw_frontmatter = rest[:end]
    body = rest[end + 4 :].lstrip("\n")

    try:
        frontmatter = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as e:
        raise SkillParseError(f"invalid YAML frontmatter: {e}") from e

    if not isinstance(frontmatter, dict):
        raise SkillParseError("frontmatter must be a YAML mapping")

    for key in REQUIRED_KEYS:
        if key not in frontmatter:
            raise SkillParseError(f"missing required frontmatter key: {key!r}")

    return {
        "name": frontmatter["name"],
        "description": frontmatter["description"],
        "when_to_use": frontmatter["when_to_use"],
        "fields": frontmatter.get("fields"),
        "body": body,
    }
