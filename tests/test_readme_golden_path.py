"""#274/#300: README regression harness for the first-look spec.

Fails when README drifts from cli._build_parser, when the promoted
guide is missing/unlinked, when AGENTS.md hides the CLI, when
retired ui/scripts spellings creep back into README, or when the
#300 split regresses (badge row, Catalog Path placement, ADR copy).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
AGENTS = REPO / "AGENTS.md"
GUIDE = REPO / "docs" / "omni-infi-guide.md"
OLD_GUIDE = REPO / "docs" / "research" / "issue-181-omni-infi-guide.md"

REQUIRED_STEPS = [
    "llm-discovery doctor",
    "llm-discovery config set-key OMNIROUTE_API_KEY",
    "llm-discovery providers list",
    "llm-discovery discover",
    "llm-discovery build",
    "llm-discovery export dry-run",
    "llm-discovery export apply",
]

# #300: the Catalog Path — keyless first command, before the Golden Path.
CATALOG_STEPS = [
    "llm-discovery refresh --only models_dev",
    "llm-discovery catalog models show <model-id>",
    "llm-discovery catalog providers show <provider-id>",
    "llm-discovery catalog providers models <provider-id>",
]

# Retired surfaces that must not reappear as live instructions in README.
STALE = [
    "python -m ui",
    "from ui",
    "import ui",
    "scripts/discover.py",
    "scripts/build_all.py",
    "scripts/query.py",
    "scripts/refresh_catalogs.py",
    "python -m llm_discovery.cli",
    "python -m llm_discovery.omniroute_export",
    "python -m llm_discovery.refresh",
    "python -m llm_discovery.discover",
]


def _readme() -> str:
    assert README.exists(), "README.md missing"
    return README.read_text(encoding="utf-8")


def test_golden_path_section_lists_steps_in_order():
    text = _readme()
    assert "## 5-minute quickstart" in text
    positions = []
    for step in REQUIRED_STEPS:
        assert step in text, f"README missing golden step: {step}"
        positions.append(text.index(step))
    assert positions == sorted(positions), "golden steps out of order"


def test_golden_commands_parse_against_cli():
    from llm_discovery.cli import _build_parser

    parser = _build_parser()
    text = _readme()
    for step in REQUIRED_STEPS:
        argv = step.split()[1:]  # drop binary name
        # set-key needs a value source in real use; parse only here.
        args = parser.parse_args(argv)
        assert getattr(args, "handler", None) is not None, f"README step does not parse: {step}"
    # Spot-check flags README advertises.
    args = parser.parse_args(["discover", "--workers", "4"])
    assert args.workers == 4
    args = parser.parse_args(["export", "dry-run"])
    assert args.mode == "dry-run"


def test_guide_promoted_and_linked():
    assert GUIDE.exists(), "promoted guide docs/omni-infi-guide.md missing"
    assert not OLD_GUIDE.exists(), "guide still lives under docs/research/"
    text = _readme()
    assert "docs/omni-infi-guide.md" in text
    guide = GUIDE.read_text(encoding="utf-8")
    assert "http://localhost:20128" in guide
    assert "OMNIROUTE_API_KEY" in guide
    assert "LLM_SHARED_PROJECT_ID" in guide


def test_agents_points_at_cli():
    text = AGENTS.read_text(encoding="utf-8")
    assert "llm-discovery" in text
    assert "doctor" in text


def test_no_stale_surfaces_in_readme():
    text = _readme()
    for stale in STALE:
        for i, line in enumerate(text.splitlines(), 1):
            if stale not in line:
                continue
            guard = line.lower()
            allowed = any(
                k in guard for k in ("retir", "delet", "remov", "do not", "don't", "never", "no ")
            )
            assert allowed, f"stale spelling in README line {i}: {line.strip()}"


def test_badge_row_is_exactly_three_badges():
    """#300: exactly three badges (CI, MIT, Python 3.12) directly under the title."""
    lines = _readme().splitlines()
    assert lines[0].strip() == "# llm-discovery"
    i = 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    badges = []
    while i < len(lines) and lines[i].startswith("[!["):
        badges.append(lines[i])
        i += 1
    assert len(badges) == 3, f"expected exactly 3 badge lines, got {len(badges)}: {badges}"
    assert "actions/workflows/ci.yml/badge.svg" in badges[0], "badge 1 must be CI"
    assert "MIT" in badges[1] and "shields.io" in badges[1], "badge 2 must be the MIT licence"
    assert "Python" in badges[2] and "3.12" in badges[2], "badge 3 must be Python 3.12"


def test_catalog_path_precedes_golden_path_and_parses():
    """#300: the Catalog Path block appears before the Golden Path and parses."""
    from llm_discovery.cli import _build_parser

    parser = _build_parser()
    text = _readme()
    assert "## Catalog quickstart (no API key)" in text, "README missing the catalog quickstart"
    start = text.index("## Catalog quickstart (no API key)")
    end = text.index("## 5-minute quickstart")
    assert start < end, "catalog quickstart must come before the 5-minute quickstart"
    for step in CATALOG_STEPS:
        assert step in text, f"README missing Catalog Path command: {step}"
        pos = text.index(step)
        assert start < pos < end, f"Catalog Path command outside the section: {step}"
        argv = [re.sub(r"^<(.+)>$", r"\1", a) for a in step.split()[1:]]
        args = parser.parse_args(argv)
        assert getattr(args, "handler", None) is not None, f"command does not parse: {step}"
    # #300: the false fresh-clone offline claim must not come back.
    assert "No network, no key" not in text


def test_interface_decisions_recorded():
    """#300: ADR 0010 is the normative record; the README no longer copies it."""
    adr = REPO / "docs" / "adr" / "0010-cli-replaces-ui-parity-contract.md"
    assert adr.exists(), "ADR 0010 must stay as the normative interface record"
    text = _readme()
    # The README links docs/reference/cli.md instead of re-recording the ADR;
    # test_no_stale_surfaces_in_readme already guards resurrected-UI spellings.
    assert "0010" not in text, "README must not carry a second copy of the interface decisions"
    assert "docs/reference/cli.md" in text
