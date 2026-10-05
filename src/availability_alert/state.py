from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def load_state(path: str) -> Dict[str, Dict[str, Any]]:
    state_path = Path(path)
    if not state_path.exists():
        return {}

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            loaded: Dict[str, Dict[str, Any]] = {}
            for raw_url, raw_state in data.items():
                url = str(raw_url)

                # Backward compatibility: legacy format stored url -> bool.
                if isinstance(raw_state, bool):
                    loaded[url] = {
                        "available": raw_state,
                        "title": "",
                        "last_checked_at": "",
                    }
                    continue

                if isinstance(raw_state, dict):
                    loaded[url] = {
                        "available": bool(raw_state.get("available", False)),
                        "title": str(raw_state.get("title") or ""),
                        "last_checked_at": str(raw_state.get("last_checked_at") or ""),
                    }
            return loaded
    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_state(path: str, state: Dict[str, Dict[str, Any]]) -> None:
    state_path = Path(path)
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )
