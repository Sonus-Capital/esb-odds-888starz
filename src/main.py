#!/usr/bin/env python3
"""
888starz Esports Odds Scraper — v1.3 (2026-06-17)

Schema: SCHEMA-LOCK-2026-06-07.md

Changes in v1.3:
  - 888starz public cyber-api endpoints now return HTTP 400, so the actor no
    longer calls them.
  - Instead, we navigate to each esports discipline page where the SSR HTML
    embeds the full app state as `window.__CYBER_APP__`, then extract the
    pre-rendered game list and moneyline odds.
  - Keeps Playwright + proxy support (residential IP in an allowlisted country).

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
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

# Map known slugs to a human discipline name when the embedded state doesn't.
FALLBACK_NAME_BY_SLUG: dict[str, str] = {
    "cs-2": "CS 2",
    "dota-2": "Dota 2",
    "league-of-legends": "League of Legends",
    "valorant": "Valorant",
    "starcraft-ii": "Starcraft 2",
    "rainbow-six": "Rainbow Six",
    "call-of-duty": "Call of Duty",
    "overwatch": "Overwatch",
    "pubg": "PUBG",
    "honor-of-kings": "Honor of Kings",
    "arena-of-valor": "Arena of Valor",
    "crossfire": "Crossfire",
    "heroes-of-might-and-magic-iii": "Heroes of Might and Magic III",
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


def extract_record(item: dict[str, Any], sport_name: str, now: str) -> Optional[Dict[str, Any]]:
    """Turn one `gamesAndLigas` node into a SCHEMA-LOCK record."""
    game = item.get("game") or {}
    liga = item.get("liga") or game.get("liga") or {}

    team1 = ((game.get("opponent1") or {}).get("fullName") or "").strip()
    team2 = ((game.get("opponent2") or {}).get("fullName") or "").strip()
    if not team1 or not team2:
        return None

    liga_name = liga.get("name", "") if isinstance(liga, dict) else ""
    match_id = game.get("id", "")
    match_url = f"{BASE_URL}/en/esports/{match_id}" if match_id else ""

    start_ts = game.get("startTs")
    if isinstance(start_ts, (int, float)) and start_ts > 1_000_000_000:
        start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    else:
        start_time = ""

    p1 = p2 = p_draw = None
    # Use embedded eventGroups. Match-winner is groupId=1 (types 1=W1, 3=W2, 2=Draw).
    event_groups = game.get("eventGroups") or []
    for eg in event_groups:
        if eg.get("groupId") != 1 and eg.get("shortGroupId") != 1:
            continue
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
        break

    if p1 is None or p2 is None:
        return None

    return {
        "bookmaker": "888starz",
        "game_raw": sport_name,
        "game": normalise_game(sport_name),
        "tournament_name": liga_name,
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


async def list_disciplines(page) -> list[dict[str, Any]]:
    """Read the all-disciplines list embedded in the current page state."""
    js = """
        () => {
            const app = window.__CYBER_APP__;
            const state = app && app.state;
            if (!state) return [];
            return state['$scyberAllDisciplines'] || [];
        }
    """
    return await page.evaluate(js) or []


async def fetch_games_for_discipline(
    page, actor, slug: str, live: bool
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Navigate to a discipline page and return its gamesAndLigas nodes."""
    kind = "live" if live else "line"
    url = f"{BASE_URL}/en/esports/real/{slug}/{kind}"
    actor.log.info(f"  Fetching {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        # Give the inline state a moment to settle, although it is SSR.
        await asyncio.sleep(0.5)
    except Exception as exc:
        actor.log.warning(f"  Failed to load {url}: {exc}")
        return [], {}, {}

    js = f"""
        () => {{
            const app = window.__CYBER_APP__;
            const state = app && app.state;
            if (!state) return {{games: [], subSport: {{}}, sport: {{}}}};
            const gamesKey = '$scyberSportGamesundefined';
            const data = (state[gamesKey] && state[gamesKey]['{kind}']) || {{}};
            return {{
                games: data.gamesAndLigas || [],
                subSport: data.subSport || {{}},
                sport: data.sport || {{}}
            }};
        }}
    """
    try:
        result = await page.evaluate(js)
    except Exception as exc:
        actor.log.warning(f"  Failed to evaluate state for {url}: {exc}")
        return [], {}, {}

    games = result.get("games", [])
    actor.log.info(f"  [{kind}] {slug}: {len(games)} raw nodes")
    return games, result.get("subSport", {}), result.get("sport", {})


async def main() -> None:
    async with Actor() as actor:
        now = datetime.now(timezone.utc).isoformat()
        input_data = await actor.get_input() or {}

        hub_slugs = [h.strip().lower() for h in (input_data.get("hubSlugs") or [])]
        max_hubs = int(input_data.get("maxHubs") or 0)
        include_line = bool(input_data.get("includeLine", True))
        include_live = bool(input_data.get("includeLive", True))
        proxy_url = input_data.get("proxyUrl") or ""
        page_timeout = float(input_data.get("requestTimeout") or 60.0)
        headless = bool(input_data.get("headless", True))

        actor.log.info(
            f"888starz scraper v1.3 | proxy={bool(proxy_url)} headless={headless}"
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

            # Load the generic esports hub so we can read the discipline list.
            hub_url = f"{BASE_URL}/en/esports/line"
            actor.log.info(f"Loading hub {hub_url}")
            await page.goto(
                hub_url,
                wait_until="domcontentloaded",
                timeout=int(page_timeout * 1000),
            )
            await asyncio.sleep(1)

            title = await page.title()
            actor.log.info(f"Page title: {title!r} url: {page.url!r}")

            disciplines = await list_disciplines(page)
            if not disciplines:
                actor.log.warning("No disciplines found in embedded state — aborting.")
                await browser.close()
                return

            actor.log.info(f"Disciplines discovered: {len(disciplines)}")

            # Build slug list from embedded allDisciplines.
            wanted: list[dict[str, Any]] = []
            for disc in disciplines:
                slug = disc.get("nameForUrl", "")
                if not slug:
                    continue
                if hub_slugs and slug not in hub_slugs:
                    continue
                wanted.append(disc)

            if max_hubs > 0:
                wanted = wanted[:max_hubs]

            for disc in wanted:
                slug = disc["nameForUrl"]
                sport_name = disc.get("name") or FALLBACK_NAME_BY_SLUG.get(slug, slug)

                if include_line:
                    games, sub_sport, _ = await fetch_games_for_discipline(
                        page, actor, slug, live=False
                    )
                    actual_sport_name = str(sub_sport.get("name") or sport_name)
                    for item in games:
                        rec = extract_record(item, actual_sport_name, now)
                        if not rec:
                            continue
                        key = f"line:{rec['team1'].lower()}||{rec['team2'].lower()}"
                        if key in seen:
                            continue
                        seen.add(key)
                        all_records.append(rec)

                if include_live:
                    games, sub_sport, _ = await fetch_games_for_discipline(
                        page, actor, slug, live=True
                    )
                    actual_sport_name = str(sub_sport.get("name") or sport_name)
                    for item in games:
                        rec = extract_record(item, actual_sport_name, now)
                        if not rec:
                            continue
                        key = f"live:{rec['team1'].lower()}||{rec['team2'].lower()}"
                        if key in seen:
                            continue
                        seen.add(key)
                        all_records.append(rec)

            await browser.close()

        actor.log.info(f"Grand total: {len(all_records)} records")

        for rec in all_records:
            await actor.push_data(rec)

        await actor.push_data({
            "_meta": True,
            "bookmaker": "888starz",
            "records_total": len(all_records),
            "method": "playwright_ssr_state",
            "scraped_at": now,
        })


if __name__ == "__main__":
    asyncio.run(main())
