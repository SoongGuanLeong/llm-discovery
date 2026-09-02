#!/usr/bin/env python3
"""One-command catalog refresh: AA + models.dev + benchmarks.

Usage:
    python scripts/refresh_catalogs.py          # refresh all 3 JSONs
    python scripts/refresh_catalogs.py --dry-run
    python scripts/refresh_catalogs.py --only aa models_dev
    ARTIFICIAL_ANALYSIS_API_KEY=xxx python scripts/refresh_catalogs.py

Also available as:
    python -m llm_discovery.refresh
    llm-discovery refresh  (if installed)
"""
from llm_discovery.refresh import main

if __name__ == "__main__":
    main()
