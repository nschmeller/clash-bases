# Clash Bases

A static, community-editable library of **Clash of Clans base layouts**, built
with **Rust + WebAssembly** (Yew) and hosted on **GitHub Pages**.

Bases live in [`bases.json`](./bases.json) and are committed to the repository.
Visitors can browse, search and filter the catalogue, then click **Open in
Clash** to load a layout straight into the game via the official
`link.clashofclans.com` URL scheme.

## How it works

- The site is a single-page Rust/WASM app compiled to a static bundle by
  [Trunk](https://trunkrs.dev).
- At load time it `fetch`-es `bases.json` and renders a card for each entry.
- Each card has a deep link to the COC mobile app
  (`https://link.clashofclans.com/?action=OpenLayout&id=...`). On a phone with
  the game installed, tapping it opens the layout directly. On desktop it
  shows a web preview with a QR code.
- There is no backend, no database and no analytics. The repo *is* the
  database.

## Catalogue origin

The catalogue currently has **5,162 entries**, assembled from a mix of
GitHub seed data and three major community catalogue sites. Every
entry credits its upstream source in the `builder` field and has a
preview image (the validator rejects entries without one).

Distribution per TH: TH4 168 · TH5 168 · TH6 212 · TH7 384 · TH8 324 ·
TH9 359 · TH10 383 · TH11 398 · TH12 366 · TH13 373 · TH14 366 ·
TH15 357 · TH16 354 · TH17 535 · TH18 415.

### Notes on aggregation tactics

The three catalogue sites — cocbases.com, basemelon.com,
blueprintcoc.com — serve their per-base detail pages as plain HTML with
the share link in an `OpenLayout` `href` and the per-base preview image
in either a JSON-LD `rawItems` block (cocbases), a fixed
`img.basemelon.com/bases/th{N}/{id}/md.jpg` URL (basemelon), or a
nearby `<img>` element (blueprintcoc). Polite throttled crawling
(3 concurrent connections per host with 1.5×-exponential-backoff on
429/503) gets through without trouble; the harvester lives at
`/tmp/scrape/harvest.py` in the development sandbox. **Zero overlap**
between the three sources at the share-link layer — each site has
independently curated content.

Other catalogue sites (clashchamps, clashbaselink, cocbase.link,
clashofclans-baselinks, clashofclans-layouts, clashcodes) sit behind
Cloudflare or return 404/503 to non-browser traffic and were not
mirrored. They remain good fallbacks for manual contributions.

## Where to find bases

If you don't have your own layout to share, these are well-known
community catalogues. Open the base you like, copy the in-game share
link, and submit it via PR (with attribution to the original builder):

- [cocbases.com](https://cocbases.com/town-hall-16/)
- [clashofclans-layouts.com](https://clashofclans-layouts.com/plans/th_16/war/)
- [basemelon.com](https://basemelon.com/coc-bases-th16/war)
- [clashbaselink.com](https://clashbaselink.com/th16-base-layout/)
- [blueprintcoc.com](https://blueprintcoc.com/blogs/town-hall-16)
- [Reddit r/ClashOfClansBases](https://www.reddit.com/r/ClashOfClansBases/)

Please respect each site's terms of use. Do not bulk-import bases
without permission — share the ones you've personally tested and credit
the original builder.

## Adding a base

1. In-game, open the layout you want to share, tap **Share**, choose **Copy
   link**.
2. Add an entry to [`bases.json`](./bases.json):

   ```json
   {
     "id": "th16-war-mybase",
     "name": "My Base Name",
     "town_hall": 16,
     "type": "War",
     "link": "https://link.clashofclans.com/en?action=OpenLayout&id=TH16%3AWB%3A...",
     "description": "Short notes about the base.",
     "builder": "YourName",
     "tags": ["anti-3-star", "anti-rootrider"],
     "added": "2026-04-26"
   }
   ```

3. Open a pull request. Once merged to `main`, the GitHub Actions workflow
   rebuilds the site and publishes it to Pages.

> **Tip:** there's an interactive helper that does step 2 for you:
>
> ```bash
> ./scripts/add-base.sh
> ```
>
> It picks up the layout link from your clipboard if one is there, prompts
> for the remaining fields, and validates the result before writing.

### Validating bases.json

```bash
python3 scripts/validate-bases.py bases.json
```

The validator goes beyond schema-checking. Every layout link is
required to match a fixed structural shape derived from real Supercell
share IDs:

- The id portion looks like `TH<n>%3A(HV|WB)%3A<32-char-base64url>`.
- The base64url payload decodes to exactly **24 bytes**.
- Bytes 4–8 of the payload encode the layout slot (1, 2 or 3) — anything
  outside that range means the link is forged or corrupted.
- The TH number embedded in the URL must match the entry's `town_hall`.

Pass `--liveness` to additionally HTTP-probe each link against
`link.clashofclans.com`. As of 2026-04 the share endpoint serves a
byte-identical landing page for every TH/blob combination — including
syntactically correct but cryptographically invalid ones — so a 200
only confirms reachability, not validity. There is no public API
(neither `link.clashofclans.com` nor `api.clashofclans.com/v1/`) that
validates a specific layout id; the in-app deep-link handler is the
only authoritative resolver. Structural validation + curation by the
upstream source is the strongest programmatic confidence the
catalogue can offer.

### Field reference

| Field         | Required | Notes                                                                |
| ------------- | -------- | -------------------------------------------------------------------- |
| `id`          | yes      | Unique slug, kebab-case.                                             |
| `name`        | yes      | Display name.                                                        |
| `town_hall`   | yes      | Integer, 1–20.                                                       |
| `type`        | yes      | One of `War`, `Trophy`, `Farm`, `Hybrid`, `Progress`, `Fun`, `Home Village`. |
| `link`        | yes      | Full `https://link.clashofclans.com/...` URL from the in-game share. |
| `image`       | yes      | Per-base preview image URL (≥200×200; YouTube/TikTok thumbnails rejected). |
| `description` | no       | Short paragraph about the base's strengths.                          |
| `builder`     | no       | Credit for the original designer.                                    |
| `tags`        | no       | Free-form list, used by the search box.                              |
| `added`       | no       | `YYYY-MM-DD` for sorting / display.                                  |

## Local development

Prerequisites: Rust 1.95+ (edition 2024) and the `wasm32-unknown-unknown`
target.

```bash
rustup target add wasm32-unknown-unknown
cargo install --locked trunk
trunk serve --open
```

`trunk serve` watches for changes to `src/`, `index.html`, `styles.css` and
`bases.json`, and live-reloads the browser.

To produce a production bundle:

```bash
trunk build --release --public-url /clash-bases/
```

The output is written to `dist/`.

## Deployment

Pushes to `main` trigger
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml), which:

1. Installs the stable Rust toolchain plus `wasm32-unknown-unknown`.
2. Installs Trunk and `wasm-bindgen-cli`.
3. Runs `trunk build --release --public-url "/<repo-name>/"`.
4. Uploads `dist/` as a Pages artifact and deploys it.

To enable this for your fork:

1. Settings → **Pages** → set **Source** to **GitHub Actions**.
2. Push to `main`.

## License

This project is licensed under the [MIT License](./LICENSE).

Clash of Clans is a registered trademark of Supercell Oy. This project is not
affiliated with or endorsed by Supercell.
