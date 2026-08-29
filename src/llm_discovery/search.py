from typing import Any

import httpx


class TavilySearcher:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str) -> list[dict[str, Any]]:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": 3,
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()

        return [
            {
                "title": result["title"],
                "url": result["url"],
                "snippet": result.get("content", "")[:1000],
            }
            for result in data.get("results", [])
        ]
