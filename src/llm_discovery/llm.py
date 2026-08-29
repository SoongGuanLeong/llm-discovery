import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from .evaluation import ModelEvaluation, ModelEvaluationRequest

SYSTEM_PROMPT = """\
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
  without an AA score when reliable first-party documentation establishes
  coding or agentic coding capability
- an agentic system may use documented underlying models as supporting evidence,
  but must never inherit or claim their AA score
- this provider-native exception does not apply to compound systems,
  tool wrappers, safety models, speech/audio models, or other specialized models
- models explicitly labeled mini, small, lite, nano, or similar variants should
  be dropped unless they have a verified AA score meeting the minimum threshold
  and reliable coding evidence
- tool use, code execution, or general reasoning alone is not sufficient
  evidence of coding capability
- the Python pipeline will verify any AA model ID and score against
  its local Artificial Analysis catalog
- use search_web when necessary
- perform at most 2 web searches
- after gathering enough evidence, make a final keep/drop decision
- when evidence is insufficient, choose "drop"
- evidence must contain at most 2 short items
- each evidence item must be one short sentence
- keep the final JSON response under 200 tokens

You have access to this tool:

search_web(query)
    Search the web for information about the model.

After gathering enough evidence, return only a JSON object with:
{
  "canonical_name": string or null,
  "coding": boolean,
  "aa_model_id": string or null,
  "confidence": number between 0 and 1,
  "decision": "keep" or "drop",
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
                        "description": "The web search query.",
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

    def evaluate(
        self,
        request: ModelEvaluationRequest,
    ) -> ModelEvaluation:
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": self._build_prompt(request),
            },
        ]

        search_count = 0
        max_iterations = self.max_searches + 2

        for _ in range(max_iterations):
            for attempt in range(3):
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": TOOLS,
                        "temperature": 0,
                        "max_tokens": 800,
                        "tool_choice": "auto",
                    },
                    timeout=120,
                )

                if response.status_code != 429:
                    break

                if attempt < 2:
                    time.sleep(5 * (attempt + 1))

            response.raise_for_status()

            message = response.json()["choices"][0]["message"]
            print("\n--- LLM MESSAGE ---")
            print(json.dumps(message, indent=2))
            print("--- END MESSAGE ---\n")
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                content = self._extract_json(message.get("content") or "")

                if not content:
                    raise RuntimeError("LLM returned an empty final response")

                try:
                    data = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"LLM returned invalid JSON:\n\n{content}") from exc

                return ModelEvaluation.model_validate(data)

            messages.append(message)

            if search_count + len(tool_calls) > self.max_searches:
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
                continue

            for tool_call in tool_calls:
                result = self._execute_tool(tool_call)
                search_count += 1

                # Limit search output so each model evaluation stays within
                # the local LLM context window.
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

        raise RuntimeError("LLM failed to return a final evaluation")

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
    ) -> str:
        payload: dict[str, Any] = {
            "provider": request.provider,
            "model_id": request.model_id,
            "provider_metadata": request.provider_metadata,
            "aa_candidates": request.aa_candidates,
            "minimum_aa_intelligence_index": self.min_score,
        }

        return json.dumps(payload, indent=2)

    @staticmethod
    def _extract_json(content: str) -> str:
        content = content.strip()

        if content.startswith("```"):
            lines = content.splitlines()

            if lines[0].startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            content = "\n".join(lines).strip()

        return content
