# 888starz esports odds scraper

Scrapes public esports match-winner odds from 888starz using the unauthenticated cyber feed API.

## Source

- Menu discovery: `GET https://888starz.bet/cyber-api/mainfeedline/web/cyber/v3/leftmenu/real?fcountry=12&gr=789&lng=en&ref=233`
- Pre-match events: `GET https://888starz.bet/cyber-api/mainfeedline/web/cyber/v3/gamesBySport/real?...&subSport=<id>`
- Live events: `GET https://888starz.bet/cyber-api/mainfeedlive/web/cyber/v3/gamesBySport/real?...&subSport=<id>`

Supported `hubSlugs`: `cs-2`, `dota-2`, `league-of-legends`, `valorant`, `rainbow-six`, `starcraft-2`, `overwatch`, `honor-of-kings`.

## Output fields

- `event_id`, `const_id`
- `brand` = `888starz`
- `game`, `league`, `team_a`, `team_b`
- `is_live`, `start_time`, `stage`
- `markets`: `[{market_id: "match_winner", outcome_id: "H"/"A", team, odds}]`

See `888STARZ-API.md` in the parent directory for the full API contract.
