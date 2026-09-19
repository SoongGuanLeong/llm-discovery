"""Allowed differences registry (#272, ADR 0010 #11).

Governing rule: every difference between old and new behaviour is either a
preserved equivalence or an entry here. The harness asserts
``computed_diffs - ALLOWED == empty``. Each entry's ``adr`` anchor must
resolve to a heading in ``docs/adr/0010-cli-replaces-ui-parity-contract.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ADR_PATH = Path(__file__).parents[2] / "docs" / "adr" / "0010-cli-replaces-ui-parity-contract.md"


@dataclass(frozen=True)
class AllowedChange:
    command: str  # "discover" | "build" | "refresh" | "export.dry-run" | "export.apply" | "config.set-key" | "providers.list" | "doctor" | "*"
    aspect: str  # "stdout" | "stderr" | "exit_code" | "flag" | "artifact" | "env_var"
    old: str
    new: str
    adr: str  # anchor, e.g. "ADR-0010#1-flag-deltas"


ALLOWED: tuple[AllowedChange, ...] = (
    # #1 flag deltas
    AllowedChange("discover", "flag", "--all-providers", "no provider means all configured", "ADR-0010#1-flag-deltas"),
    AllowedChange("discover", "flag", "<provider> defaults to first configured", "no provider means all configured", "ADR-0010#1-flag-deltas"),
    AllowedChange("discover", "flag", "discover --all with no provider runs all", "discover --all with no provider is usage error (exit 2)", "ADR-0010#1-flag-deltas"),
    AllowedChange("build", "flag", "hidden positional providers_pos", "dropped; --providers only", "ADR-0010#1-flag-deltas"),
    AllowedChange("build", "flag", "--all-providers", "dropped as redundant", "ADR-0010#1-flag-deltas"),
    AllowedChange("build", "flag", "--max-workers alias", "dropped; --workers survives", "ADR-0010#1-flag-deltas"),
    AllowedChange("refresh", "flag", "--aa-api-key argv secret", "dropped; AA_API_KEY env only", "ADR-0010#1-flag-deltas"),
    AllowedChange("export.dry-run", "flag", "--dry-run / --check / --apply trio, --check alias", "dry-run|apply subcommands; --check dropped", "ADR-0010#1-flag-deltas"),
    AllowedChange("export.dry-run", "flag", "--omniroute-url", "renamed --gateway-url", "ADR-0010#1-flag-deltas"),
    AllowedChange("export.apply", "flag", "--omniroute-url", "renamed --gateway-url", "ADR-0010#1-flag-deltas"),
    AllowedChange("export.dry-run", "flag", "no mode succeeds silently (exit 0)", "explicit dry-run|apply required, else exit 2 usage", "ADR-0010#1-flag-deltas"),
    # #2 streaming and cancellation
    AllowedChange("*", "stderr", "SSE events {type, line, ts} plus terminal done/killed", "progress lines on stderr plus one envelope on stdout", "ADR-0010#2-streaming-and-cancellation"),
    AllowedChange("*", "stderr", "per-line ts timestamp", "no timestamp; stderr is human text", "ADR-0010#2-streaming-and-cancellation"),
    AllowedChange("*", "exit_code", "cancel endpoint SIGTERM, 2s poll, SIGKILL, {status:killed}", "one SIGINT yields interrupted envelope, exit 130", "ADR-0010#2-streaming-and-cancellation"),
    AllowedChange("build", "exit_code", "409 job already running", "dropped; single-process CLI", "ADR-0010#2-streaming-and-cancellation"),
    # #3 exit-code remap (old catch-all 1 split per #268 taxonomy)
    AllowedChange("discover", "exit_code", "unknown provider exits 1", "exits 2 usage", "ADR-0010#3-exit-code-remap"),
    AllowedChange("discover", "exit_code", "no provider configured exits 1", "exits 3 prerequisite", "ADR-0010#3-exit-code-remap"),
    AllowedChange("*", "exit_code", "missing or invalid config/providers.yaml exits 1", "exits 3 prerequisite", "ADR-0010#3-exit-code-remap"),
    AllowedChange("*", "exit_code", "missing catalog for query exits 1", "exits 3 prerequisite", "ADR-0010#3-exit-code-remap"),
    AllowedChange("export.apply", "exit_code", "gateway unreachable exits 1", "exits 4 pipeline", "ADR-0010#3-exit-code-remap"),
    AllowedChange("build", "exit_code", "pipeline exception exits 1", "exits 4 pipeline", "ADR-0010#3-exit-code-remap"),
    AllowedChange("*", "exit_code", "httpx unavailable exits 2", "exits 3 prerequisite", "ADR-0010#3-exit-code-remap"),
    AllowedChange("*", "exit_code", "UI 400 bad key body", "exit 2 usage", "ADR-0010#3-exit-code-remap"),
    # #4 secret entry
    AllowedChange("config.set-key", "env_var", ".env chmod 600 only when file absent", ".env chmod 600 always", "ADR-0010#4-secret-entry"),
    AllowedChange("config.set-key", "stdout", "hint masks last 3-4 chars", "hint masks last 4 chars", "ADR-0010#4-secret-entry"),
    AllowedChange("config.set-key", "flag", "key via HTTP body", "key via stdin only, no argv flag", "ADR-0010#4-secret-entry"),
    # #5 provider listing shape
    AllowedChange("providers.list", "stdout", "GET /api/providers returns sorted names only", "providers list returns rich objects; name set must match", "ADR-0010#5-provider-listing-shape"),
    # #6 entrypoint surface
    AllowedChange("*", "stdout", "module main() per script plus ui server", "one surface: llm-discovery plus python -m llm_discovery", "ADR-0010#6-entrypoint-surface"),
)


def _adr_headings() -> set[str]:
    text = _ADR_PATH.read_text(encoding="utf-8")
    import re

    headings: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line.strip())
        if not m:
            continue
        title = m.group(1).strip()
        slug = re.sub(r"[^a-z0-9 _-]", "", title.lower())
        slug = slug.replace(" ", "-").replace("_", "-")
        slug = re.sub(r"-+", "-", slug)
        headings.add(slug)
    return headings


def validate_anchors() -> list[str]:
    """Return adr anchors that do not resolve to an ADR heading."""
    headings = _adr_headings()
    bad: list[str] = []
    for entry in ALLOWED:
        anchor = entry.adr.split("#", 1)[1] if "#" in entry.adr else entry.adr
        if anchor not in headings:
            bad.append(entry.adr)
    return bad


def is_allowed(command: str, aspect: str) -> bool:
    return any(
        (e.command == command or e.command == "*") and e.aspect == aspect
        for e in ALLOWED
    )
