import ast
import json
import re

from .evaluation import ModelEvaluation


def _strip_think_tags(content: str) -> str:
    # Remove <think>...</think>, <reasoning>...</reasoning> wrappers used by reasoning models
    content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE)
    content = re.sub(r"<reasoning>[\s\S]*?</reasoning>", "", content, flags=re.IGNORECASE)
    # Also handle unclosed think tag at start (streaming truncation)
    content = re.sub(r"^\s*<think>\s*", "", content, flags=re.IGNORECASE)
    return content.strip()


def extract_json(content: str) -> str:
    content = _strip_think_tags(content.strip())
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
                # peek ahead: is this a true string terminator?
                j = i + 1
                while j < len(content) and content[j] in " \t\n\r":
                    j += 1
                nxt = content[j] if j < len(content) else ""
                # terminators: , ] } :  (end of value)
                if nxt in ",]}:" or nxt == "":
                    out.append(ch)
                    in_string = False
                else:
                    # inner unescaped quote -> escape it
                    out.append("\\")
                    out.append(ch)
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


def _normalize_quotes(content: str) -> str:
    # smart quotes -> straight double quotes
    return content.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'").replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")


def _try_single_quote_fix(content: str) -> str | None:
    if "'" not in content:
        return None
    # 1. try json after naive single->double quote replacement (handles true/false/null)
    try:
        # replace single quotes with double quotes only when safe - quick heuristic
        candidate = content.replace("'", '"')
        data = json.loads(candidate)
        if isinstance(data, dict):
            return json.dumps(data)
    except Exception:
        pass
    # 2. try ast.literal_eval after converting json literals to python
    try:
        py_content = content.replace("true", "True").replace("false", "False").replace("null", "None")
        data = ast.literal_eval(py_content)
        if isinstance(data, dict):
            return json.dumps(data)
    except Exception:
        pass
    return None


def _find_last_json_object(text: str) -> str | None:
    """For reasoning models that emit chain-of-thought + final JSON, prefer last object."""
    decoder = json.JSONDecoder()
    last: str | None = None
    idx = 0
    while True:
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            _, end = decoder.raw_decode(text, start)
            last = text[start:end]
            idx = end
        except json.JSONDecodeError:
            idx = start + 1
    return last


def extract_and_validate(text: str) -> ModelEvaluation:
    """Extract JSON from possibly-fenced text, repair, and validate."""
    # Reasoning models often put JSON at end of chain-of-thought; try last object first
    last_obj = _find_last_json_object(_strip_think_tags(text))
    if last_obj:
        # Prefer last object if it looks like evaluation (has decision field)
        if "\"decision\"" in last_obj or "'decision'" in last_obj or "decision" in last_obj:
            text = last_obj
    extracted = extract_json(text)
    if not extracted:
        raise ValueError("Empty content after JSON extraction")
    # normalize smart quotes before any parsing
    extracted = _normalize_quotes(extracted)
    data: dict | None = None
    try:
        data = json.loads(extracted)
    except json.JSONDecodeError:
        # 1. try newline/trailing-comma repair
        repaired = repair_json(extracted)
        if repaired != extracted:
            try:
                data = json.loads(repaired)
            except json.JSONDecodeError:
                pass
        # 2. try single-quote / python literal fix
        if data is None:
            sq = _try_single_quote_fix(extracted)
            if sq is not None:
                try:
                    data = json.loads(sq)
                except json.JSONDecodeError:
                    pass
            if data is None and repaired != extracted:
                sq2 = _try_single_quote_fix(repaired)
                if sq2 is not None:
                    try:
                        data = json.loads(sq2)
                    except json.JSONDecodeError:
                        pass
        if data is None:
            raise
    return ModelEvaluation.model_validate(data)