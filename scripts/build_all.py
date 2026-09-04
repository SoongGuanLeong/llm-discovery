#!/usr/bin/env python3
"""Build all providers into store — shim for CLI parity with discover.py.

Usage:
  .venv/bin/python scripts/build_all.py --workers 4
  .venv/bin/python scripts/build_all.py --providers kilo_ai groq --workers 8
  .venv/bin/python -m llm_discovery.build_all --workers 4 --providers kilo_ai
"""
from llm_discovery.build_all import main
if __name__ == "__main__":
    main()
