"""The skills work in any harness (agents/README.md): one copy in .agents/skills (Pi reads it), a link
in .claude/skills (Claude Code), and no tool of one harness named in a skill's text."""

import re

import yaml

from tests.conftest import REAL_REPO

SHARED = REAL_REPO / ".agents" / "skills"
CLAUDE = REAL_REPO / ".claude" / "skills"
# The Linear MCP server's tools, its arguments, and the prefix of a harness's tool names: what the skills named before
# A skill names the action ("read the issue with its relations"), done with .github/linear.py or the harness's
# own Linear connection.
HARNESS_TOOL = re.compile(r"\b(list_issues|get_issue|save_issue|save_comment|save_project|includeRelations|mcp__\w+)\b")


def test_every_skill_has_one_copy_and_a_link_for_claude_code():
    shared = sorted(p.name for p in SHARED.iterdir() if (p / "SKILL.md").is_file())
    assert {"implement", "issue", "triage"} <= set(shared), "a product adds its own skills beside these"
    for entry in CLAUDE.iterdir():
        assert entry.is_symlink(), f".claude/skills/{entry.name} is a copy: make it a link to .agents/skills/{entry.name}"
        assert entry.readlink().as_posix() == f"../../.agents/skills/{entry.name}"
    assert sorted(p.name for p in CLAUDE.iterdir()) == shared, "each skill is linked for Claude Code"


def test_no_skill_names_a_tool_of_one_harness():
    for skill in SHARED.glob("*/SKILL.md"):
        text = skill.read_text()
        assert not HARNESS_TOOL.findall(text), f"{skill.parent.name} names {HARNESS_TOOL.findall(text)}"
        assert "in Claude Code" not in text, f"{skill.parent.name} is written for one harness"


def test_the_implement_skill_cites_sections_that_agents_readme_has():
    """The skill holds no rule of its own: it sends the run to agents/README.md's sections (with no key, "What can start
    now"). Each section it cites is a heading there, so a renamed heading fails here, and a reworded skill does not."""
    text = " ".join((SHARED / "implement" / "SKILL.md").read_text().split())  # a citation can wrap over two lines
    cited = set(re.findall(r'agents/README\.md "([^"]+)"', text))
    readme = (REAL_REPO / "agents" / "README.md").read_text()
    headings = set(re.findall(r"^#+ (.+)$", readme, re.MULTILINE))
    assert cited and cited <= headings, cited - headings


# a row of AGENTS.md's table of the teams: the team's key, then its repository
TEAM_ROW = re.compile(r"^\| ([A-Z]+) \| ([\w.-]+/[\w.-]+) \|", re.MULTILINE)


def teams_section() -> str:
    """The product's AGENTS.md "The teams": the table of the teams (agents/README.md "The teams" holds the rules)."""
    return (REAL_REPO / "AGENTS.md").read_text().split("\n## The teams\n", 1)[1].split("\n## ", 1)[0]


def teams() -> dict[str, str]:
    """AGENTS.md "The teams": each Linear team's key and its repository, from the table's rows."""
    return dict(TEAM_ROW.findall(teams_section()))


def test_the_teams_table_names_each_team_and_its_repository():
    rows = TEAM_ROW.findall(teams_section())
    assert rows, "the table has a row for each team"
    assert len(teams()) == len(rows), "each team's key is in the table once"
    assert all(len(key) in (3, 4) for key in teams()), "a team's key is three or four capital letters"


def test_no_skill_or_brief_names_one_team_as_the_only_one():
    """A team per repository. A skill or a brief that says `list ABC` reads one team and misses the other's
    issues. They name TEAM and point to "The teams"; an issue key such as `ABC-12` (a team's key and a number) is an
    example or a reference, not a team."""
    bare = re.compile(r"\b(" + "|".join(teams()) + r")\b(?!-\d)")
    files = [*SHARED.glob("*/SKILL.md"), *(REAL_REPO / "agents").glob("*.md"), REAL_REPO / "AGENTS.md"]
    for path in files:
        found = [line for line in path.read_text().splitlines() if bare.search(line) and not TEAM_ROW.match(line)]
        assert not found, f"{path.relative_to(REAL_REPO)} names a team as the only one: {found[:3]}"


def test_every_skills_frontmatter_is_valid_yaml_with_its_name_and_a_description():
    """Claude Code reads a frontmatter leniently, a strict reader (pi) refuses it: a plain `description` that holds
    `: ` is a nested mapping in YAML. So each frontmatter must load with a strict parser."""
    skills = sorted(SHARED.glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        text = skill.read_text()
        assert text.startswith("---\n"), f"{skill.parent.name}: no frontmatter"
        head = text.split("\n---\n", 1)[0].removeprefix("---\n")
        meta = yaml.safe_load(head)
        assert isinstance(meta, dict), f"{skill.parent.name}: the frontmatter is not a mapping"
        assert meta.get("name") == skill.parent.name, f"{skill.parent.name}: name {meta.get('name')!r}"
        assert isinstance(meta.get("description"), str) and meta["description"].strip(), f"{skill.parent.name}: no description"


def test_the_triage_and_the_skills_read_each_teams_own_pull_requests():
    """The owner's skills and the triage run from the product's checkout for every team, so each
    `gh pr` command names the team's repository; without `-R REPO`, gh reads the current folder's pull requests."""
    files = [*SHARED.glob("*/SKILL.md"), REAL_REPO / "agents" / "planner.md"]
    for path in files:
        text = " ".join(path.read_text().split())  # a command can wrap over two lines
        for command in re.findall(r"`gh pr (?:list|view) [^`]+`", text):
            assert "-R REPO" in command, f"{path.relative_to(REAL_REPO)}: {command} reads the current folder's repository"
