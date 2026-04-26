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
`link.clashofclans.com`. Liveness is best-effort: Supercell does not
expose an "is this layout valid?" API, so a 200 only confirms that the
share endpoint accepted the request, not that the layout still resolves
in-game. The CI workflow runs this on every push.

### Field reference

| Field         | Required | Notes                                                                |
| ------------- | -------- | -------------------------------------------------------------------- |
| `id`          | yes      | Unique slug, kebab-case.                                             |
| `name`        | yes      | Display name.                                                        |
| `town_hall`   | yes      | Integer, 1–16+.                                                      |
| `type`        | yes      | `War`, `CWL`, `Trophy`, `Farm`, `Hybrid`, `Legend`, `Builder Hall`…  |
| `link`        | yes      | Full `https://link.clashofclans.com/...` URL from the in-game share. |
| `description` | no       | Short paragraph about the base's strengths.                          |
| `builder`     | no       | Credit for the original designer.                                    |
| `tags`        | no       | Free-form list, used by the search box.                              |
| `added`       | no       | `YYYY-MM-DD` for sorting / display.                                  |

## Local development

Prerequisites: Rust 1.85+ (edition 2024) and the `wasm32-unknown-unknown`
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
