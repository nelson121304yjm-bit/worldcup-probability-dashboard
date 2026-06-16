"""Update metadata for the bundled static match snapshot.

This is intentionally conservative: it does not scrape websites or bypass access
controls. It validates the existing JavaScript data file and can stamp a manual
refresh note after you regenerate the source data.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "web" / "data" / "matches.js"
ASSIGNMENT_RE = re.compile(r"^\s*window\.WORLD_CUP_MATCHES\s*=\s*(\{.*\});?\s*$", re.S)


def load_payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = ASSIGNMENT_RE.match(text)
    if not match:
        raise ValueError(f"{path} does not look like window.WORLD_CUP_MATCHES = {{...}}")
    return json.loads(match.group(1))


def write_payload(path: Path, payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"window.WORLD_CUP_MATCHES = {rendered};\n", encoding="utf-8")


def validate_payload(payload: dict) -> None:
    matches = payload.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("payload must include a non-empty matches list")

    required = {"id", "status", "home", "away", "kickoff"}
    for index, item in enumerate(matches):
        missing = sorted(required - set(item))
        if missing:
            raise ValueError(f"matches[{index}] missing fields: {', '.join(missing)}")


def stamp_payload(payload: dict, note: str) -> dict:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M CST")
    payload["lastUpdated"] = f"{now}（{note}）"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or stamp the bundled dashboard data snapshot.")
    parser.add_argument("--file", type=Path, default=DATA_FILE, help="Path to web/data/matches.js")
    parser.add_argument("--stamp", action="store_true", help="Update lastUpdated after manual data refresh")
    parser.add_argument("--note", default="manual refresh", help="Text appended to lastUpdated when --stamp is used")
    args = parser.parse_args()

    payload = load_payload(args.file)
    validate_payload(payload)

    if args.stamp:
        write_payload(args.file, stamp_payload(payload, args.note))

    print(
        json.dumps(
            {
                "file": str(args.file),
                "sourceName": payload.get("sourceName"),
                "lastUpdated": payload.get("lastUpdated"),
                "matches": len(payload.get("matches", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
