import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def save_result(
    result: dict[str, Any],
    provider: str,
    output_dir: Path = Path("data/discovery"),
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).astimezone()
    filename = timestamp.strftime("%Y-%m-%dT%H-%M-%S%z") + ".json"

    payload = {
        "provider": provider,
        "evaluated_at": timestamp.isoformat(),
        **result,
    }

    path = output_dir / filename
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    return path
