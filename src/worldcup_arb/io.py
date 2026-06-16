from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from .model import Event, events_from_payload


def load_events(path: str | Path) -> tuple[Event, ...]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return events_from_payload(payload)


def dump_json(path: str | Path, payload: Any) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        write_json(handle, payload)


def write_json(handle: TextIO, payload: Any) -> None:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
