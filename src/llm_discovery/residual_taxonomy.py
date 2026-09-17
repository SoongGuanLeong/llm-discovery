"""Alias for residual classifier (issue #238).

Re-export from residual_classifier for callers expecting residual_taxonomy.
"""
from .residual_classifier import TAXONOMY, classify_residual, classify_residual_uncertain

__all__ = ["classify_residual_uncertain", "classify_residual", "TAXONOMY"]
