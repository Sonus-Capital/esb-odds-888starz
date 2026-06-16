# 888starz esports odds scraper

Scrapes public esports match-winner odds from 888starz via Playwright + the unauthenticated cyber feed API.

## Why Playwright?

Raw `httpx` requests to 888starz's cyber feed return `400 InvalidQueryParametersException` from cloud IPs. Letting the official SPA load inside Chromium establishes the correct session/context, then `page.evaluate(fetch...)` calls the same endpoints the UI uses.

## Source

- Menu discovery: `GET https://888starz.bet/cyber-api/mainfeedline/web/cyber/v3/leftmenu/real?fcountry=12&gr=789&lng=en&ref=233`
- Pre-match events: `GET https://888starz.bet/cyber-api/mainfeedline/web/cyber/v3/gamesBySport/real?...&subSport=<id>`
- Live events: `GET https://888starz.bet/cyber-api/mainfeedlive/web/cyber/v3/gamesBySport/real?...&subSport=<id>`

Supported `hubSlugs`: `cs-2`, `dota-2`, `league-of-legends`, `valorant`, `rainbow-six`, `starcraft-2`, `overwatch`, `honor-of-kings`.

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

See `888STARZ-API.md` in the parent directory for the full API contract.
