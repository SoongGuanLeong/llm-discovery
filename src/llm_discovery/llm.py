import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from .evaluation import ModelEvaluation, ModelEvaluationRequest
from .evidence import EvidencePacket
from .judge_transport import JudgeTransport
from .json_repair import extract_and_validate


SYSTEM_PROMPT = """\\
You evaluate LLMs for a model discovery system.

Your task is to identify the provider model, determine whether it is
suitable for coding, identify its Artificial Analysis equivalent when
possible, and decide whether it should be kept.

Hard requirements:
- coding must be true to be eligible for "keep"
- you must always return either "keep" or "drop"
- do not invent benchmark results, model identities, AA matches, or scores
- if reliable Artificial Analysis candidates are provided, choose from those
  candidates when a candidate clearly matches the provider model
- when AA candidates are provided, aa_model_id must be exactly one of the
  provided candidate IDs or null
- never return an AA slug, name, or other identifier as aa_model_id
- if no direct AA candidate matches, you may identify a parent/base model only
  when the relationship is clearly documented
- a parent model does not automatically make the provider model equivalent
  to the parent model
- Artificial Analysis Intelligence Index is the primary capability reference
  when an AA score is available
- other coding benchmarks such as HumanEval, SWE-bench, LiveCodeBench,
  Terminal-Bench, and Codeforces are supporting evidence only
- pricing (blended $ per 1M tokens from Artificial Analysis) is provided when available and influences flash vs max: cheap high-quality models favor flash (intelligence per dollar)
- never convert another benchmark's score into an Artificial Analysis score
- a parent-model AA score may be used only as an inferred reference when
  the provider model is clearly derived from that parent and coding evidence
  supports the relationship
- if a direct or parent-model AA score is below the configured minimum,
  decision must be "drop"
- if no AA score is available, use coding benchmarks only as supporting
  evidence; do not assign or estimate an AA score
- without a verified AA score, keep is allowed only when strong,
  model-specific coding evidence justifies the decision
- provider-native or proprietary general-purpose models or systems may be kept
  without an AA score ONLY when ALL hold: (1) provider-native (provider organisation matches model creator, not a third-party re-host), (2) first-party URL domain matches provider (e.g., openai.com, mistral.ai, seed.bytedance.com, bytedance.com), (3) doc snippet explicitly mentions coding, code generation, software engineering, or programming and lists >=2 programming languages or states agentic coding, (4) evidence includes the source URL as (source: <url>). No URL -> unverified -> exception does not apply; do not hallucinate URL
- an agentic system may use documented underlying models as supporting evidence,
  but must never inherit or claim their AA score
- this provider-native exception does not apply to compound systems,
  tool wrappers, safety models, speech/audio models, embedding, vision, reranker, or other specialized models
- models explicitly labeled mini, small, lite, nano, or similar variants should
  be dropped unless they have a verified AA score meeting the minimum threshold
  and reliable coding evidence; provider claim alone never lifts mini/small/lite/nano to moderate
- tool use, code execution, or general reasoning alone is not sufficient
  evidence of coding capability
- triangulation: for no-AA / claim-only models, verify via web search before lifting above weak; unverified claim stays weak
- the Python pipeline will verify any AA model ID and score against
  its local Artificial Analysis catalog
- use search_web when necessary for no-AA / claim-only models
- perform at most 2 web searches. Use queries in order: (1) "{model_id} model card coding programming languages" (add site:<provider-domain> when known), (2) "{model_id} coding benchmark HumanEval SWE-bench LiveCodeBench"
- record every source URL in evidence as (source: <url>) and, if a numeric benchmark is found, also in raw_benchmarks with the same URL; claim-only (no number) goes only in evidence, never invent URL
- after gathering enough evidence, make a final keep/drop decision
- when evidence is insufficient, choose "drop"
- evidence must contain at most 2 short items
- each evidence item must be one short sentence
- keep the final JSON response under 200 tokens
- evidence_level calibration (use same scale as deterministic pipeline):
  - strong: AA Intelligence >= 55 alone, or AA >= 45 plus at least one coding benchmark (SWE-bench Verified >= 40, Terminal-Bench >= 50, LiveCodeBench, HumanEval), or coding_score >= 45, or 2+ positive coding benchmarks, or any coding benchmark >= 50
  - moderate: AA >= 24 alone, or any single coding benchmark >= 30, or provider claim verified via first-party URL with >=2 programming languages listed (source: <url> required) — unverified claim stays weak; mini/small/lite/nano never moderate via claim alone
  - weak: claim only, no AA, no benchmark >= 30, or unverified claim without source URL
  - none: no evidence at all
  Prefer strong when AA + coding benchmark both present; prefer moderate when only AA or only one benchmark present. Claim-only never reaches strong without a benchmark number

You have access to this tool:

search_web(query)
    Search the web for information about the model.

After gathering enough evidence, return only a JSON object with:
{
  "canonical_name": string or null,
  "coding": boolean,
  "aa_relevance": "strong"|"moderate"|"weak"|"none",
  "confidence": number between 0 and 1,
  "decision": "keep" or "drop",
  "evidence_level": "strong"|"moderate"|"weak"|"none",
  "evidence": [strings]
}
"""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for information about an LLM, including model identity, capabilities, and benchmarks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The web search query."
                    }
                },
                "required": ["query"],
            },
        },
    }
]


class LocalLLMEvaluator:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        min_score: float,
        search_web: Callable[[str], list[dict[str, Any]]],
        max_searches: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.min_score = min_score
        self.search_web = search_web
        self.max_searches = max_searches
        self.transport = JudgeTransport(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
        )

    def evaluate(
        self,
        request: ModelEvaluationRequest,
        evidence_packet: EvidencePacket | None = None,
    ) -> ModelEvaluation:
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": self._build_prompt(request, evidence_packet),
            },
        ]

        search_count = 0
        max_iterations = self.max_searches + 4
        disable_next = False

        for _ in range(max_iterations):
            for attempt in range(3):
                response = self._post(messages, disable_tools=disable_next)
                if response.status_code not in (429, 503):
                    break
                retry_after = response.headers.get("retry-after")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 10 * (2 ** attempt)
                if attempt < 2:
                    time.sleep(min(wait, 60))
            else:
                raise RuntimeError(f"Judge transport failed after retries: HTTP {response.status_code}")
            # reset after use
            disable_next = False

            response.raise_for_status()

            message = response.json()["choices"][0]["message"]
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                raw_content = message.get("content") or ""
                try:
                    return extract_and_validate(raw_content)
                except ValueError:
                    messages.append(message)
                    messages.append({
                        "role": "user",
                        "content": (
                            "Return ONLY a JSON object with exactly these keys: "
                            'canonical_name, coding (bool), aa_relevance (strong|moderate|weak|none), evidence_level (strong|moderate|weak|none), '
                            'confidence (0-1 float), decision ("keep"|"drop"), '
                            'evidence (list of at most 2 short strings). '
                            "No prose, no markdown fences."
                        ),
                    })
                    disable_next = True
                    continue
                except Exception:
                    messages.append(message)
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON. "
                            "Return ONLY a JSON object with exactly these keys: "
                            'canonical_name, coding (bool), aa_relevance (strong|moderate|weak|none), evidence_level (strong|moderate|weak|none), '
                            'confidence (0-1 float), decision ("keep"|"drop"), '
                            'evidence (list of at most 2 short strings). '
                            "Do not include reasoning, prose, or markdown fences."
                        ),
                    })
                    disable_next = True
                    continue

            messages.append(message)

            for tool_call in tool_calls:
                result = self._execute_tool(tool_call)
                search_count += 1

                result = [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("snippet", "")[:800],
                    }
                    for item in result[:3]
                ]

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result),
                    }
                )

            if search_count >= self.max_searches:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have reached the web search limit. "
                            "Do not call any more tools. "
                            "Return the final JSON decision now."
                        ),
                    }
                )

        raise RuntimeError("LLM failed to return a final evaluation: invalid JSON after retries")

    def _post(
        self,
        messages: list[dict[str, Any]],
        disable_tools: bool = False,
    ) -> httpx.Response:
        """Delegate to JudgeTransport. Retry/backoff lives in transport."""
        return self.transport.post_chat(messages, disable_tools=disable_tools)

    def _execute_tool(
        self,
        tool_call: dict[str, Any],
    ) -> list[dict[str, Any]]:
        function = tool_call["function"]

        if function["name"] != "search_web":
            raise ValueError(f"Unknown tool: {function['name']}")

        arguments = json.loads(function["arguments"])

        return self.search_web(arguments["query"])

    def _build_prompt(
        self,
        request: ModelEvaluationRequest,
        evidence_packet: EvidencePacket | None = None,
    ) -> str:
        evidence_summary = {}
        if evidence_packet:
            evidence_summary = evidence_packet.evidence_summary()
            polarity_info = {}
            for bench in evidence_packet.benchmarks:
                polarity_info[bench.name] = {
                    "value": bench.value,
                    "polarity": bench.polarity.value,
                    "category": bench.category.value,
                }
            evidence_summary["polarity"] = polarity_info

        payload = {
            "provider": request.provider,
            "model_id": request.model_id,
            "provider_metadata": request.provider_metadata,
            "artificial_analysis": request.aa_match,
            "benchmarks": request.benchmarks,
            "pricing": getattr(request, "pricing", None),
            "evidence": evidence_summary,
            "minimum_aa_intelligence_index": self.min_score,
        }

        return json.dumps(payload, indent=2)