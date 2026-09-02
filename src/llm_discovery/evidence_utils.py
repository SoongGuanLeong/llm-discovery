"""Shared evidence cleaning utilities.\n\nExtracted from results.py per T8. Single source for evidence noise\nremoval so SingleModelWriter and ProviderBatchWriter stay decoupled.\n"""
import re


def clean_evidence(evidence: list[str] | None) -> list[str]:
    """Remove free-model-rule noise and free-marker references from evidence."""
    if not evidence:
        return []
    cleaned: list[str] = []
    for ev in evidence:
        if "free-model-rule" in ev:
            continue
        ev = re.sub(r"(?::|-|_)free", "", ev)
        ev = re.sub(r"''''", "", ev)
        cleaned.append(ev)
    return cleaned


# Backward-compat alias for expand-contract period.
_clean_evidence = clean_evidence
