# 888starz esports odds scraper

Scrapes public esports match-winner odds from 888starz via Playwright + the SSR-embedded app state (`window.__CYBER_APP__`).

## Why Playwright + SSR state?

The public `cyber-api` endpoints started returning HTTP 400 in mid-June 2026
(`feed/InvalidQueryParametersException`). However, 888starz's own web UI still
loads each cyber/esports discipline with the full game and odds data rendered
inside the initial HTML as `window.__CYBER_APP__`. This actor navigates to each
discipline page using a real browser (with optional residential proxy), reads the
embedded state, and emits match-winner records.

## Source

- Entry/hub: `https://888starz.bet/en/esports/line` — gives the list of disciplines.
- Per-discipline line: `https://888starz.bet/en/esports/real/{slug}/line`
- Per-discipline live: `https://888starz.bet/en/esports/real/{slug}/live`

Supported `hubSlugs` include: `cs-2`, `dota-2`, `league-of-legends`, `valorant`, `rainbow-six`, `starcraft-2`, `overwatch`, `honor-of-kings`, `call-of-duty`, `pubg`, `arena-of-valor`, `crossfire`, `heroes-of-might-and-magic-iii`.

## Output fields (SCHEMA-LOCK-2026-06-07)

- `bookmaker` = `888starz`
- `game_raw`, `game`
- `tournament_name`
- `team1`, `team2`
- `match_start_time`, `match_url`
- `market_name` = `Match Winner`
- `price_team1`, `price_team2`, `price_draw`

Optional input fields:

- `proxyUrl`: an HTTP proxy URL (`http://user:pass@host:port`) to route Playwright through.
- `hubSlugs`: list of game slugs to restrict scraping.
- `includeLine` / `includeLive`
- `headless`: set to `false` for headed debugging.

888starz only accepts traffic from a restricted set of countries, so the proxy must terminate in one of those markets.
