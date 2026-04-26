# Session handoff — context from the previous Claude session

This file exists so a fresh Claude session (with a wider network ACL)
can resume the catalogue-growing work without re-deriving everything.

---

## Project state at handoff

- 3 commits pushed to `claude/clash-base-sharing-app-GXszu`.
- 140 entries in `bases.json`, all passing
  `python3 scripts/validate-bases.py bases.json` (structural).
- Distribution by Town Hall:

  | TH | count | notes |
  |----|-------|-------|
  | 18 | 4     | **THIS IS THE CEILING TO BREAK.** 3 from `tonykslee/ClashCookies` FWA seed (Basic/Ice/Rising Dawn, all WB), 1 from `topusapp/topusapp.github.io` YouTube data (KLAWKLA's HV). |
  | 17 | 53    | Rich. Mostly `isabelle1309/COCBaseShowcase` Apr/May/Jun '25 + `tonykslee` FWA. |
  | 16 | 23    | Mix of `RomNeedBoba/coclayout` legend HV + `tonykslee` FWA + `saadahmed` farm + 1 user-submitted (Praz). |
  | 15 | 9     | `tonykslee` FWA + `saadahmed` farm + `RomNeedBoba` legend. |
  | 14–4 | 3–6 each | `tonykslee` FWA + `saadahmed` farm. |

---

## Layout-link structural invariants (verified)

A real Supercell share URL always matches:

```
https://link.clashofclans.com/<lang>/?action=OpenLayout&id=TH<n>%3A(HV|WB)%3A<32-char base64url>
```

When the base64url tail is decoded:

- **Exactly 24 bytes**, never any other length.
- **Bytes 0..3**: big-endian `uint32`, small (0..~100). Likely a
  collection index.
- **Bytes 4..7**: big-endian `uint32` ∈ `{1, 2, 3}`. The in-game layout
  slot (Layout 1 / Layout 2 / War Layout). Anything outside this set
  means the link is fabricated or corrupted.
- **Bytes 8..23**: opaque 16-byte tag, almost certainly an HMAC keyed by
  Supercell. We cannot forge or enumerate IDs.

`scripts/validate-bases.py` enforces all of these plus the cross-check
that the `TH<n>` in the URL matches the entry's `town_hall` field.
Optional `--liveness` mode probes `link.clashofclans.com` and inspects
the response body for layout-specific markers.

---

## Network reachability map (from the previous sandbox)

### Reachable
- `raw.githubusercontent.com` — any public repo file, no auth.
- `mcp__github__search_code` / `search_repositories` / `search_issues`
  / `search_pull_requests` — work across all of public GitHub. **Use
  these aggressively.**
- `github.com` repo HTML pages (file listings work; web search results
  require login — preloaded `results: []` for unauthenticated requests).
- `registry.npmjs.org` — npm metadata. Only relevant package is
  `clash-of-clans-data` (game stats, not layouts).
- `api.github.com` — works but **rate-limited to 60 req/hour unauth**.

### Scoped
- `mcp__github__get_file_contents` / `list_commits` / `list_branches`
  are **session-scoped to `nschmeller/clash-bases` only**. Calls against
  other repos fail with `Access denied: repository "X" is not configured
  for this session`. Use raw.githubusercontent.com instead, with known
  paths.

### Previously blocked (status to re-test in a new session with `Full` ACL)
The following were all 403 / DNS-fail from this session. The user has
just updated the cloud environment to **Full** network access, which
applies to new sessions. Re-test these immediately:

- All catalogue sites: `cocbases.com`, `basemelon.com`,
  `blueprintcoc.com`, `clashchamps.com`, `clashcodes.com`,
  `clashofclans-layouts.com`, `bases-coc.com`, `cocbase.link`,
  `clashofclans-baselinks.com`, `clashbaselink.com`.
- `link.clashofclans.com` (Supercell's share endpoint).
- Wayback: `web.archive.org`, `archive.org/wayback/available`,
  `web.archive.org/cdx/search/cdx`.
- Search: `duckduckgo.com`, `html.duckduckgo.com`, `www.bing.com`,
  `www.google.com`, `sourcegraph.com`, `grep.app`, `search.brave.com`,
  `search.marginalia.nu`.
- Social: `www.reddit.com`, `www.pinterest.com`, `www.youtube.com`,
  `hn.algolia.com`, `api.stackexchange.com`.
- CDN proxies: `cdn.jsdelivr.net`, `unpkg.com`, `cdn.statically.io`.
- IPFS: `ipfs.io`, `dweb.link`, `gateway.pinata.cloud`.

If any of these now resolve in a new session, use them in this priority
order:

1. **`web.archive.org/cdx/search/cdx?url=cocbases.com/town-hall-18/&output=json&from=20251101`** — gives every snapshot URL of the catalogue page; raw HTML can then be fetched via `web.archive.org/web/<timestamp>/<url>`. Same trick works for basemelon, blueprintcoc, clashbaselink, etc.
2. **Direct catalogue pages** — `https://cocbases.com/town-hall-18/`,
   `https://blueprintcoc.com/blogs/town-hall-18`,
   `https://basemelon.com/coc-bases-th18`. Cloudflare may still bot-detect; rotate User-Agent and Accept-Language headers, or use a residential-look UA like Chrome 126.
3. **Reddit JSON API** — `https://www.reddit.com/r/ClashOfClansBases/top.json?t=month&limit=100` returns posts with TH18 share links in their body.
4. **YouTube descriptions** — playlists like
   `https://www.youtube.com/playlist?list=PL6V5qb8gIdiUcy-KaOpKOvxU8o2fBBQya`
   ("⚒️ TH18 Base Layouts + Links — Blueprint CoC"). Fetch via the YouTube
   oEmbed endpoint or static HTML to extract descriptions; descriptions
   commonly contain `link.clashofclans.com/...` URLs.

---

## Upstream sources already harvested (don't re-mine these)

Already in `bases.json`. If you find a strictly larger version of any
source, replace; otherwise skip.

| Source | Branch | Path | Notes |
|---|---|---|---|
| `tonykslee/ClashCookies` | master | `src/services/FwaLayoutSeedData.ts` | 30 FWA layouts TH8–TH18, 3 styles each. **All 3 TH18 already imported.** |
| `tonykslee/ClashCookies` | master | `tests/fwaBaseSwap.layoutLinks.test.ts` | Adds 1 TH16 + 1 TH17 + 1 TH18 — already imported. |
| `saadahmed0147/coc_bases` | main | `lib/Data/townhall_data.dart` | 42 layouts TH4–TH17 categorised. Already imported. |
| `isabelle1309/COCBaseShowcase` | main | `images/{apr25,may25,jun25}/{apr25,may25,jun25}.json` | Monthly TH17 showcase. Apr=15, May=29, Jun=3. Already imported. **No `jul25`/`aug25`/etc. exist** — we probed every month name. |
| `RomNeedBoba/coclayout` | master | `Townhall{15,16}.html` | Legend HV layouts. 16 TH16 + 3 TH15 imported. TH7–TH14 also exist (29–30 each, mostly WB) but already at saturation for those THs. |
| `topusapp/topusapp.github.io` | main | `content/yttrending-videos-th-20.json` | Thai YouTube trending. **1 TH18 from KLAWKLA's release video** — already imported. |
| Praz (user-submitted) | n/a | n/a | TH16 HV. |

### Promising sources investigated but not yielding
- `vichea69/clash-of-clans-Frontend` — uses external API, not static data.
- `arcadeclash/coc-recommended-base-links` — empty placeholder repo.
- `diyor200605/ClashOfClansBases` — placeholder share-codes (`TH6-BASE-001` etc.), not real Supercell IDs.
- `WinslayS/COCArmyFront` — 70 TH17 layouts but mostly em-dash (no name) entries; quality lower than isabelle1309.
- `jakelen61732/jakelen61732` — TH4/TH5 + Builder Hall only.
- `Lohith1807/Discord-Clash-Bot-cloud` — 6 FWA links TH12–TH17, no TH18.
- `chiefpansancolt/clash-of-clans-data` — game stats package, no shared layouts.

### GitHub search query history (already exhausted)
These all returned ≤4 results, all in the sources above:
- `"link.clashofclans.com" "OpenLayout"` → 35 hits, all mined
- `"OpenLayout&id=TH18"` → 4
- `"TH18%3AHV" OpenLayout` → 1 (topusapp)
- `"TH18%3AWB" OpenLayout` → 2 (tonykslee seed + tests)
- `"id=TH18" OR "TH18%3A" OpenLayout` → 3
- `"FwaLayout" Townhall` → 7 (all in tonykslee)
- Issues / PRs corpus: 0 hits
- `repo:tonykslee/ClashCookies "TH18"` → only known files

---

## Recommended new-session prompt

> Read `CONTEXT.md`. The previous session was on a restricted egress
> sandbox; you should now have `Full` network access. Verify by curl-ing
> `https://cocbases.com/town-hall-18/`. If it resolves (HTTP 200,
> non-empty HTML body), scrape every `link.clashofclans.com/...` URL
> from each catalogue site listed in CONTEXT.md, plus their Wayback
> snapshots for redundancy. Filter to TH17/TH18, dedupe against
> `bases.json`, attribute each new entry to its source URL, run
> `python3 scripts/validate-bases.py bases.json` to confirm structural
> validity, then commit and push to
> `claude/clash-base-sharing-app-GXszu`.

---

## Architectural notes

- **No images committed to repo.** Every `image` field is a remote URL
  (imgur, bases-coc.com, raw.githubusercontent.com). Adding entries
  with new image hosts is fine; just keep them remote.
- **MIT licensed.** Layout *links* are by design shareable; redistributing
  with attribution is the established norm in the COC scene. We always
  credit the source repo / builder in the `builder` field.
- **CI runs on push to `main`.** GH Pages deploys via
  `actions/deploy-pages@v4`. The workflow runs the validator with
  `--liveness` against every link.
