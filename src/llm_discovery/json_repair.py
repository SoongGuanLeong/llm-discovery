import json
import re

from .evaluation import ModelEvaluation


def extract_json(content: str) -> str:
    content = content.strip()
    if not content:
        return content
    # 1. Fenced block anywhere: ``` + optional json + captured inner + ```
    fence_re = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")
    m = fence_re.search(content)
    if m:
        inner = m.group(1).strip()
        if inner:
            return inner
    # 2. Extract JSON object via raw_decode from first {
    decoder = json.JSONDecoder()
    idx = content.find("{")
    if idx != -1:
        try:
            _, end = decoder.raw_decode(content, idx)
            return content[idx:end].strip()
        except json.JSONDecodeError:
            pass
    return content.strip()


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
