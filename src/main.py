"""Apify actor: 888starz esports odds via public cyber feed API.

Implementation notes
--------------------
* Uses the unauthenticated cyber endpoints discovered on 888starz.bet:
  - line: /cyber-api/mainfeedline/web/cyber/v3/gamesBySport/real
  - live: /cyber-api/mainfeedlive/web/cyber/v3/gamesBySport/real
  - menu: /cyber-api/mainfeedline/web/cyber/v3/leftmenu/real
* subSport IDs are mapped below; W1/W2 odds live in eventGroups[groupId=1].
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from apify import Actor
import httpx

from .normalise import normalise_game

BASE_URL = "https://888starz.bet"

SUB_SPORT_MAP: dict[str, int] = {
    "dota-2": 1,
    "league-of-legends": 2,
    "starcraft-2": 4,
    "overwatch": 11,
    "honor-of-kings": 14,
    "rainbow-six": 15,
    "valorant": 27,
    "cs-2": 46,
}

NAME_BY_SUB_SPORT: dict[int, str] = {
    1: "Dota 2",
    2: "League of Legends",
    4: "Starcraft 2",
    11: "Overwatch",
    14: "Honor of Kings",
    15: "Rainbow Six",
    27: "Valorant",
    46: "Counter-Strike 2",
}

COMMON_PARAMS = {
    "fcountry": "12",
    "gr": "789",
    "lng": "en",
    "ref": "233",
    "cfView": "3",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://888starz.bet/en/esports/real/cs-2/line",
    "Origin": "https://888starz.bet",
    "Connection": "keep-alive",
}

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_match_winner(game: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (home_odds, away_odds) from eventGroups groupId 1."""
    home: float | None = None
    away: float | None = None
    for group in game.get("eventGroups", []):
        if group.get("groupId") != 1:
            continue
        events = group.get("events", [])
        if len(events) >= 1 and events[0]:
            home = events[0][0].get("cf")
        if len(events) >= 2 and events[1]:
            away = events[1][0].get("cf")
        break
    return home, away


def build_record(game: dict[str, Any], is_live: bool) -> dict[str, Any] | None:
    opp1 = game.get("opponent1", {})
    opp2 = game.get("opponent2", {})
    team_a = opp1.get("fullName") or (opp1.get("opps") or [{}])[0].get("name")
    team_b = opp2.get("fullName") or (opp2.get("opps") or [{}])[0].get("name")
    if not team_a or not team_b:
        return None

    liga = game.get("liga", {})
    league = liga.get("name", "")
    sub_id = game.get("subSport", {}).get("id") if isinstance(game.get("subSport"), dict) else None
    game_name = normalise_game(NAME_BY_SUB_SPORT.get(sub_id, "Esports"))

    start_ts = game.get("startTs")
    start_time: str | None = None
    if start_ts:
        try:
            start_time = datetime.fromtimestamp(int(start_ts), tz=timezone.utc).isoformat()
        except Exception:
            pass

    home_odds, away_odds = extract_match_winner(game)
    if home_odds is None or away_odds is None:
        return None

    info = game.get("matchInfoObj", {})

    return {
        "event_id": str(game.get("id", "")),
        "const_id": str(game.get("constId", "")),
        "brand": "888starz",
        "sport": "Esports",
        "game": game_name,
        "league": league,
        "team_a": team_a,
        "team_b": team_b,
        "is_live": is_live,
        "start_time": start_time,
        "stage": info.get("tournamentStage"),
        "markets": [
            {"market_id": "match_winner", "outcome_id": "H", "team": team_a, "odds": home_odds},
            {"market_id": "match_winner", "outcome_id": "A", "team": team_b, "odds": away_odds},
        ],
        "scraped_at": now_iso(),
    }


async def fetch_games(client: httpx.AsyncClient, endpoint: str, sub_sport: int, timeout: float) -> list[dict[str, Any]]:
    url = urljoin(BASE_URL, endpoint)
    params = {**COMMON_PARAMS, "subSport": str(sub_sport)}
    r = await client.get(url, params=params, headers=COMMON_HEADERS, timeout=timeout)
    Actor.log.info(f"{endpoint} subSport={sub_sport} -> HTTP {r.status_code}")
    if r.status_code >= 400:
        Actor.log.warning(f"Body: {r.text[:500]}")
        r.raise_for_status()
    data = r.json()
    games = data.get("games", [])
    # enrich each game with subSport since live endpoint doesn't always include it
    for g in games:
        if not isinstance(g.get("subSport"), dict):
            g["subSport"] = {"id": sub_sport, "name": NAME_BY_SUB_SPORT.get(sub_sport, "Esports")}
    return games


async def main() -> None:
    async with Actor() as actor:
        input_data = await actor.get_input() or {}
        hub_slugs = [h.strip().lower() for h in (input_data.get("hubSlugs") or [])]
        max_hubs = int(input_data.get("maxHubs") or 0)
        include_live = bool(input_data.get("includeLive", True))
        include_line = bool(input_data.get("includeLine", True))
        proxy_url = input_data.get("proxyUrl") or None
        timeout = float(input_data.get("requestTimeout") or 45.0)

        if hub_slugs:
            hubs = [SUB_SPORT_MAP[s] for s in hub_slugs if s in SUB_SPORT_MAP]
        else:
            hubs = list(SUB_SPORT_MAP.values())
        if max_hubs > 0:
            hubs = hubs[:max_hubs]

        Actor.log.info(f"888starz feed actor: hubs={hubs}")

        proxies = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None
        client = httpx.AsyncClient(
            headers=COMMON_HEADERS,
            proxy=proxy_url,
            http2=False,
            follow_redirects=True,
        )

        total = 0
        try:
            for sub_id in hubs:
                games: list[dict[str, Any]] = []
                if include_line:
                    try:
                        games += await fetch_games(client, "/cyber-api/mainfeedline/web/cyber/v3/gamesBySport/real", sub_id, timeout)
                    except Exception as exc:
                        Actor.log.warning(f"line subSport={sub_id} failed: {exc}")
                if include_live:
                    try:
                        games += await fetch_games(client, "/cyber-api/mainfeedlive/web/cyber/v3/gamesBySport/real", sub_id, timeout)
                    except Exception as exc:
                        Actor.log.warning(f"live subSport={sub_id} failed: {exc}")

                seen: set[str] = set()
                for game in games:
                    key = str(game.get("id", ""))
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rec = build_record(game, is_live=game.get("kind") == 1 or "mainfeedlive" in getattr(game, "_endpoint", ""))
                    if rec:
                        await actor.push_data(rec)
                        total += 1
                Actor.log.info(f"subSport={sub_id}: pushed {len(seen)} unique games")
        finally:
            await client.aclose()

        Actor.log.info(f"Finished; pushed {total} records")


if __name__ == "__main__":
    asyncio.run(main())
