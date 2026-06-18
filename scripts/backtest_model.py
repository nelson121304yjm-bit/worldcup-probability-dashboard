"""Generate a conservative model backtest and calibration-readiness report."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "web" / "data" / "matches.js"
ASSIGNMENT_RE = re.compile(r"^\s*window\.WORLD_CUP_MATCHES\s*=\s*(\{.*\});?\s*$", re.S)
SHANGHAI = ZoneInfo("Asia/Shanghai")

CURRENT_TOTAL_GOALS_BASELINE = 2.78
CURRENT_SPORTTERY_SCORE_WEIGHT = 0.45
WORLD_CUP_DRAW_PRIOR = 0.27
SHRINKAGE_PRIOR_MATCHES = 40
MIN_FINISHED_FOR_SCORE_CALIBRATION = 30
MIN_MARKET_SNAPSHOTS = 20
MIN_SCORE_MARKET_SNAPSHOTS = 20


def load_payload(path: Path = DATA_FILE) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = ASSIGNMENT_RE.match(text)
    if not match:
        raise ValueError(f"{path} does not look like window.WORLD_CUP_MATCHES = {{...}}")
    return json.loads(match.group(1))


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    matches = [match for match in payload.get("matches") or [] if isinstance(match, dict)]
    finished = [match for match in matches if score_tuple(match) is not None and match.get("status") == "finished"]
    upcoming = [match for match in matches if match.get("status") != "finished"]

    scores = [score_tuple(match) for match in finished]
    scores = [score for score in scores if score is not None]
    total = len(scores)
    home_wins = sum(1 for home, away in scores if home > away)
    draws = sum(1 for home, away in scores if home == away)
    away_wins = sum(1 for home, away in scores if home < away)
    total_goals = [home + away for home, away in scores]
    home_goals = [home for home, _ in scores]
    away_goals = [away for _, away in scores]

    finished_with_1x2 = sum(1 for match in finished if has_complete_1x2_market_vector(match))
    finished_with_crs = sum(1 for match in finished if has_correct_score_market(match))
    hupu_rating_matches = sum(1 for match in finished if isinstance((match.get("hupu") or {}).get("ratingCount"), int))

    avg_total_goals = average(total_goals)
    draw_rate = safe_rate(draws, total)
    shrunk_total_goals = shrink(avg_total_goals, total, CURRENT_TOTAL_GOALS_BASELINE, SHRINKAGE_PRIOR_MATCHES)
    shrunk_draw_rate = shrink(draw_rate, total, WORLD_CUP_DRAW_PRIOR, SHRINKAGE_PRIOR_MATCHES)

    score_counter = Counter(f"{home}-{away}" for home, away in scores)
    common_scores = [
        {"score": score, "count": count, "rate": rounded(safe_rate(count, total))}
        for score, count in score_counter.most_common(8)
    ]

    return {
        "generatedAt": datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M CST"),
        "dataSource": {
            "sourceName": payload.get("sourceName"),
            "lastUpdated": payload.get("lastUpdated"),
        },
        "sample": {
            "matches": len(matches),
            "finished": len(finished),
            "upcoming": len(upcoming),
            "finishedWith1x2Snapshot": finished_with_1x2,
            "finishedWithCorrectScoreSnapshot": finished_with_crs,
            "finishedWithHupuRatingCount": hupu_rating_matches,
        },
        "observed": {
            "homeWins": home_wins,
            "draws": draws,
            "awayWins": away_wins,
            "homeWinRate": rounded(safe_rate(home_wins, total)),
            "drawRate": rounded(draw_rate),
            "awayWinRate": rounded(safe_rate(away_wins, total)),
            "avgHomeGoals": rounded(average(home_goals)),
            "avgAwayGoals": rounded(average(away_goals)),
            "avgTotalGoals": rounded(avg_total_goals),
            "over25Rate": rounded(safe_rate(sum(1 for goals in total_goals if goals > 2.5), total)),
            "bothTeamsScoredRate": rounded(safe_rate(sum(1 for home, away in scores if home > 0 and away > 0), total)),
            "commonScores": common_scores,
        },
        "readiness": {
            "scoreDistribution": readiness(
                total >= MIN_FINISHED_FOR_SCORE_CALIBRATION,
                total,
                MIN_FINISHED_FOR_SCORE_CALIBRATION,
                "finished matches with scores",
            ),
            "marketWeights": readiness(
                finished_with_1x2 >= MIN_MARKET_SNAPSHOTS,
                finished_with_1x2,
                MIN_MARKET_SNAPSHOTS,
                "finished matches with retained pre-match 1X2 odds",
            ),
            "sportteryScoreBlend": readiness(
                finished_with_crs >= MIN_SCORE_MARKET_SNAPSHOTS,
                finished_with_crs,
                MIN_SCORE_MARKET_SNAPSHOTS,
                "finished matches with retained pre-match correct-score odds",
            ),
        },
        "suggestions": {
            "totalGoalsBaseline": {
                "current": CURRENT_TOTAL_GOALS_BASELINE,
                "dataOnly": rounded(avg_total_goals),
                "shrunkEstimate": rounded(shrunk_total_goals),
                "autoApply": total >= MIN_FINISHED_FOR_SCORE_CALIBRATION,
                "note": "Use the shrunk estimate only after enough finished matches; the current sample is a monitoring signal, not a full calibration set.",
            },
            "drawRatePrior": {
                "currentPrior": WORLD_CUP_DRAW_PRIOR,
                "dataOnly": rounded(draw_rate),
                "shrunkEstimate": rounded(shrunk_draw_rate),
                "autoApply": total >= MIN_FINISHED_FOR_SCORE_CALIBRATION,
            },
            "sportteryScoreWeight": {
                "current": CURRENT_SPORTTERY_SCORE_WEIGHT,
                "autoApply": finished_with_crs >= MIN_SCORE_MARKET_SNAPSHOTS,
                "note": "Do not retune the model/CRS blend until finished matches have retained pre-match CRS snapshots.",
            },
        },
        "recommendations": recommendations(total, finished_with_1x2, finished_with_crs),
    }


def score_tuple(match: dict[str, Any]) -> tuple[int, int] | None:
    score = match.get("score")
    if not isinstance(score, list) or len(score) != 2:
        return None
    if isinstance(score[0], int) and isinstance(score[1], int):
        return score[0], score[1]
    return None


def has_complete_1x2_market_vector(match: dict[str, Any]) -> bool:
    odds = market_odds(match)
    if len(odds) < 3:
        return False

    vectors = [
        [positive_number(odd.get("sporttery")) for odd in odds[:3]],
        [positive_number(odd.get("referenceOdds")) if odd.get("referenceValid") is not False else None for odd in odds[:3]],
        [probability(odd.get("polymarket")) for odd in odds[:3]],
        [bookmaker_probability(odd) for odd in odds[:3]],
    ]
    return any(all(value is not None for value in vector) for vector in vectors)


def has_correct_score_market(match: dict[str, Any]) -> bool:
    snapshot = match.get("closingSnapshot")
    sporttery = snapshot.get("sporttery") if isinstance(snapshot, dict) else match.get("sporttery")
    if not isinstance(sporttery, dict):
        return False
    scores = sporttery.get("correctScore")
    return isinstance(scores, dict) and any(positive_number(value) is not None for value in scores.values())


def market_odds(match: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = match.get("closingSnapshot")
    odds = snapshot.get("odds") if isinstance(snapshot, dict) else match.get("odds")
    if not isinstance(odds, list):
        return []
    return [odd for odd in odds if isinstance(odd, dict)]


def bookmaker_probability(odd: dict[str, Any]) -> float | None:
    bookmakers = odd.get("bookmakers")
    if not isinstance(bookmakers, list):
        return None
    decimals = [positive_number(book.get("decimalOdds")) for book in bookmakers if isinstance(book, dict)]
    decimals = [value for value in decimals if value is not None]
    if decimals:
        return 1 / max(decimals)
    return None


def positive_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0 and math.isfinite(value):
        return float(value)
    return None


def probability(value: Any) -> float | None:
    if isinstance(value, (int, float)) and 0 < value < 1 and math.isfinite(value):
        return float(value)
    return None


def average(values: list[float | int]) -> float | None:
    return sum(values) / len(values) if values else None


def safe_rate(count: int, total: int) -> float | None:
    return count / total if total else None


def shrink(value: float | None, sample_size: int, prior: float, prior_matches: int) -> float | None:
    if value is None:
        return None
    return (value * sample_size + prior * prior_matches) / (sample_size + prior_matches)


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def readiness(ready: bool, current: int, required: int, unit: str) -> dict[str, Any]:
    return {
        "ready": ready,
        "current": current,
        "required": required,
        "unit": unit,
        "status": "ready" if ready else "collecting",
    }


def recommendations(finished: int, market_snapshots: int, score_snapshots: int) -> list[str]:
    items: list[str] = []
    if finished < MIN_FINISHED_FOR_SCORE_CALIBRATION:
        items.append("Keep score-distribution tuning conservative until at least 30 finished matches are available.")
    if market_snapshots < MIN_MARKET_SNAPSHOTS:
        items.append("Retain pre-match 1X2 closing snapshots before calibrating market-vs-performance weights.")
    if score_snapshots < MIN_SCORE_MARKET_SNAPSHOTS:
        items.append("Retain pre-match correct-score snapshots before changing the model/CRS blend weight.")
    if not items:
        items.append("Sample thresholds are met; run a grid search or logistic calibration before changing frontend constants.")
    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the dashboard model against finished match data.")
    parser.add_argument("--file", type=Path, default=DATA_FILE, help="Path to web/data/matches.js")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(load_payload(args.file))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
