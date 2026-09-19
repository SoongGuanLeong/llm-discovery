"""Deletable surface once parity is green (#272 gate for #273, ADR 0010 #8-10)."""
from __future__ import annotations

# Files importing ui.server directly (must die with ui/).
UI_COUPLED_TESTS: tuple[str, ...] = (
    "tests/test_186_key_providers.py",
    "tests/ui/test_187_sse_dryrun.py",
    "tests/ui/test_188_apply.py",
    "tests/ui/test_189_build.py",
    "tests/ui/test_190_open_guide.py",
)

# Remaining old surface removed in #273 per ADR 0010 #6, #9.
OLD_SURFACE: tuple[str, ...] = (
    "ui/server.py",
    "ui/static",
    "scripts/discover.py",
    "scripts/build_all.py",
    "scripts/refresh_catalogs.py",
    "scripts/query.py",
    "src/llm_discovery/omniroute_export.py",  # entrypoint main() moves fully into cli; module kept only if imported
    "tests/test_ui_scaffold.py",
    "tests/parity",
)

DELETABLE = UI_COUPLED_TESTS + OLD_SURFACE
