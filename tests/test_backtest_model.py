from scripts.backtest_model import build_report


def test_backtest_report_summarizes_finished_scores() -> None:
    payload = {
        "sourceName": "fixture",
        "lastUpdated": "now",
        "matches": [
            {"status": "finished", "score": [2, 0], "odds": []},
            {"status": "finished", "score": [1, 1], "odds": []},
            {"status": "upcoming", "score": ["-", "-"], "odds": []},
        ],
    }

    report = build_report(payload)

    assert report["sample"]["matches"] == 3
    assert report["sample"]["finished"] == 2
    assert report["sample"]["upcoming"] == 1
    assert report["observed"]["homeWins"] == 1
    assert report["observed"]["draws"] == 1
    assert report["observed"]["avgTotalGoals"] == 2.0
    assert report["readiness"]["marketWeights"]["ready"] is False


def test_backtest_report_counts_closing_market_snapshots() -> None:
    payload = {
        "matches": [
            {
                "status": "finished",
                "score": [2, 1],
                "closingSnapshot": {
                    "odds": [
                        {"outcome": "主胜", "sporttery": 1.8},
                        {"outcome": "平局", "sporttery": 3.4},
                        {"outcome": "客胜", "sporttery": 4.2},
                    ],
                    "sporttery": {"correctScore": {"2-1": 7.5}},
                },
            }
        ],
    }

    report = build_report(payload)

    assert report["sample"]["finishedWith1x2Snapshot"] == 1
    assert report["sample"]["finishedWithCorrectScoreSnapshot"] == 1


def test_backtest_report_includes_recent_finished_form() -> None:
    payload = {
        "matches": [
            {"status": "finished", "kickoff": "2026-06-12 03:00 CST", "score": [1, 0], "odds": []},
            {"status": "finished", "kickoff": "2026-06-13 03:00 CST", "score": [1, 1], "odds": []},
            {"status": "finished", "kickoff": "2026-06-14 03:00 CST", "score": [2, 2], "odds": []},
            {"status": "finished", "kickoff": "2026-06-15 03:00 CST", "score": [4, 1], "odds": []},
            {"status": "finished", "kickoff": "2026-06-16 03:00 CST", "score": [3, 0], "odds": []},
        ],
    }

    report = build_report(payload)

    assert report["recent"]["window"] == 5
    assert report["recent"]["avgTotalGoals"] == 3.0
    assert report["recent"]["drawRate"] == 0.4
    assert report["recent"]["over25Rate"] == 0.6
    assert report["recent"]["matches"][-1]["score"] == "3-0"
