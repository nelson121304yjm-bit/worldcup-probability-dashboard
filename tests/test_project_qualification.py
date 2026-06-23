from scripts.project_qualification import build_projection, outcome_probabilities


def finished(stage: str, home: str, away: str, home_score: int, away_score: int) -> dict:
    return {
        "stage": stage,
        "home": home,
        "away": away,
        "status": "finished",
        "score": [home_score, away_score],
        "odds": [],
    }


def test_projection_locks_finished_group_top_two() -> None:
    payload = {
        "matches": [
            finished("A组", "A", "B", 1, 0),
            finished("A组", "A", "C", 1, 0),
            finished("A组", "A", "D", 1, 0),
            finished("A组", "B", "C", 1, 0),
            finished("A组", "B", "D", 1, 0),
            finished("A组", "C", "D", 1, 0),
        ]
    }

    projection = build_projection(payload, simulations=50, seed=1, best_third_count=0)
    teams = {team["team"]: team for team in projection["groups"][0]["teams"]}

    assert teams["A"]["currentPoints"] == 9
    assert teams["A"]["topTwoProbability"] == 1.0
    assert teams["B"]["advanceProbability"] == 1.0
    assert teams["C"]["advanceProbability"] == 0.0
    assert [team["team"] for team in projection["advancingTeams"]] == ["A", "B"]


def test_projection_selects_best_third_across_groups() -> None:
    payload = {
        "matches": [
            finished("A组", "A", "B", 1, 0),
            finished("A组", "A", "C", 1, 0),
            finished("A组", "A", "D", 1, 0),
            finished("A组", "B", "C", 1, 0),
            finished("A组", "B", "D", 1, 0),
            finished("A组", "C", "D", 1, 0),
            finished("B组", "E", "F", 1, 0),
            finished("B组", "E", "G", 1, 0),
            finished("B组", "E", "H", 1, 0),
            finished("B组", "F", "G", 1, 1),
            finished("B组", "F", "H", 1, 0),
            finished("B组", "G", "H", 1, 1),
        ]
    }

    projection = build_projection(payload, simulations=50, seed=1, best_third_count=1)
    teams = {team["team"]: team for group in projection["groups"] for team in group["teams"]}

    assert teams["C"]["thirdProbability"] == 1.0
    assert teams["C"]["advanceProbability"] == 1.0
    assert teams["G"]["thirdProbability"] == 1.0
    assert teams["G"]["advanceProbability"] == 0.0


def test_outcome_probabilities_move_toward_stronger_team() -> None:
    match = {
        "home": "强队",
        "away": "弱队",
        "odds": [
            {"outcome": "强队胜", "referenceOdds": 3.0},
            {"outcome": "平局", "referenceOdds": 3.0},
            {"outcome": "弱队胜", "referenceOdds": 3.0},
        ],
        "performance": {
            "home": {"form": 85, "attack": 85, "defense": 85, "playerHealth": 85, "starPower": 85, "goalkeeper": 85, "stamina": 85},
            "away": {"form": 35, "attack": 35, "defense": 35, "playerHealth": 35, "starPower": 35, "goalkeeper": 35, "stamina": 35},
        },
    }

    probabilities = outcome_probabilities(match)

    assert probabilities["home"] > probabilities["away"]
    assert round(sum(probabilities.values()), 8) == 1.0
