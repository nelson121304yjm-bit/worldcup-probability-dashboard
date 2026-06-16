import copy

from scripts.fetch_sporttery import (
    correct_score_odds,
    hupu_item_to_result,
    parse_hupu_schedules,
    parse_score,
    parse_wc2026_results,
    result_update_allowed,
    sporttery_pool,
    update_matches,
    wc2026_date,
)


def test_parse_score_handles_colon() -> None:
    assert parse_score("2:1") == (2, 1)
    assert parse_score(" 0-0 ") == (0, 0)
    assert parse_score("") is None


def test_correct_score_odds_maps_sporttery_keys() -> None:
    odds = correct_score_odds({"s00s00": "15.00", "s02s01": "6.10", "s02s01f": "0", "goalLine": ""})

    assert odds == {"0-0": 15.0, "2-1": 6.1}


def test_parse_wc2026_finished_cards() -> None:
    html = """
    <div class="wc-match-card is-finished">
      <div class="wc-match-date"> 06月16日 00:00<span> · 北京时间</span></div>
      <div class="wc-match-team"><span class="wc-team-name">西班牙</span><span class="wc-team-score">0</span></div>
      <div class="wc-match-team"><span class="wc-team-name">佛得角</span><span class="wc-team-score">0</span></div>
    </div>
    """

    assert parse_wc2026_results(html) == [
        {
            "matchDate": "2026-06-16",
            "homeTeam": "西班牙",
            "awayTeam": "佛得角",
            "leagueNameAbbr": "世界杯",
            "sectionsNo999": "0:0",
            "_source": "wc-2026",
            "_sourceUrl": "https://wc-2026.com/world-cup-odds/",
        }
    ]
    assert wc2026_date("06月16日 00:00 · 北京时间") == "2026-06-16"


def test_parse_hupu_schedules_from_next_data() -> None:
    payload = {
        "props": {
            "pageProps": {
                "schedules": [
                    {
                        "title": "世界杯第1轮",
                        "competitionId": "13009",
                        "home": {"name": "法国"},
                        "away": {"name": "塞内加尔"},
                        "begin_time": 1781636400,
                        "home_score": 0,
                        "away_score": 0,
                        "status": {"txt": "未开始"},
                        "matchBigStatus": {"txt": "未开始"},
                        "currentMatchId": "3513900",
                    },
                    {
                        "title": "英超",
                        "competitionId": "1",
                        "home": {"name": "阿森纳"},
                        "away": {"name": "切尔西"},
                        "dateTime": "2026年6月17日 3点00分 周三",
                    },
                ]
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json_dumps(payload)}</script>'

    assert parse_hupu_schedules(html) == [
        {
            "matchDate": "2026-06-17",
            "homeTeam": "法国",
            "awayTeam": "塞内加尔",
            "leagueNameAbbr": "世界杯",
            "_source": "hupu",
            "_sourceUrl": "https://m.hupu.com/soccerleagues/fifaWC/live/3513900?matchId=3513900",
            "_hupuStatus": "未开始",
            "_hupuMatchId": "3513900",
        }
    ]


def test_hupu_finished_match_includes_score() -> None:
    result = hupu_item_to_result(
        {
            "title": "世界杯第1轮",
            "home": {"name": "西班牙"},
            "away": {"name": "佛得角"},
            "dateTime": "2026年6月16日 0点00分 周二",
            "home_score": 2,
            "away_score": 1,
            "matchBigStatus": {"txt": "已结束"},
            "currentMatchId": "123",
        }
    )

    assert result
    assert result["matchDate"] == "2026-06-16"
    assert result["sectionsNo999"] == "2:1"


def test_sporttery_pool_converts_had_fields() -> None:
    pool = sporttery_pool({"h": "1.32", "d": "4.20", "a": "7.45", "updateDate": "2026-06-16", "updateTime": "18:59:59"})

    assert pool["home"] == 1.32
    assert pool["draw"] == 4.2
    assert pool["away"] == 7.45
    assert pool["lastUpdated"] == "2026-06-16 18:59:59"


def test_update_matches_refreshes_odds_and_finished_score() -> None:
    payload = {
        "sourceName": "snapshot",
        "lastUpdated": "-",
        "matches": [
            {
                "id": "france-senegal",
                "status": "upcoming",
                "stage": "I组",
                "kickoff": "2026-06-12 03:00 CST",
                "minute": "03:00",
                "home": "法国",
                "away": "塞内加尔",
                "score": ["-", "-"],
                "odds": [{"outcome": "法国胜"}, {"outcome": "平局"}, {"outcome": "塞内加尔胜"}],
                "sporttery": {"matchId": "2040178"},
                "timeline": [],
                "sources": [],
                "marketNotes": [],
            }
        ],
    }
    calculator_items = [
        {
            "matchId": 2040178,
            "matchNumStr": "周二017",
            "matchDate": "2026-06-12",
            "homeTeamAbbName": "法国",
            "awayTeamAbbName": "塞内加尔",
            "leagueAbbName": "世界杯",
            "had": {"h": "1.32", "d": "4.20", "a": "7.45", "updateDate": "2026-06-16", "updateTime": "18:59:59"},
            "hhad": {"h": "2.07", "d": "3.45", "a": "2.81", "goalLine": "-1"},
            "crs": {"s02s00": "6.50"},
        }
    ]
    result_items = [
        {
            "matchId": 2040178,
            "matchDate": "2026-06-12",
            "homeTeam": "法国",
            "awayTeam": "塞内加尔",
            "leagueNameAbbr": "世界杯",
            "sectionsNo999": "2:0",
        }
    ]

    changes = update_matches(payload, calculator_items, result_items)
    match = payload["matches"][0]

    assert changes == ["france-senegal 法国 vs 塞内加尔"]
    assert match["status"] == "finished"
    assert match["score"] == [2, 0]
    assert match["minute"] == "FT"
    assert match["odds"][0]["sporttery"] == 1.32
    assert match["odds"][1]["sporttery"] == 4.2
    assert match["odds"][2]["sporttery"] == 7.45
    assert match["sporttery"]["correctScore"] == {"2-0": 6.5}


def test_update_matches_does_not_touch_unmatched_games() -> None:
    payload = {
        "sourceName": "snapshot",
        "lastUpdated": "-",
        "matches": [
            {
                "id": "x",
                "status": "upcoming",
                "kickoff": "2026-06-17 03:00 CST",
                "home": "法国",
                "away": "塞内加尔",
                "score": ["-", "-"],
                "odds": [],
                "sporttery": {},
            }
        ],
    }
    original = copy.deepcopy(payload)
    calculator_items = [{"matchId": 1, "matchDate": "2026-06-17", "homeTeamAbbName": "法国", "awayTeamAbbName": "阿根廷", "leagueAbbName": "世界杯"}]

    assert update_matches(payload, calculator_items, []) == []
    assert payload == original


def test_future_results_are_not_applied_even_if_source_marks_finished() -> None:
    payload = {
        "sourceName": "snapshot",
        "lastUpdated": "-",
        "matches": [
            {
                "id": "future",
                "status": "upcoming",
                "kickoff": "2099-06-17 03:00 CST",
                "home": "法国",
                "away": "塞内加尔",
                "score": ["-", "-"],
                "odds": [],
                "sporttery": {},
            }
        ],
    }
    result_items = [
        {
            "matchDate": "2099-06-17",
            "homeTeam": "法国",
            "awayTeam": "塞内加尔",
            "leagueNameAbbr": "世界杯",
            "sectionsNo999": "2:0",
            "_source": "wc-2026",
        }
    ]

    assert result_update_allowed(payload["matches"][0]) is False
    assert update_matches(payload, [], result_items) == []
    assert payload["matches"][0]["status"] == "upcoming"
    assert payload["matches"][0]["score"] == ["-", "-"]


def test_hupu_metadata_applies_to_upcoming_match_without_score() -> None:
    payload = {
        "sourceName": "snapshot",
        "lastUpdated": "-",
        "matches": [
            {
                "id": "france-senegal",
                "status": "upcoming",
                "kickoff": "2026-06-17 03:00 CST",
                "home": "法国",
                "away": "塞内加尔",
                "score": ["-", "-"],
                "odds": [],
                "sporttery": {},
                "sources": [],
                "marketNotes": [],
            }
        ],
    }
    hupu_items = [
        {
            "matchDate": "2026-06-17",
            "homeTeam": "法国",
            "awayTeam": "塞内加尔",
            "leagueNameAbbr": "世界杯",
            "_source": "hupu",
            "_sourceUrl": "https://m.hupu.com/soccerleagues/fifaWC/live/3513900?matchId=3513900",
            "_hupuStatus": "未开始",
            "_hupuMatchId": "3513900",
        }
    ]

    changes = update_matches(payload, [], hupu_items)
    match = payload["matches"][0]

    assert changes == ["france-senegal 法国 vs 塞内加尔"]
    assert match["status"] == "upcoming"
    assert match["score"] == ["-", "-"]
    assert match["hupu"]["matchId"] == "3513900"
    assert "虎扑公开足球赛程页面用于近期赛程/赛果校验；不提供赔率或支持率。" in match["marketNotes"]
    assert "虎扑赛程校验" in payload["sourceName"]


def json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
