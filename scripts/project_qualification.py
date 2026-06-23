"""Project World Cup group-stage qualification from the current snapshot."""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "web" / "data" / "matches.js"
ASSIGNMENT_RE = re.compile(r"^\s*window\.WORLD_CUP_MATCHES\s*=\s*(\{.*\});?\s*$", re.S)
SHANGHAI = ZoneInfo("Asia/Shanghai")

PERFORMANCE_FIELDS = (
    ("form", 0.24),
    ("attack", 0.18),
    ("defense", 0.18),
    ("playerHealth", 0.16),
    ("starPower", 0.14),
    ("goalkeeper", 0.06),
    ("stamina", 0.04),
)
QUALIFICATION_GROUP_RE = re.compile(r"^[A-L]组$")
DEFAULT_SIMULATIONS = 2500
DEFAULT_BEST_THIRD_COUNT = 8


@dataclass
class TeamStanding:
    team: str
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def copy(self) -> "TeamStanding":
        return copy.deepcopy(self)


def load_payload(path: Path = DATA_FILE) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = ASSIGNMENT_RE.match(text)
    if not match:
        raise ValueError(f"{path} does not look like window.WORLD_CUP_MATCHES = {{...}}")
    return json.loads(match.group(1))


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"window.WORLD_CUP_MATCHES = {rendered};\n", encoding="utf-8")


def build_projection(
    payload: dict[str, Any],
    *,
    simulations: int = DEFAULT_SIMULATIONS,
    seed: int = 20260621,
    best_third_count: int = DEFAULT_BEST_THIRD_COUNT,
) -> dict[str, Any]:
    group_matches = [match for match in payload.get("matches") or [] if is_group_match(match)]
    groups = sorted({str(match.get("stage")) for match in group_matches})
    current = {group: build_current_standings(group_matches, group) for group in groups}
    remaining = {group: [match for match in group_matches if match.get("stage") == group and score_tuple(match) is None] for group in groups}

    counters = {
        team: {"topTwo": 0, "third": 0, "bestThird": 0, "advance": 0}
        for standings in current.values()
        for team in standings
    }
    rng = random.Random(seed)
    run_count = max(1, int(simulations))

    for _ in range(run_count):
        simulated = {group: {team: standing.copy() for team, standing in standings.items()} for group, standings in current.items()}
        for group, matches in remaining.items():
            for match in matches:
                result = sample_outcome(match, rng)
                apply_result(simulated[group], str(match.get("home")), str(match.get("away")), result)

        ranked_groups = {group: rank_standings(standings.values()) for group, standings in simulated.items()}
        third_candidates = []
        for group, ranked in ranked_groups.items():
            for index, standing in enumerate(ranked):
                if index < 2:
                    counters[standing.team]["topTwo"] += 1
                    counters[standing.team]["advance"] += 1
                elif index == 2:
                    counters[standing.team]["third"] += 1
                    third_candidates.append((group, standing))

        for _, standing in rank_third_candidates(third_candidates)[:best_third_count]:
            counters[standing.team]["bestThird"] += 1
            counters[standing.team]["advance"] += 1

    projected_groups = []
    for group in groups:
        current_ranked = rank_standings(current[group].values())
        projected_groups.append(
            {
                "group": group,
                "teams": [
                    {
                        "team": standing.team,
                        "currentRank": index + 1,
                        "currentPoints": standing.points,
                        "currentGoalDifference": standing.goal_difference,
                        "currentGoalsFor": standing.goals_for,
                        "topTwoProbability": rounded(counters[standing.team]["topTwo"] / run_count),
                        "thirdProbability": rounded(counters[standing.team]["third"] / run_count),
                        "bestThirdProbability": rounded(counters[standing.team]["bestThird"] / run_count),
                        "advanceProbability": rounded(counters[standing.team]["advance"] / run_count),
                    }
                    for index, standing in enumerate(current_ranked)
                ],
            }
        )

    advancing = sorted(
        [team for group in projected_groups for team in group["teams"]],
        key=lambda item: (-item["advanceProbability"], -item["topTwoProbability"], -item["currentPoints"], item["team"]),
    )
    return {
        "generatedAt": datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M CST"),
        "method": "小组赛出线蒙特卡洛：已完赛按真实比分计入，未赛按当前 1X2 市场共识 + 表现评分抽样；每组前二与 8 个最佳小组第三晋级。",
        "simulations": run_count,
        "bestThirdCount": best_third_count,
        "groups": projected_groups,
        "advancingTeams": advancing[: 2 * len(groups) + best_third_count],
    }


def is_group_match(match: dict[str, Any]) -> bool:
    return bool(QUALIFICATION_GROUP_RE.match(str(match.get("stage") or "")))


def build_current_standings(matches: list[dict[str, Any]], group: str) -> dict[str, TeamStanding]:
    standings: dict[str, TeamStanding] = {}
    for match in matches:
        if match.get("stage") != group:
            continue
        home = str(match.get("home") or "")
        away = str(match.get("away") or "")
        if not home or not away:
            continue
        standings.setdefault(home, TeamStanding(home))
        standings.setdefault(away, TeamStanding(away))
        score = score_tuple(match)
        if score is not None:
            apply_result(standings, home, away, result_from_score(*score))
    return standings


def apply_result(standings: dict[str, TeamStanding], home: str, away: str, result: tuple[int, int]) -> None:
    home_goals, away_goals = result
    standings.setdefault(home, TeamStanding(home))
    standings.setdefault(away, TeamStanding(away))
    home_row = standings[home]
    away_row = standings[away]
    home_row.played += 1
    away_row.played += 1
    home_row.goals_for += home_goals
    home_row.goals_against += away_goals
    away_row.goals_for += away_goals
    away_row.goals_against += home_goals
    if home_goals > away_goals:
        home_row.wins += 1
        away_row.losses += 1
        home_row.points += 3
    elif home_goals < away_goals:
        away_row.wins += 1
        home_row.losses += 1
        away_row.points += 3
    else:
        home_row.draws += 1
        away_row.draws += 1
        home_row.points += 1
        away_row.points += 1


def rank_standings(standings: Any) -> list[TeamStanding]:
    return sorted(
        standings,
        key=lambda item: (-item.points, -item.goal_difference, -item.goals_for, item.team),
    )


def rank_third_candidates(candidates: list[tuple[str, TeamStanding]]) -> list[tuple[str, TeamStanding]]:
    return sorted(
        candidates,
        key=lambda item: (-item[1].points, -item[1].goal_difference, -item[1].goals_for, item[1].team),
    )


def sample_outcome(match: dict[str, Any], rng: random.Random) -> tuple[int, int]:
    probabilities = outcome_probabilities(match)
    draw = rng.random()
    if draw < probabilities["home"]:
        return 1, 0
    if draw < probabilities["home"] + probabilities["draw"]:
        return 1, 1
    return 0, 1


def outcome_probabilities(match: dict[str, Any]) -> dict[str, float]:
    market = market_probabilities(match)
    home_strength = team_strength(((match.get("performance") or {}).get("home") or {}))
    away_strength = team_strength(((match.get("performance") or {}).get("away") or {}))
    edge = home_strength - away_strength
    logits = {
        "home": math.log(max(market["home"], 0.001)) + edge * 1.25,
        "draw": math.log(max(market["draw"], 0.001)) - abs(edge) * 0.55 + draw_balance_boost(match),
        "away": math.log(max(market["away"], 0.001)) - edge * 1.25,
    }
    max_logit = max(logits.values())
    exps = {key: math.exp(value - max_logit) for key, value in logits.items()}
    total = sum(exps.values())
    return {key: value / total for key, value in exps.items()}


def market_probabilities(match: dict[str, Any]) -> dict[str, float]:
    odds = [odd for odd in match.get("odds") or [] if isinstance(odd, dict)]
    if len(odds) < 3:
        return {"home": 0.34, "draw": 0.28, "away": 0.38}

    sources = [
        normalize_vector([positive_inverse(odd.get("sporttery")) for odd in odds[:3]]),
        normalize_vector(
            [
                positive_inverse(odd.get("referenceOdds")) if odd.get("referenceValid") is not False else None
                for odd in odds[:3]
            ]
        ),
        normalize_vector([probability(odd.get("polymarket")) for odd in odds[:3]]),
        normalize_vector([bookmaker_probability(odd) for odd in odds[:3]]),
    ]
    values = []
    for index in range(3):
        finite = [source[index] for source in sources if source[index] is not None]
        values.append(sum(finite) / len(finite) if finite else None)
    normalized = normalize_vector(values)
    if any(value is None for value in normalized):
        return {"home": 0.34, "draw": 0.28, "away": 0.38}
    return {"home": normalized[0], "draw": normalized[1], "away": normalized[2]}


def normalize_vector(values: list[float | None]) -> list[float | None]:
    finite = [value if value is not None and value > 0 and math.isfinite(value) else None for value in values]
    total = sum(value or 0 for value in finite)
    if total <= 0:
        return [None for _ in values]
    return [value / total if value is not None else None for value in finite]


def bookmaker_probability(odd: dict[str, Any]) -> float | None:
    bookmakers = odd.get("bookmakers")
    if not isinstance(bookmakers, list):
        return None
    decimals = [positive_number(book.get("decimalOdds")) for book in bookmakers if isinstance(book, dict)]
    decimals = [value for value in decimals if value is not None]
    return 1 / max(decimals) if decimals else None


def team_strength(values: dict[str, Any]) -> float:
    return sum(((score(values.get(key, 50)) - 50) / 50) * weight for key, weight in PERFORMANCE_FIELDS)


def draw_balance_boost(match: dict[str, Any]) -> float:
    performance = match.get("performance") or {}
    home = performance.get("home") or {}
    away = performance.get("away") or {}
    defensive_average = (
        score(home.get("defense", 50))
        + score(home.get("goalkeeper", 50))
        + score(away.get("defense", 50))
        + score(away.get("goalkeeper", 50))
    ) / 4
    return ((defensive_average - 50) / 50) * 0.18


def score(value: Any) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return 50
    return max(0, min(100, float(value)))


def score_tuple(match: dict[str, Any]) -> tuple[int, int] | None:
    score_value = match.get("score")
    if isinstance(score_value, list) and len(score_value) == 2 and all(isinstance(item, int) for item in score_value):
        return score_value[0], score_value[1]
    return None


def result_from_score(home: int, away: int) -> tuple[int, int]:
    return home, away


def positive_inverse(value: Any) -> float | None:
    number = positive_number(value)
    return 1 / number if number is not None else None


def positive_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0 and math.isfinite(float(value)):
        return float(value)
    return None


def probability(value: Any) -> float | None:
    if isinstance(value, (int, float)) and 0 < value < 1 and math.isfinite(float(value)):
        return float(value)
    return None


def rounded(value: float, digits: int = 4) -> float:
    return round(value, digits)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project group qualification probabilities.")
    parser.add_argument("--file", type=Path, default=DATA_FILE, help="Path to web/data/matches.js")
    parser.add_argument("--simulations", type=int, default=DEFAULT_SIMULATIONS)
    parser.add_argument("--seed", type=int, default=20260621)
    parser.add_argument("--write", action="store_true", help="Write projection into the dashboard snapshot")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_payload(args.file)
    projection = build_projection(payload, simulations=args.simulations, seed=args.seed)
    if args.write:
        payload["qualificationProjection"] = projection
        write_payload(args.file, payload)
    print(json.dumps(projection, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
