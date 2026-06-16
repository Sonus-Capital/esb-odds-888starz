# 888starz esports odds scraper

Public 888starz esports odds via DOM/text scraping.

## Flow

1. Open `https://888starz.bet/en/esports/real/all`.
2. Discover game hub links (`/en/esports/real/{game}`).
3. Click through each hub and extract event card links.
4. Parse event URL for game, league, event id; parse link text for W1/W2 odds and scores.

## Inputs

- `hubSlugs`: array of game slugs to limit scraping (e.g. `["cs-2", "dota-2"]`).
- `maxHubs`: integer limit on hubs (0 = all).
- `headful`: debug mode.
