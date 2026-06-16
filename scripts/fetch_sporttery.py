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


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "web" / "data" / "matches.js"
ASSIGNMENT_RE = re.compile(r"^\s*window\.WORLD_CUP_MATCHES\s*=\s*(\{.*\});?\s*$", re.S)
SPORTTERY_CALCULATOR_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry"
SPORTTERY_RESULTS_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
SPORTTERY_SOURCE_URL = "https://www.sporttery.cn/jc/zqsgkj/"
WC2026_ODDS_URL = "https://wc-2026.com/world-cup-odds/"
HUPU_MOBILE_SOCCER_URL = "https://m.hupu.com/soccer"
HUPU_MOBILE_SCHEDULE_URL = "https://m.hupu.com/soccer/schedule"
SHANGHAI = ZoneInfo("Asia/Shanghai")


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
    request = urllib.request.Request(
        f"{url}?{query}",
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

    for match in payload.get("matches") or []:
        before = json.dumps(match, ensure_ascii=False, sort_keys=True)
        key = dashboard_key(match)
        calc_item = calculator_by_id.get(sporttery_id(match)) or calculator_by_key.get(key)
        result_item = result_by_id.get(sporttery_id(match)) or result_by_key.get(key)
        hupu_item = hupu_by_key.get(key)

        if calc_item and is_same_dashboard_match(match, calc_item):
            apply_calculator_item(match, calc_item)

        if hupu_item and is_same_dashboard_match(match, hupu_item):
            apply_hupu_item(match, hupu_item)

        if result_item and is_same_dashboard_match(match, result_item):
            apply_result_item(match, result_item)

        after = json.dumps(match, ensure_ascii=False, sort_keys=True)
        if after != before:
            changes.append(f"{match.get('id')} {match.get('home')} vs {match.get('away')}")

    if changes:
        now = datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M CST")
        payload["lastUpdated"] = f"{now}（自动刷新 Sporttery 公开赛果/赔率 + 虎扑近期赛程/热度；未匹配数据保持原状）"
        source = str(payload.get("sourceName") or "").replace("虎扑赛程校验", "虎扑赛程/热度校验")
        if "Sporttery 自动更新" not in source:
            payload["sourceName"] = f"{source}；Sporttery 自动更新" if source else "Sporttery 自动更新"
        else:
            payload["sourceName"] = source
        if "虎扑赛程/热度校验" not in payload["sourceName"]:
            payload["sourceName"] = f"{payload['sourceName']}；虎扑赛程/热度校验"

    return changes


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

    if any(str(item.get(key) or "").strip() for key in ("h", "d", "a")):
        pool = {
            "home": to_float(item.get("h")),
            "draw": to_float(item.get("d")),
            "away": to_float(item.get("a")),
            "goalLine": str(item.get("goalLine") or ""),
            "lastUpdated": str(item.get("matchDate") or ""),
        }
        update_odds_vector(match, pool)

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


def fetch_sporttery(options: FetchOptions) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today = datetime.now(SHANGHAI).date()
    begin = today - timedelta(days=options.days_back)
    end = today + timedelta(days=options.days_forward)
    calculator_items: list[dict[str, Any]] = []
    result_items: list[dict[str, Any]] = []
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

    if warnings:
        print(json.dumps({"warnings": warnings}, ensure_ascii=False), file=sys.stderr)

    return calculator_items, result_items


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
    calculator_items, result_items = fetch_sporttery(options)
    changes = update_matches(payload, calculator_items, result_items)

    summary = {
        "calculatorMatches": len(calculator_items),
        "resultMatches": len(result_items),
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
