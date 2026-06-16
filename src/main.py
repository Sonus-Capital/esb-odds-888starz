#!/usr/bin/env python3
"""
888starz Esports Odds Scraper — v1.2 (2026-06-16)

Schema: SCHEMA-LOCK-2026-06-07.md
Changes in v1.2:
  - Switched from httpx to Playwright page.evaluate(fetch...) so requests reuse
    the browser's live session/cookies, matching the 1xBet/Vavada pattern.
  - Proxy support via `proxyUrl` or built-in Apify proxy.
  - Output aligned with SCHEMA-LOCK keys.

Notes:
  - 888starz only accepts players/requests from a restricted country allowlist.
    The proxy supplied in `proxyUrl` must terminate in one of those countries.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlparse

from apify import Actor
from playwright.async_api import async_playwright

from .normalise import normalise_game

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("888starz-scraper")

BASE_URL = "https://888starz.bet"
PARAMS = "cfView=3&fcountry=12&gr=789&lng=en&ref=233"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

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


def parse_proxy_url(proxy_url: str) -> dict[str, str] | None:
    """Return Playwright proxy dict from http://user:pass@host:port URL."""
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return None
    proxy: dict[str, str] = {
        "server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}",
    }
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def extract_record(game: dict[str, Any], sport_name: str, now: str) -> Optional[Dict[str, Any]]:
    team1 = ((game.get("opponent1") or {}).get("fullName") or "").strip()
    team2 = ((game.get("opponent2") or {}).get("fullName") or "").strip()
    if not team1 or not team2:
        return None

    liga = (game.get("liga") or {}).get("name", "")
    match_id = game.get("id", "")
    match_url = f"{BASE_URL}/en/esports/{match_id}" if match_id else ""

    start_ts = game.get("startTs")
    if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
        start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    else:
        start_time = ""

    p1 = p2 = p_draw = None
    for eg in (game.get("eventGroups") or [])[:1]:
        for outcome_list in eg.get("events", []):
            for o in outcome_list:
                t = o.get("type")
                try:
                    odds = float(o.get("cf", 0))
                except (TypeError, ValueError):
                    continue
                if not (1.01 <= odds <= 500):
                    continue
                if t == 1:
                    p1 = odds
                elif t == 3:
                    p2 = odds
                elif t == 2:
                    p_draw = odds

    if p1 is None or p2 is None:
        return None

    return {
        "bookmaker": "888starz",
        "game_raw": sport_name,
        "game": normalise_game(sport_name),
        "tournament_name": liga,
        "team1": team1,
        "team2": team2,
        "match_start_time": start_time,
        "match_url": match_url,
        "market_name": "Match Winner",
        "price_team1": p1,
        "price_team2": p2,
        "price_draw": p_draw,
        "scraped_at": now,
    }


async def goto_esports(page, actor, timeout: float) -> None:
    actor.log.info("Loading 888starz esports hub...")
    await page.goto(
        f"{BASE_URL}/en/esports",
        wait_until="domcontentloaded",
        timeout=int(timeout * 1000),
    )
    actor.log.info("Waiting for CF challenge + page hydration (12s)...")
    await asyncio.sleep(12)
    title = await page.title()
    actor.log.info(f"Page title: {title!r}")


async def fetch_subsports(page, actor, live: bool) -> List[int]:
    endpoint = (
        "/cyber-api/mainfeedlive/web/cyber/v3/leftmenu/real"
        if live
        else "/cyber-api/mainfeedline/web/cyber/v3/leftmenu/real"
    )
    try:
        raw = await page.evaluate(
            f"""
            async () => {{
                const r = await fetch('{endpoint}?{PARAMS}');
                return await r.text();
            }}
            """
        )
        data = json.loads(raw)
        ids = [item["subSportId"] for item in data if "subSportId" in item]
        actor.log.info(f"{'Live' if live else 'Prematch'} subSports from menu: {ids}")
        return ids
    except Exception as exc:
        actor.log.warning(f"Could not fetch {'live' if live else 'prematch'} leftmenu: {exc}")
        return []


async def fetch_games(page, actor, sub_id: int, live: bool) -> dict[str, Any]:
    endpoint = (
        "/cyber-api/mainfeedlive/web/cyber/v3/gamesBySport/real"
        if live
        else "/cyber-api/mainfeedline/web/cyber/v3/gamesBySport/real"
    )
    raw = await page.evaluate(
        f"""
        async () => {{
            const r = await fetch('{endpoint}?{PARAMS}&subSport={sub_id}');
            return await r.text();
        }}
        """
    )
    try:
        return json.loads(raw)
    except Exception as exc:
        actor.log.warning(f"Failed parsing {'live' if live else 'prematch'} subSport={sub_id}: {exc}")
        return {}


async def main() -> None:
    async with Actor() as actor:
        now = datetime.now(timezone.utc).isoformat()
        input_data = await actor.get_input() or {}

        hub_slugs = [h.strip().lower() for h in (input_data.get("hubSlugs") or [])]
        max_hubs = int(input_data.get("maxHubs") or 0)
        include_line = bool(input_data.get("includeLine", True))
        include_live = bool(input_data.get("includeLive", True))
        proxy_url = input_data.get("proxyUrl") or ""
        page_timeout = float(input_data.get("requestTimeout") or 30.0)
        headless = bool(input_data.get("headless", True))

        if hub_slugs:
            wanted_subs = {SUB_SPORT_MAP[s] for s in hub_slugs if s in SUB_SPORT_MAP}
        else:
            wanted_subs = set(SUB_SPORT_MAP.values())

        actor.log.info(
            f"888starz scraper v1.2 | proxy={bool(proxy_url)} headless={headless}"
        )

        proxy = parse_proxy_url(proxy_url)

        all_records: List[Dict[str, Any]] = []
        seen: set[str] = set()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=headless,
                proxy=cast(Any, proxy) if proxy else None,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await context.new_page()

            await goto_esports(page, actor, page_timeout)

            sub_sports_to_scrape: List[int] = []
            if include_line:
                sub_sports_to_scrape.extend(await fetch_subsports(page, actor, live=False))
            if include_live:
                sub_sports_to_scrape.extend(await fetch_subsports(page, actor, live=True))

            # Deduplicate and filter to wanted subs
            sub_sports_to_scrape = sorted(set(sub_sports_to_scrape))
            if wanted_subs:
                sub_sports_to_scrape = [s for s in sub_sports_to_scrape if s in wanted_subs]
            if max_hubs > 0:
                sub_sports_to_scrape = sub_sports_to_scrape[:max_hubs]

            for ss_id in sub_sports_to_scrape:
                sport_name = NAME_BY_SUB_SPORT.get(ss_id, f"ss_{ss_id}")

                if include_line:
                    data = await fetch_games(page, actor, ss_id, live=False)
                    games = data.get("games", [])
                    sport_name = (data.get("subSport") or {}).get("name", sport_name)
                    t_recs = 0
                    for g in games:
                        rec = extract_record(g, sport_name, now)
                        if not rec:
                            continue
                        key = f"{rec['team1'].lower()}||{rec['team2'].lower()}"
                        if key in seen:
                            continue
                        seen.add(key)
                        all_records.append(rec)
                        t_recs += 1
                    if t_recs:
                        actor.log.info(f"  [line] {sport_name}: {t_recs}")

                if include_live:
                    data = await fetch_games(page, actor, ss_id, live=True)
                    games = data.get("games", [])
                    sport_name = (data.get("subSport") or {}).get("name", sport_name)
                    t_recs = 0
                    for g in games:
                        rec = extract_record(g, sport_name, now)
                        if not rec:
                            continue
                        key = f"{rec['team1'].lower()}||{rec['team2'].lower()}"
                        if key in seen:
                            continue
                        seen.add(key)
                        all_records.append(rec)
                        t_recs += 1
                    if t_recs:
                        actor.log.info(f"  [live] {sport_name}: {t_recs}")

            await browser.close()

        actor.log.info(f"Grand total: {len(all_records)} records")

        for rec in all_records:
            await actor.push_data(rec)

        await actor.push_data({
            "_meta": True,
            "bookmaker": "888starz",
            "records_total": len(all_records),
            "method": "playwright_cyber_api",
            "scraped_at": now,
        })


if __name__ == "__main__":
    asyncio.run(main())
