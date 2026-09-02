import json
import re

from .evaluation import ModelEvaluation


def extract_json(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


_extract_json = extract_json


def repair_json(content: str) -> str:
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(content):
        ch = content[i]
        if in_string:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch in "\n\r":
                out.append("\\n" if ch == "\n" else "\\r")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
                out.append(ch)
            else:
                out.append(ch)
        i += 1
    repaired = "".join(out)
    repaired = re.sub(r",\s*([\}\]])", r"\1", repaired)
    return repaired


_repair_json = repair_json


def extract_and_validate(text: str) -> ModelEvaluation:
    """Extract JSON from possibly-fenced text, repair, and validate."""
    extracted = extract_json(text)
    if not extracted:
        raise ValueError("Empty content after JSON extraction")
    data: dict | None = None
    try:
        data = json.loads(extracted)
    except json.JSONDecodeError:
        repaired = repair_json(extracted)
        if repaired != extracted:
            data = json.loads(repaired)
        else:
            raise
    return ModelEvaluation.model_validate(data)
