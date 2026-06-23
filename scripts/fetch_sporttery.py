"""Refresh the static dashboard snapshot from public football sources.

The updater is deliberately conservative:

- It uses Sporttery public JSON for calculator odds and attempts Sporttery
  public results.
- If Sporttery results are unavailable, it falls back to finished-score cards on
  the public wc-2026 odds page and recent schedules from Hupu's public football
  page.
- It updates a match only when it can match by Sporttery match id or by
  Chinese team names plus kickoff date.
- It does not invent missing odds, historical closing lines, or community
  support signals.
- It exits with code 2 when no file changes were needed, so GitHub Actions can
  skip empty commits.
"""

from __future__ import annotations

import argparse
import copy
import html as html_lib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.project_qualification import build_projection
except ModuleNotFoundError:  # pragma: no cover - used when running this file directly
    from project_qualification import build_projection


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "web" / "data" / "matches.js"
ASSIGNMENT_RE = re.compile(r"^\s*window\.WORLD_CUP_MATCHES\s*=\s*(\{.*\});?\s*$", re.S)
SPORTTERY_CALCULATOR_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry"
SPORTTERY_RESULTS_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
SPORTTERY_SOURCE_URL = "https://www.sporttery.cn/jc/zqsgkj/"
WC2026_ODDS_URL = "https://wc-2026.com/world-cup-odds/"
HUPU_MOBILE_SOCCER_URL = "https://m.hupu.com/soccer"
HUPU_MOBILE_SCHEDULE_URL = "https://m.hupu.com/soccer/schedule"
PANEWS_ARENA_URL = "https://worldcup.panewslab.com/"
PANEWS_ARENA_STATE_URL = "https://worldcup.panewslab.com/api/arena-state"
SHANGHAI = ZoneInfo("Asia/Shanghai")
PANEWS_MODELS = {
    "minimax": {"name": "MiniMax Match Desk", "short": "MiniMax", "color": "#0c8f64"},
    "deepseek": {"name": "DeepSeek Match Desk", "short": "DeepSeek", "color": "#b68417"},
    "gemini": {"name": "Gemini Scout", "short": "Gemini", "color": "#385f9f"},
    "kimi": {"name": "Kimi Pitch Trader", "short": "Kimi", "color": "#b74335"},
    "glm": {"name": "GLM Match Analyst", "short": "GLM", "color": "#096d78"},
}
TEAM_CODES = {
    "阿尔及利亚": {"ALG"},
    "阿根廷": {"ARG"},
    "澳大利亚": {"AUS"},
    "奥地利": {"AUT"},
    "比利时": {"BEL"},
    "波黑": {"BOS", "BIH"},
    "巴西": {"BRA"},
    "加拿大": {"CAN"},
    "佛得角": {"CAB", "CVI"},
    "哥伦比亚": {"COL"},
    "哥斯达黎加": {"CRC"},
    "刚果民主共和国": {"CDR", "COD", "DRC"},
    "库拉索": {"CUR", "CUW"},
    "捷克": {"CZE"},
    "厄瓜多尔": {"ECU"},
    "埃及": {"EGY"},
    "英格兰": {"ENG"},
    "法国": {"FRA"},
    "德国": {"GER"},
    "加纳": {"GHA"},
    "海地": {"HAI"},
    "伊朗": {"IRN"},
    "伊拉克": {"IRQ"},
    "意大利": {"ITA"},
    "日本": {"JPN"},
    "约旦": {"JOR"},
    "韩国": {"KOR", "KR"},
    "墨西哥": {"MEX"},
    "摩洛哥": {"MAR"},
    "荷兰": {"NLD", "NET"},
    "新西兰": {"NZL"},
    "挪威": {"NOR"},
    "巴拿马": {"PAN"},
    "巴拉圭": {"PAR"},
    "葡萄牙": {"PRT"},
    "卡塔尔": {"QAT"},
    "沙特阿拉伯": {"KSA", "SAU"},
    "苏格兰": {"SCO"},
    "塞内加尔": {"SEN"},
    "西班牙": {"ESP", "SPA"},
    "南非": {"RSA"},
    "瑞典": {"SWE"},
    "瑞士": {"CHE"},
    "突尼斯": {"TUN"},
    "土耳其": {"TUR"},
    "乌拉圭": {"URY", "URU"},
    "美国": {"USA"},
    "乌兹别克斯坦": {"UZB"},
    "科特迪瓦": {"CIV", "CÔT", "COT"},
}


@dataclass(frozen=True)
class FetchOptions:
    data_file: Path
    dry_run: bool
    days_back: int
    days_forward: int
    timeout: int


def load_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = ASSIGNMENT_RE.match(text)
    if not match:
        raise ValueError(f"{path} does not look like window.WORLD_CUP_MATCHES = {{...}}")
    return json.loads(match.group(1))


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"window.WORLD_CUP_MATCHES = {rendered};\n", encoding="utf-8")


def fetch_json(url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "worldcup-probability-dashboard/1.0 (+https://github.com/nelson121304yjm-bit/worldcup-probability-dashboard)",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.sporttery.cn/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc


def fetch_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; worldcup-probability-dashboard/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc


def iter_calculator_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("value") or {}
    matches: list[dict[str, Any]] = []
    for group in value.get("matchInfoList") or []:
        matches.extend(group.get("subMatchList") or [])
    return [item for item in matches if is_world_cup_item(item)]


def iter_result_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (payload.get("value") or {}).get("matchResult") or [] if is_world_cup_item(item)]


class WC2026ResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._card_depth = 0
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set(str(dict(attrs).get("class") or "").split())
        if tag == "div" and "wc-match-card" in classes and "is-finished" in classes:
            self._current = {"teams": [], "scores": []}
            self._card_depth = 1
            return

        if self._current is not None and tag == "div":
            self._card_depth += 1

        if self._current is None:
            return

        if tag == "div" and "wc-match-date" in classes:
            self._capture = "date"
        elif tag == "span" and "wc-team-name" in classes:
            self._capture = "team"
        elif tag == "span" and "wc-team-score" in classes:
            self._capture = "score"

    def handle_data(self, data: str) -> None:
        if self._current is None or self._capture is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._capture == "date":
            self._current["dateText"] = f"{self._current.get('dateText', '')}{text}"
        elif self._capture == "team":
            self._current["teams"].append(text)
        elif self._capture == "score":
            self._current["scores"].append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and self._capture and tag in {"div", "span"}:
            self._capture = None

        if self._current is not None and tag == "div":
            self._card_depth -= 1
            if self._card_depth <= 0:
                self.cards.append(self._current)
                self._current = None
                self._card_depth = 0


def parse_wc2026_results(html: str) -> list[dict[str, Any]]:
    parser = WC2026ResultParser()
    parser.feed(html)
    results: list[dict[str, Any]] = []
    for card in parser.cards:
        teams = card.get("teams") or []
        scores = card.get("scores") or []
        match_date_value = wc2026_date(str(card.get("dateText") or ""))
        if len(teams) >= 2 and len(scores) >= 2 and match_date_value:
            results.append(
                {
                    "matchDate": match_date_value,
                    "homeTeam": teams[0],
                    "awayTeam": teams[1],
                    "leagueNameAbbr": "世界杯",
                    "sectionsNo999": f"{scores[0]}:{scores[1]}",
                    "_source": "wc-2026",
                    "_sourceUrl": WC2026_ODDS_URL,
                }
            )
    return results


def parse_hupu_schedules(html: str) -> list[dict[str, Any]]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S)
    if not match:
        return []

    try:
        payload = json.loads(html_lib.unescape(match.group(1)))
    except json.JSONDecodeError:
        return []

    page_props = (payload.get("props") or {}).get("pageProps") or {}
    schedules = list(page_props.get("schedules") or [])
    schedule_data = page_props.get("data") or {}
    if isinstance(schedule_data, dict):
        for group in schedule_data.get("games") or []:
            if isinstance(group, dict):
                schedules.extend(group.get("data") or [])
    results: list[dict[str, Any]] = []
    for item in schedules:
        if not isinstance(item, dict) or not is_hupu_world_cup_item(item):
            continue

        converted = hupu_item_to_result(item)
        if converted:
            results.append(converted)
    return results


def is_hupu_world_cup_item(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "")
    competition_id = str(item.get("competitionId") or "")
    return "世界杯" in title or competition_id == "13009"


def hupu_item_to_result(item: dict[str, Any]) -> dict[str, Any] | None:
    home = hupu_team_name(item.get("home"))
    away = hupu_team_name(item.get("away"))
    kickoff = hupu_match_datetime(item)
    if not home or not away or not kickoff:
        return None

    match_id = str(item.get("currentMatchId") or "").strip()
    status = hupu_status_text(item)
    result: dict[str, Any] = {
        "matchDate": kickoff.date().isoformat(),
        "homeTeam": home,
        "awayTeam": away,
        "leagueNameAbbr": "世界杯",
        "_source": "hupu",
        "_sourceUrl": HUPU_MOBILE_SCHEDULE_URL,
        "_hupuStatus": status,
        "_hupuMatchId": match_id,
    }
    rating_count = parse_hupu_metric_text(item.get("pv"))
    if rating_count is not None:
        result["_hupuRatingCount"] = rating_count
        result["_hupuRatingText"] = str(item.get("pv") or "").strip()

    match_count = to_int(item.get("matchCount"))
    if match_count is not None:
        result["_hupuHeat"] = match_count

    home_score = item.get("home_score")
    away_score = item.get("away_score")
    if hupu_is_finished(status) and isinstance(home_score, int) and isinstance(away_score, int):
        result["sectionsNo999"] = f"{home_score}:{away_score}"

    return result


def hupu_team_name(raw: Any) -> str:
    if isinstance(raw, dict):
        return normalize_team(str(raw.get("name") or "").strip())
    return normalize_team(str(raw or "").strip())


def hupu_match_datetime(item: dict[str, Any]) -> datetime | None:
    begin_time = item.get("begin_time")
    if isinstance(begin_time, (int, float)) and begin_time > 0:
        return datetime.fromtimestamp(begin_time, tz=SHANGHAI)

    china_time = str(item.get("chinaMatchTime") or "")
    if china_time:
        try:
            return datetime.fromisoformat(china_time.replace("Z", "+00:00")).astimezone(SHANGHAI)
        except ValueError:
            pass

    date_time = str(item.get("dateTime") or "")
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2})点(?:(\d{1,2})分)?", date_time)
    if match:
        year, month, day, hour, minute = match.groups()
        return datetime(int(year), int(month), int(day), int(hour), int(minute or 0), tzinfo=SHANGHAI)
    return None


def hupu_status_text(item: dict[str, Any]) -> str:
    for key in ("matchBigStatus", "status"):
        status = item.get(key)
        if isinstance(status, dict):
            text = str(status.get("txt") or status.get("desc") or "").strip()
            if text:
                return text
    return ""


def hupu_is_finished(status: str) -> bool:
    return any(token in status for token in ("已结束", "完场", "结束", "FT"))


def parse_hupu_metric_text(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万)?", text)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2):
        number *= 10000
    return int(round(number))


def wc2026_date(text: str) -> str | None:
    match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if not match:
        return None
    month, day = map(int, match.groups())
    return date(2026, month, day).isoformat()


def is_world_cup_item(item: dict[str, Any]) -> bool:
    league = str(item.get("leagueAbbName") or item.get("leagueNameAbbr") or item.get("leagueName") or "")
    return "世界杯" in league


def team_names(item: dict[str, Any]) -> tuple[str, str]:
    home = str(item.get("homeTeamAbbName") or item.get("homeTeam") or item.get("allHomeTeam") or item.get("homeTeamAllName") or "").strip()
    away = str(item.get("awayTeamAbbName") or item.get("awayTeam") or item.get("allAwayTeam") or item.get("awayTeamAllName") or "").strip()
    return normalize_team(home), normalize_team(away)


def normalize_team(value: str) -> str:
    aliases = {
        "沙特": "沙特阿拉伯",
    }
    value = value.strip()
    return aliases.get(value, value)


def match_date(item: dict[str, Any]) -> str:
    return str(item.get("matchDate") or "").strip()


def match_key(item: dict[str, Any]) -> tuple[str, str, str]:
    home, away = team_names(item)
    return home, away, match_date(item)


def dashboard_key(match: dict[str, Any]) -> tuple[str, str, str]:
    date_part = str(match.get("kickoff") or "")[:10]
    return normalize_team(str(match.get("home") or "")), normalize_team(str(match.get("away") or "")), date_part


def sporttery_id(match: dict[str, Any]) -> str:
    return str((match.get("sporttery") or {}).get("matchId") or "").strip()


def item_source(item: dict[str, Any]) -> str:
    return str(item.get("_source") or "sporttery")


def source_priority(item: dict[str, Any]) -> int:
    source = item_source(item)
    if source == "sporttery":
        return 0
    if source == "wc-2026":
        return 1
    if source == "hupu":
        return 2
    return 9


def build_indexes(
    calculator_items: list[dict[str, Any]],
    result_items: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
]:
    calculator_by_id: dict[str, dict[str, Any]] = {}
    result_by_id: dict[str, dict[str, Any]] = {}
    calculator_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    result_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    hupu_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    for item in calculator_items:
        item_id = str(item.get("matchId") or "")
        if item_id:
            calculator_by_id[item_id] = item
        calculator_by_key[match_key(item)] = item

    for item in sorted(result_items, key=source_priority, reverse=True):
        if item_source(item) == "hupu":
            hupu_by_key[match_key(item)] = item
        item_id = str(item.get("matchId") or "")
        if item_id:
            result_by_id[item_id] = item
        result_by_key[match_key(item)] = item

    return calculator_by_id, result_by_id, calculator_by_key, result_by_key, hupu_by_key


def update_matches(payload: dict[str, Any], calculator_items: list[dict[str, Any]], result_items: list[dict[str, Any]]) -> list[str]:
    calculator_by_id, result_by_id, calculator_by_key, result_by_key, hupu_by_key = build_indexes(calculator_items, result_items)
    changes: list[str] = []
    panews_state = payload.pop("_panewsArenaState", None)

    for match in payload.get("matches") or []:
        before = json.dumps(match, ensure_ascii=False, sort_keys=True)
        key = dashboard_key(match)
        calc_item = calculator_by_id.get(sporttery_id(match)) or calculator_by_key.get(key)
        result_item = result_by_id.get(sporttery_id(match)) or result_by_key.get(key)
        hupu_item = hupu_by_key.get(key)

        if result_item and is_same_dashboard_match(match, result_item):
            preserve_snapshot_before_result(match, result_item)

        if calc_item and is_same_dashboard_match(match, calc_item):
            apply_calculator_item(match, calc_item)

        if hupu_item and is_same_dashboard_match(match, hupu_item):
            apply_hupu_item(match, hupu_item)

        if result_item and is_same_dashboard_match(match, result_item):
            apply_result_item(match, result_item)

        if panews_state:
            apply_panews_ai_item(match, panews_state)

        after = json.dumps(match, ensure_ascii=False, sort_keys=True)
        if after != before:
            changes.append(f"{match.get('id')} {match.get('home')} vs {match.get('away')}")

    if changes:
        payload["qualificationProjection"] = build_projection(payload)
        now = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M CST")
        payload["lastUpdated"] = f"{now}（自动刷新 Sporttery 公开赛果/赔率 + 虎扑近期赛程/热度；未匹配数据保持原状）"
        source = str(payload.get("sourceName") or "").replace("虎扑赛程校验", "虎扑赛程/热度校验")
        if "Sporttery 自动更新" not in source:
            payload["sourceName"] = f"{source}；Sporttery 自动更新" if source else "Sporttery 自动更新"
        else:
            payload["sourceName"] = source
        if "虎扑赛程/热度校验" not in payload["sourceName"]:
            payload["sourceName"] = f"{payload['sourceName']}；虎扑赛程/热度校验"
        if any(isinstance(match.get("panewsAi"), dict) for match in payload.get("matches") or []):
            if "PANews AI Arena" not in payload["sourceName"]:
                payload["sourceName"] = f"{payload['sourceName']}；PANews AI Arena"

    return changes


def preserve_snapshot_before_result(match: dict[str, Any], result_item: dict[str, Any]) -> None:
    if match.get("status") == "finished":
        return
    if not result_update_allowed(match):
        return
    if not parse_score(str(result_item.get("sectionsNo999") or "")):
        return
    preserve_closing_snapshot(match, result_item)


def is_same_dashboard_match(match: dict[str, Any], sporttery_item: dict[str, Any]) -> bool:
    home, away = team_names(sporttery_item)
    dash_home, dash_away, dash_date = dashboard_key(match)
    item_date = match_date(sporttery_item)
    return bool(home and away and item_date and home == dash_home and away == dash_away and item_date == dash_date)


def apply_calculator_item(match: dict[str, Any], item: dict[str, Any]) -> None:
    sporttery = match.setdefault("sporttery", {})
    sporttery["matchId"] = str(item.get("matchId") or sporttery.get("matchId") or "")
    sporttery["matchNumStr"] = str(item.get("matchNumStr") or sporttery.get("matchNumStr") or "")
    sporttery["sourceUrl"] = "https://www.sporttery.cn/jc/jsq/zqspf/"
    sporttery["lastUpdated"] = combine_update_time(item) or sporttery.get("lastUpdated", "")
    sporttery["had"] = sporttery_pool(item.get("had") or {})
    sporttery["hhad"] = sporttery_pool(item.get("hhad") or {})
    sporttery["correctScore"] = correct_score_odds(item.get("crs") or {})

    update_odds_vector(match, sporttery["had"])
    append_note_once(match, "marketNotes", "Sporttery HAD/HHAD/CRS 由官方计算器公开接口自动刷新。")
    append_source_once(match, "https://www.sporttery.cn/jc/jsq/zqspf/")


def apply_result_item(match: dict[str, Any], item: dict[str, Any]) -> None:
    if not result_update_allowed(match):
        return

    score = parse_score(str(item.get("sectionsNo999") or ""))
    if score:
        old_score = tuple(match.get("score") or ())
        old_status = match.get("status")
        match["status"] = "finished"
        match["minute"] = "FT"
        match["score"] = list(score)
        if old_status != "finished" or old_score != score:
            source_name = result_source_name(item)
            source_url = str(item.get("_sourceUrl") or SPORTTERY_SOURCE_URL)
            append_timeline_result(match, score, f"赛果来自 {source_name} 自动刷新。")
            append_note_once(match, "marketNotes", f"已完赛比分由 {source_name} 自动刷新；未保存的历史盘口不补造。")
            append_source_once(match, source_url)

    result_pool = result_odds_pool(item)
    if result_pool:
        update_odds_vector(match, result_pool)

    if item.get("_source") == "hupu":
        hupu = match.setdefault("hupu", {})
        if item.get("_hupuMatchId"):
            hupu["matchId"] = str(item.get("_hupuMatchId"))
        hupu["status"] = str(item.get("_hupuStatus") or "")
        hupu["sourceUrl"] = str(item.get("_sourceUrl") or HUPU_MOBILE_SOCCER_URL)
        apply_hupu_metrics(hupu, item)
    elif item.get("_source") != "wc-2026":
        sporttery = match.setdefault("sporttery", {})
        if item.get("matchId"):
            sporttery["matchId"] = str(item.get("matchId"))
        if item.get("matchNumStr"):
            sporttery["matchNumStr"] = str(item.get("matchNumStr"))
        sporttery["sourceUrl"] = sporttery.get("sourceUrl") or SPORTTERY_SOURCE_URL


def result_odds_pool(item: dict[str, Any]) -> dict[str, Any] | None:
    if not any(str(item.get(key) or "").strip() for key in ("h", "d", "a")):
        return None
    return {
        "home": to_float(item.get("h")),
        "draw": to_float(item.get("d")),
        "away": to_float(item.get("a")),
        "goalLine": str(item.get("goalLine") or ""),
        "lastUpdated": str(item.get("matchDate") or ""),
    }


def preserve_closing_snapshot(match: dict[str, Any], item: dict[str, Any]) -> None:
    if isinstance(match.get("closingSnapshot"), dict):
        return

    odds = snapshot_odds(match.get("odds") or [])
    sporttery = snapshot_sporttery(match.get("sporttery") or {})
    if not odds and not sporttery:
        return

    snapshot: dict[str, Any] = {
        "capturedAt": datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M CST"),
        "reason": "Captured before marking the match as finished, for future model backtests.",
        "resultSource": result_source_name(item),
        "kickoff": str(match.get("kickoff") or ""),
    }
    if odds:
        snapshot["odds"] = odds
    if sporttery:
        snapshot["sporttery"] = sporttery
    if isinstance(match.get("sources"), list) and match["sources"]:
        snapshot["sources"] = [str(source) for source in match["sources"]]

    match["closingSnapshot"] = snapshot


def snapshot_odds(raw_odds: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_odds, list):
        return []

    odds: list[dict[str, Any]] = []
    has_price = False
    for raw in raw_odds[:3]:
        if not isinstance(raw, dict):
            continue
        cleaned: dict[str, Any] = {}
        for key in ("outcome", "referenceOdds", "referenceValid", "sporttery", "polymarket"):
            if key in raw:
                cleaned[key] = copy.deepcopy(raw[key])

        bookmakers = snapshot_bookmakers(raw.get("bookmakers"))
        if bookmakers:
            cleaned["bookmakers"] = bookmakers

        if cleaned:
            has_price = has_price or odds_entry_has_price(cleaned)
            odds.append(cleaned)

    return odds if has_price else []


def snapshot_bookmakers(raw_bookmakers: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_bookmakers, list):
        return []

    bookmakers: list[dict[str, Any]] = []
    for raw in raw_bookmakers:
        if not isinstance(raw, dict):
            continue
        cleaned = {
            key: copy.deepcopy(raw[key])
            for key in ("book", "decimalOdds", "americanOdds", "sourceUrl", "lastUpdated")
            if key in raw
        }
        if cleaned and odds_entry_has_price(cleaned):
            bookmakers.append(cleaned)
    return bookmakers


def snapshot_sporttery(raw_sporttery: Any) -> dict[str, Any]:
    if not isinstance(raw_sporttery, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for key in ("matchId", "matchNumStr", "sourceUrl", "lastUpdated"):
        if raw_sporttery.get(key):
            cleaned[key] = copy.deepcopy(raw_sporttery[key])
    for key in ("had", "hhad", "correctScore"):
        value = raw_sporttery.get(key)
        if isinstance(value, dict) and value:
            cleaned[key] = copy.deepcopy(value)
    return cleaned if snapshot_sporttery_has_market(cleaned) else {}


def snapshot_sporttery_has_market(sporttery: dict[str, Any]) -> bool:
    return any(isinstance(sporttery.get(key), dict) and sporttery[key] for key in ("had", "hhad", "correctScore"))


def odds_entry_has_price(odd: dict[str, Any]) -> bool:
    for key in ("referenceOdds", "sporttery", "decimalOdds"):
        value = odd.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    polymarket = odd.get("polymarket")
    if isinstance(polymarket, (int, float)) and 0 <= polymarket <= 1:
        return True
    american = odd.get("americanOdds")
    if isinstance(american, (int, float)) and american != 0:
        return True
    return False


def apply_panews_ai_item(match: dict[str, Any], arena_state: dict[str, Any]) -> None:
    arena_match = find_panews_match(match, arena_state.get("matches") or [])
    if not arena_match:
        return

    prediction = panews_prediction_for_match(arena_match, arena_state)
    if not prediction:
        return

    match["panewsAi"] = prediction
    append_source_once(match, PANEWS_ARENA_URL)
    append_note_once(match, "marketNotes", "PANews AI Arena 提供外部 AI 交易观点/持仓快照；该数据来自公开页面，不参与本站模型计权。")


def find_panews_match(match: dict[str, Any], arena_matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    kickoff = parse_dashboard_kickoff(match)
    home_codes = team_codes(match.get("home"))
    away_codes = team_codes(match.get("away"))
    if not kickoff or not home_codes or not away_codes:
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for item in arena_matches:
        if not isinstance(item, dict):
            continue
        arena_kickoff = parse_iso_datetime(item.get("kickoffTs"))
        if not arena_kickoff:
            continue
        minutes = abs(int((arena_kickoff - kickoff).total_seconds() / 60))
        if minutes > 90:
            continue
        arena_home = normalize_code(item.get("homeCode"))
        arena_away = normalize_code(item.get("awayCode"))
        if arena_home not in home_codes or arena_away not in away_codes:
            continue
        if best is None or minutes < best[0]:
            best = (minutes, item)
    return best[1] if best else None


def panews_prediction_for_match(arena_match: dict[str, Any], arena_state: dict[str, Any]) -> dict[str, Any] | None:
    trades = [
        trade
        for trade in (arena_state.get("tradeBook") or {}).get("trades") or []
        if isinstance(trade, dict) and trade.get("matchId") == arena_match.get("id") and trade.get("status") == "executed"
    ]
    model_accounts = arena_state.get("modelAccounts") or {}
    model_rows = []
    for model_id, meta in PANEWS_MODELS.items():
        model_trades = sorted(
            [trade for trade in trades if trade.get("modelId") == model_id],
            key=lambda trade: str(trade.get("ts") or ""),
        )
        latest = model_trades[-1] if model_trades else None
        position = panews_position_for_model(model_accounts.get(model_id), arena_match.get("id"))
        probabilities = normalize_panews_probabilities(
            latest.get("probabilities") if latest else None,
            arena_match.get("prices") or {},
        )
        if not latest and not position:
            continue
        model_rows.append(
            {
                "modelId": model_id,
                "name": meta["name"],
                "short": meta["short"],
                "color": meta["color"],
                "latestAction": panews_action(latest),
                "outcome": str((latest or position or {}).get("outcome") or ""),
                "probabilities": probabilities,
                "reason": str((latest or {}).get("reason") or "").strip()[:220],
                "amount": rounded_number((latest or {}).get("amount")),
                "price": rounded_number((latest or {}).get("price"), 4),
                "shares": rounded_number(position.get("shares") if position else (latest or {}).get("shares"), 2),
                "positionValue": rounded_number(position.get("value") if position else None, 2),
                "updatedAt": str((latest or {}).get("ts") or ""),
            }
        )

    if not model_rows:
        return None

    consensus = panews_consensus(model_rows)
    return {
        "sourceName": "PANews AI Arena",
        "sourceUrl": PANEWS_ARENA_URL,
        "arenaMatchId": str(arena_match.get("id") or ""),
        "matchUrl": str(arena_match.get("sourceUrl") or PANEWS_ARENA_URL),
        "lastUpdated": str((arena_state.get("status") or {}).get("updatedAt") or (arena_state.get("status") or {}).get("lastTradeAt") or ""),
        "marketPrices": normalize_panews_probabilities(arena_match.get("prices") or {}, None),
        "consensus": consensus,
        "models": model_rows,
        "note": "外部 AI 交易观点，来自 PANews World Cup AI Arena 公开账本；不等同本站概率模型。",
    }


def panews_position_for_model(account: Any, match_id: Any) -> dict[str, Any] | None:
    if not isinstance(account, dict):
        return None
    positions = account.get("displayPositions")
    if not isinstance(positions, list):
        positions = list((account.get("positions") or {}).values()) if isinstance(account.get("positions"), dict) else []
    best: dict[str, Any] | None = None
    for item in positions:
        if not isinstance(item, dict) or item.get("matchId") != match_id:
            continue
        value = to_float(item.get("value")) or 0
        if best is None or value > (to_float(best.get("value")) or 0):
            best = item
    return best


def normalize_panews_probabilities(raw: Any, fallback: Any) -> dict[str, float | None]:
    raw = raw if isinstance(raw, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    values = {
        "home": to_float(raw.get("home")) if raw.get("home") is not None else to_float(fallback.get("home")),
        "draw": to_float(raw.get("draw")) if raw.get("draw") is not None else to_float(fallback.get("draw")),
        "away": to_float(raw.get("away")) if raw.get("away") is not None else to_float(fallback.get("away")),
    }
    finite = [value for value in values.values() if isinstance(value, (int, float)) and value >= 0]
    total = sum(finite)
    if total > 0:
        return {
            key: round(float(value) / total, 4) if isinstance(value, (int, float)) and value >= 0 else None
            for key, value in values.items()
        }
    return {key: None for key in ("home", "draw", "away")}


def panews_consensus(models: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"home": [], "draw": [], "away": []}
    picks: list[str] = []
    for model in models:
        probabilities = model.get("probabilities") or {}
        finite = {key: probabilities.get(key) for key in ("home", "draw", "away") if isinstance(probabilities.get(key), (int, float))}
        for key, value in finite.items():
            totals[key].append(value)
        if finite:
            picks.append(max(finite.items(), key=lambda item: item[1])[0])

    average_probabilities = {
        key: round(sum(values) / len(values), 4) if values else None for key, values in totals.items()
    }
    top_outcome = ""
    top_probability = None
    finite_average = {key: value for key, value in average_probabilities.items() if isinstance(value, (int, float))}
    if finite_average:
        top_outcome, top_probability = max(finite_average.items(), key=lambda item: item[1])

    return {
        "modelCount": len(models),
        "topOutcome": top_outcome,
        "topProbability": top_probability,
        "averageProbabilities": average_probabilities,
        "agreement": round(picks.count(top_outcome) / len(picks), 4) if top_outcome and picks else None,
    }


def panews_action(trade: dict[str, Any] | None) -> str:
    if not trade:
        return "hold"
    side = str(trade.get("side") or "")
    if side in {"buy", "sell"}:
        return side
    return "hold"


def team_codes(value: Any) -> set[str]:
    normalized = normalize_team(str(value or ""))
    return {normalize_code(code) for code in TEAM_CODES.get(normalized, set()) if normalize_code(code)}


def normalize_code(value: Any) -> str:
    return str(value or "").strip().upper()


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(SHANGHAI)
    except ValueError:
        return None


def rounded_number(value: Any, digits: int = 2) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    return round(number, digits)


def apply_hupu_item(match: dict[str, Any], item: dict[str, Any]) -> None:
    hupu = match.setdefault("hupu", {})
    if item.get("_hupuMatchId"):
        hupu["matchId"] = str(item.get("_hupuMatchId"))
    hupu["status"] = str(item.get("_hupuStatus") or "")
    hupu["sourceUrl"] = str(item.get("_sourceUrl") or HUPU_MOBILE_SOCCER_URL)
    apply_hupu_metrics(hupu, item)
    remove_source_prefix(match, "https://m.hupu.com/soccerleagues/")
    append_source_once(match, hupu["sourceUrl"])
    remove_note(match, "marketNotes", "虎扑公开足球赛程页面用于近期赛程/赛果校验；不提供赔率或支持率。")
    append_note_once(match, "marketNotes", "虎扑公开足球赛程页面用于近期赛程/赛果校验，并展示公开热度/评分人数；不提供赔率或支持率。")


def apply_hupu_metrics(hupu: dict[str, Any], item: dict[str, Any]) -> None:
    if item.get("_hupuRatingCount") is not None:
        hupu["ratingCount"] = int(item["_hupuRatingCount"])
    if item.get("_hupuRatingText"):
        hupu["ratingText"] = str(item["_hupuRatingText"])
    if item.get("_hupuHeat") is not None:
        hupu["heat"] = int(item["_hupuHeat"])


def result_source_name(item: dict[str, Any]) -> str:
    if item.get("_source") == "wc-2026":
        return "wc-2026 公开比赛赔率页面"
    if item.get("_source") == "hupu":
        return "虎扑公开足球赛程页面"
    return "Sporttery 官方公开赛果接口"


def result_update_allowed(match: dict[str, Any]) -> bool:
    kickoff = parse_dashboard_kickoff(match)
    if not kickoff:
        return False
    return kickoff <= datetime.now(SHANGHAI) - timedelta(hours=2)


def parse_dashboard_kickoff(match: dict[str, Any]) -> datetime | None:
    text = str(match.get("kickoff") or "")
    found = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", text)
    if not found:
        return None
    year, month, day, hour, minute = map(int, found.groups())
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI)


def update_odds_vector(match: dict[str, Any], pool: dict[str, Any]) -> None:
    odds = match.get("odds")
    if not isinstance(odds, list) or len(odds) < 3:
        return
    values = [pool.get("home"), pool.get("draw"), pool.get("away")]
    for odd, value in zip(odds[:3], values, strict=False):
        if isinstance(value, (int, float)) and value > 0:
            odd["sporttery"] = value


def sporttery_pool(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "home": to_float(raw.get("h")),
        "draw": to_float(raw.get("d")),
        "away": to_float(raw.get("a")),
        "goalLine": str(raw.get("goalLine") or raw.get("goalLineValue") or ""),
        "lastUpdated": combine_update_time(raw),
    }


def correct_score_odds(raw: dict[str, Any]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for key, value in raw.items():
        match = re.fullmatch(r"s(\d{2})s(\d{2})", str(key))
        odd = to_float(value)
        if match and odd:
            home = int(match.group(1))
            away = int(match.group(2))
            scores[f"{home}-{away}"] = odd
    return scores


def combine_update_time(raw: dict[str, Any]) -> str:
    update_date = str(raw.get("updateDate") or "").strip()
    update_time = str(raw.get("updateTime") or "").strip()
    if update_date and update_time:
        return f"{update_date} {update_time}"
    return update_date or update_time


def parse_score(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*[:：-]\s*(\d+)\s*", value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def append_timeline_result(match: dict[str, Any], score: tuple[int, int], text: str) -> None:
    timeline = match.setdefault("timeline", [])
    title = f"{match.get('home')} {score[0]} - {score[1]} {match.get('away')}"
    if any(item.get("minute") == "FT" and item.get("title") == title for item in timeline if isinstance(item, dict)):
        return
    timeline.insert(0, {"minute": "FT", "title": title, "text": text})


def append_note_once(match: dict[str, Any], field: str, note: str) -> None:
    notes = match.setdefault(field, [])
    if note not in notes:
        notes.append(note)


def remove_note(match: dict[str, Any], field: str, note: str) -> None:
    notes = match.get(field)
    if isinstance(notes, list):
        match[field] = [item for item in notes if item != note]


def append_source_once(match: dict[str, Any], source: str) -> None:
    sources = match.setdefault("sources", [])
    if source not in sources:
        sources.append(source)


def remove_source_prefix(match: dict[str, Any], prefix: str) -> None:
    sources = match.get("sources")
    if isinstance(sources, list):
        match["sources"] = [source for source in sources if not str(source).startswith(prefix)]


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_sporttery(options: FetchOptions) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    today = datetime.now(SHANGHAI).date()
    begin = today - timedelta(days=options.days_back)
    end = today + timedelta(days=options.days_forward)
    calculator_items: list[dict[str, Any]] = []
    result_items: list[dict[str, Any]] = []
    panews_state: dict[str, Any] | None = None
    warnings: list[str] = []
    try:
        calculator = fetch_json(SPORTTERY_CALCULATOR_URL, {"channel": "c"}, options.timeout)
        calculator_items = iter_calculator_matches(calculator)
    except RuntimeError as exc:
        warnings.append(str(exc))

    try:
        results = fetch_json(
            SPORTTERY_RESULTS_URL,
            {
                "matchPage": 1,
                "pageSize": 100,
                "pageNo": 1,
                "matchBeginDate": begin.isoformat(),
                "matchEndDate": end.isoformat(),
                "isFix": 0,
                "pcOrWap": 1,
            },
            options.timeout,
        )
        result_items.extend(iter_result_matches(results))
    except RuntimeError as exc:
        warnings.append(str(exc))

    try:
        result_items.extend(parse_wc2026_results(fetch_text(WC2026_ODDS_URL, options.timeout)))
    except RuntimeError as exc:
        warnings.append(str(exc))

    try:
        result_items.extend(parse_hupu_schedules(fetch_text(HUPU_MOBILE_SOCCER_URL, options.timeout)))
    except RuntimeError as exc:
        warnings.append(str(exc))

    try:
        result_items.extend(parse_hupu_schedules(fetch_text(HUPU_MOBILE_SCHEDULE_URL, options.timeout)))
    except RuntimeError as exc:
        warnings.append(str(exc))

    try:
        panews_state = fetch_json(PANEWS_ARENA_STATE_URL, {}, options.timeout)
    except RuntimeError as exc:
        warnings.append(str(exc))

    if warnings:
        print(json.dumps({"warnings": warnings}, ensure_ascii=False), file=sys.stderr)

    return calculator_items, result_items, panews_state


def parse_args() -> FetchOptions:
    parser = argparse.ArgumentParser(description="Refresh dashboard data from public football sources.")
    parser.add_argument("--file", type=Path, default=DATA_FILE, help="Path to web/data/matches.js")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")
    parser.add_argument("--days-back", type=int, default=7, help="How many days of results to request")
    parser.add_argument("--days-forward", type=int, default=2, help="How many future days of results to request")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds")
    args = parser.parse_args()
    return FetchOptions(args.file, args.dry_run, args.days_back, args.days_forward, args.timeout)


def main() -> int:
    options = parse_args()
    payload = load_payload(options.data_file)
    calculator_items, result_items, panews_state = fetch_sporttery(options)
    if panews_state:
        payload["_panewsArenaState"] = panews_state
    changes = update_matches(payload, calculator_items, result_items)

    summary = {
        "calculatorMatches": len(calculator_items),
        "resultMatches": len(result_items),
        "panewsMatches": len((panews_state or {}).get("matches") or []),
        "panewsTrades": len(((panews_state or {}).get("tradeBook") or {}).get("trades") or []),
        "changes": changes,
        "dryRun": options.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not changes:
        return 2

    if not options.dry_run:
        write_payload(options.data_file, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
