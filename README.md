# SF Apartment Alerts

Watches the **official availability feeds** of San Francisco apartment buildings and texts you the
moment a new unit matching your criteria is listed, with a link to the listing and a link to the
actual online application.

It deliberately does **not** scrape Zillow, Redfin, Apartments.com or any other aggregator. Those
lag the property's real availability and list units that are already gone. Every building here is
read from its own site or the leasing back end that powers it, so a listing shows up here when it
shows up for the leasing office.

No LLM, no API keys, no cloud service. It is plain Python that runs on your machine for free.

## What it watches

67 buildings are preconfigured, across eleven leasing platforms:

| Platform | What it reads |
|---|---|
| Yardi RentCafe / securecafe | the online-leasing availability page, with per-unit apply links |
| SightMap (Engrain) | the JSON behind the interactive floor-plan map |
| RentCafe marketing sites | the embedded floor-plan app JSON |
| RentCafe hosted templates | the embedded unit list |
| Greystar | the Next.js payload on property pages |
| Equity Residential | the embedded unit availability object |
| EliseAI portals | the live application system's unit search |
| UDR | the embedded property view-model |
| AvalonBay | the embedded units array |
| Related | server-rendered unit cards |
| Entrata | the embedded unit data attribute |

## Quickstart

```bash
git clone <your-fork-url> && cd apartment_searcher
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml     # then edit: phone number + filters
python -m aptbot test-notify           # confirm a text arrives
python -m aptbot once                  # poll everything once, print matches, send nothing
./scripts/install.sh                   # run continuously, start at login, restart on crash
```

Requires Python 3.11 or newer.

### Getting texts

| Channel | Works on | Cost | Setup |
|---|---|---|---|
| `imessage` (default) | macOS only | free | Messages.app signed in; grant Automation permission on first send |
| `ntfy` | anywhere | free | pick a topic name, install the ntfy app, subscribe |
| `twilio` | anywhere | paid | put credentials in `.env`, see `.env.example` |

On Linux or Windows, switch `notify.channels` to `ntfy` or `twilio`. The iMessage channel drives
Messages.app through AppleScript and will fail elsewhere.

## Configuring what you get

`filters:` in `config.yaml` takes `bedrooms` (0 means studio), `min_price`, `max_price`,
`min_sqft` and `available_by`. Any of them can be overridden per building. Toggle a building with
`enabled: false`. After editing, restart:

```bash
launchctl kickstart -k gui/$(id -u)/com.aptbot.sf
```

Every alert names the building class, derived from the `stories` field: high-rise at 12 floors or
more, mid-rise at 5 to 11, low-rise below that.

## How it behaves

- The first time it sees a building it records everything silently as a baseline and sends nothing.
  You only get told about units that appear after that.
- A unit that disappears and returns within `relist_days` is not re-alerted. After that window it
  counts as new again.
- A failed fetch never marks units as gone. It backs off and retries.
- When a property lists a unit before publishing a price, the alert says the application is not open
  online yet and links the contact form. You get a follow-up alert when it becomes applyable.

## Daily use

```bash
./scripts/aptbot status      # last poll per building, recent alerts
./scripts/aptbot once        # poll now and print matches
./scripts/aptbot list        # configured buildings
./scripts/aptbot resolve     # re-resolve property ids and API urls
tail -f logs/aptbot.log
```

`data/current_matches.md` is rewritten every few minutes with everything currently listed that
matches your filters.

## Adding or fixing a building

Property IDs, SightMap embed hashes and portal slugs change when a building switches vendors, so
expect a few config entries to go stale over time. `./scripts/aptbot resolve` re-derives them.

To add a building, open its availability page and look for one of these markers to tell you which
source type handles it:

| Marker in the page source | `source.type` |
|---|---|
| `securecafe.com/onlineleasing/<slug>/` | `securecafe` |
| `sightmap.com/embed/<hash>` | `sightmap` |
| `id="jd-fp-data-script-app"` | `jdfp` |
| `ysi.unitsList = [` | `rentcafe_site` |
| `self.__next_f` with `propertyUnits` | `greystar` |
| `ea5.unitAvailability =` | `equity` |
| an EliseAI portal link | `elise` |
| `window.udr.jsonObjPropertyViewModel` | `udr` |
| `"units":[{"unitId"` | `avalon` |
| `div.unit-card` on relatedrentals.com | `related` |
| `entrata.com` with a `data-units` attribute | `entrata` |

Then verify with `./scripts/aptbot once -b <building_id> --all-units`.

## Development

```bash
python -m pytest -q
```

Parser tests run against saved real pages in `tests/fixtures` (stored gzipped). If a site changes
its markup, the matching test fails, which is how you tell "the site changed" apart from "I broke
the parser."

## Please be considerate

This reads public pages on a schedule. The shipped defaults are 120 seconds per building, and
180 for securecafe hosts, with jitter and per-host concurrency limits. Tightening those to a few
seconds across many buildings puts real load on small property sites and is a good way to get your
IP blocked. Check each site's terms of service before pointing this at it. It reads only pages any
visitor can load, and it never submits anything.

## License

MIT, see [LICENSE](LICENSE).
