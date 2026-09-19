"""#274: README golden path stays walkable from --help only.

Fails when README drifts from cli._build_parser, when the promoted
guide is missing/unlinked, when AGENTS.md hides the CLI, or when
retired ui/scripts spellings creep back into README.
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
    assert "5-minute Golden Path" in text
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


def test_interface_decisions_recorded():
    text = _readme()
    assert "0010" in text or "ADR 0010" in text
    lowered = text.lower()
    assert "no http" in lowered or "no new http" in lowered or "cli is the contract" in lowered
    # Retired UI must read as retired, not as an option.
    assert re.search(r"ui/.*(retired|removed|deleted)", lowered), "README must state ui/ retired"
