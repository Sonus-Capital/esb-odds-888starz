"""Apify actor: 888starz esports odds via public DOM/text scraping.

Implementation notes
--------------------
* 888starz shows esports odds publicly; no login required.
* Start at /en/esports/real/all to discover game hubs (/en/esports/real/{game}).
* Each game hub lists events as links; link text contains game, league,
  team names, scores, and W1/W2 match-winner odds.
* We parse the link text and URL, normalise game names, and push records.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from apify import Actor
from playwright.async_api import async_playwright, Page

from .normalise import normalise_game

STARZ_BASE = "https://888starz.bet/en/esports/real"
ALL_URL = f"{STARZ_BASE}/all"

GAME_LABELS = [
    ("cs-2", "Counter-Strike 2"),
    ("cs-go", "CS:GO"),
    ("dota-2", "Dota 2"),
    ("league-of-legends", "League of Legends"),
    ("mobile-legends", "Mobile Legends"),
    ("valorant", "Valorant"),
    ("starcraft-2", "Starcraft 2"),
    ("rainbow-six", "Rainbow Six"),
    ("call-of-duty", "Call of Duty"),
    ("overwatch", "Overwatch"),
    ("pubg", "PUBG"),
    ("honor-of-kings", "Honor of Kings"),
    ("arena-of-valor", "Arena of Valor"),
    ("crossfire", "Crossfire"),
    ("fifa", "FIFA"),
    ("efootball", "eFootball"),
    ("basketball", "Basketball"),
]

DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s+/\s+(\d{2}:\d{2})")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug_to_name(slug: str) -> str:
    return " ".join(slug.replace("_", " ").strip("-").split("-")).title()


def game_name_from_slug(game_slug: str) -> str:
    for slug, display in GAME_LABELS:
        if game_slug.lower() == slug or game_slug.lower().startswith(slug + "-"):
            return display
    if game_slug.lower().startswith("cs"):
        return "Counter-Strike 2"
    return slug_to_name(game_slug)


def extract_league_name(league_slug: str, game_slug: str) -> str:
    for slug, _ in GAME_LABELS:
        if league_slug.lower().startswith(slug + "-"):
            league_slug = league_slug[len(slug) + 1:]
            break
    return slug_to_name(league_slug)


def split_team_slug(team_slug: str) -> tuple[str, str]:
    if "--" in team_slug:
        a, b = team_slug.split("--", 1)
        return a, b
    parts = team_slug.split("-")
    if len(parts) >= 2:
        mid = len(parts) // 2
        return "-".join(parts[:mid]), "-".join(parts[mid:])
    return (parts[0] if parts else ""), ""


def parse_event_url(path: str) -> dict[str, Any] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 7 or parts[5] not in ("live", "line"):
        return None
    league_parts = parts[4].split("-", 1)
    event_parts = parts[6].split("-", 1)
    if len(league_parts) != 2 or len(event_parts) != 2:
        return None
    return {
        "game": parts[3],
        "league_id": league_parts[0],
        "league_slug": league_parts[1],
        "kind": parts[5],
        "event_id": event_parts[0],
        "team_slug": event_parts[1],
        "is_live": parts[5] == "live",
    }


def parse_event_text(text: str) -> dict[str, Any]:
    """Extract W1/W2 odds, scores, and start time from the visible link text."""
    w1 = w2 = None
    score_a = score_b = None
    start_time: str | None = None

    tokens = text.split()
    for i, tok in enumerate(tokens):
        if i + 1 < len(tokens):
            t = tok.upper()
            nxt = tokens[i + 1]
            if t == "W1":
                try:
                    w1 = float(nxt)
                except Exception:
                    pass
            elif t == "W2":
                try:
                    w2 = float(nxt)
                except Exception:
                    pass

    # Find first pair of adjacent integers <= 2 digits as score
    for i in range(len(tokens) - 1):
        if tokens[i].isdigit() and len(tokens[i]) <= 2 and tokens[i + 1].isdigit() and len(tokens[i + 1]) <= 2:
            score_a = int(tokens[i])
            score_b = int(tokens[i + 1])
            break

    date_m = DATE_RE.search(text)
    if date_m:
        try:
            dt = datetime.strptime(f"{date_m.group(1)} {date_m.group(2)}", "%d/%m/%Y %H:%M")
            start_time = dt.replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass

    odds = [o for o in [w1, w2] if o is not None]
    return {
        "odds": odds,
        "score_a": score_a,
        "score_b": score_b,
        "start_time": start_time,
    }


def build_record(parsed: dict[str, Any], text: str) -> dict[str, Any] | None:
    game_slug = parsed["game"]
    league_slug = parsed["league_slug"]
    game = normalise_game(game_name_from_slug(game_slug))
    league = extract_league_name(league_slug, game_slug)

    team_a_slug, team_b_slug = split_team_slug(parsed["team_slug"])
    team_a = slug_to_name(team_a_slug)
    team_b = slug_to_name(team_b_slug)

    info = parse_event_text(text)

    markets = []
    if len(info["odds"]) == 2:
        markets = [
            {"market_id": "match_winner", "outcome_id": "H", "team": team_a, "odds": info["odds"][0]},
            {"market_id": "match_winner", "outcome_id": "A", "team": team_b, "odds": info["odds"][1]},
        ]

    return {
        "event_id": parsed["event_id"],
        "brand": "888starz",
        "sport": "Esports",
        "game": game,
        "league": league,
        "team_a": team_a,
        "team_b": team_b,
        "score_a": info["score_a"],
        "score_b": info["score_b"],
        "is_live": parsed["is_live"],
        "start_time": info["start_time"],
        "markets": markets,
        "event_url": f"https://888starz.bet{parsed.get('_path', '')}",
        "scraped_at": now_iso(),
    }


async def accept_cookies(page: Page) -> None:
    for selector in [
        "button:has-text('Accept')",
        "button:has-text('I agree')",
        "button:has-text('Agree')",
    ]:
        with suppress(Exception):
            await page.locator(selector).first.click(timeout=3000)
            await asyncio.sleep(0.5)


async def safe_goto(page: Page, url: str) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    except Exception as exc:
        Actor.log.warning(f"Navigation to {url} ended with {exc}; continuing anyway")
    with suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=15000)


async def extract_game_hubs(page: Page) -> list[dict[str, str]]:
    hrefs = await page.eval_on_selector_all("a[href]", "elements => elements.map(a => a.href)")
    seen: set[str] = set()
    hubs: list[dict[str, str]] = []
    for href in hrefs:
        path = urlparse(href).path
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "en" and parts[1] == "esports" and parts[2] == "real" and parts[3] != "all":
            slug = parts[3]
            if slug not in seen:
                seen.add(slug)
                hubs.append({"url": href, "slug": slug, "name": slug_to_name(slug)})
    return hubs


async def extract_events_from_hub(page: Page, hub_url: str) -> list[dict[str, Any]]:
    await safe_goto(page, hub_url)
    await accept_cookies(page)

    rows = await page.eval_on_selector_all(
        "a[href]",
        r"""elements => {
            const re = /^https:\/\/888starz\.bet\/en\/esports\/real\/[^/]+\/\d+-[^/]+\/(?:line|live)\/\d+-[^/]+$/;
            const seen = new Set();
            return elements
                .map(a => ({href: a.href, text: a.innerText.trim().replace(/\s+/g, ' ')}))
                .filter(x => re.test(x.href))
                .filter(x => { if (seen.has(x.href)) return false; seen.add(x.href); return true; });
        }""",
    )

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows:
        href = row["href"]
        text = row["text"]
        parsed = parse_event_url(urlparse(href).path)
        if not parsed or parsed["event_id"] in seen_ids:
            continue
        seen_ids.add(parsed["event_id"])
        parsed["_path"] = urlparse(href).path
        rec = build_record(parsed, text)
        if rec:
            records.append(rec)
    return records


async def main() -> None:
    async with Actor() as actor:
        input_data = await actor.get_input() or {}
        max_hubs = int(input_data.get("maxHubs") or 0)
        hub_slugs = input_data.get("hubSlugs") or []
        headless = not bool(input_data.get("headful"))

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            )
            page = await context.new_page()

            await safe_goto(page, ALL_URL)
            await accept_cookies(page)

            hubs = await extract_game_hubs(page)
            if hub_slugs:
                hubs = [h for h in hubs if h["slug"] in hub_slugs]
            if max_hubs > 0:
                hubs = hubs[:max_hubs]

            Actor.log.info(f"Discovered {len(hubs)} game hubs: {[h['slug'] for h in hubs]}")

            total = 0
            for hub in hubs:
                try:
                    records = await extract_events_from_hub(page, hub["url"])
                    Actor.log.info(f"Hub {hub['slug']}: {len(records)} events")
                    for rec in records:
                        await actor.push_data(rec)
                    total += len(records)
                except Exception as exc:
                    Actor.log.exception(f"Failed hub {hub['slug']}: {exc}")

            await context.close()
            await browser.close()

            Actor.log.info(f"Finished; pushed {total} events total")


if __name__ == "__main__":
    asyncio.run(main())
