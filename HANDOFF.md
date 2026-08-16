<!--
Generated from the maintainer's working handoff. Absolute paths are genericised:
  <repo>   this repository
  %LOCALAPPDATA%\FIFA14LocalFUTBeta   the persistent save folder
-->
# FIFA 14 Local FUT — working handoff

A local/community FUT server for a FIFA 14 install the user already owns. It answers the
game's HTTP and Blaze traffic on localhost so single-player FUT works offline: club,
squads, transfer market, packs, cups, seasons, match settlement and the economy.

**Scope rules.** Local tooling only. Do not target EA or FUT services, do not bypass
protections, never print credentials. Diagnostics against the installed game are read-only.

---

## 1. Start here

Everything below is shipped in **0.4.5 beta** and passes all eight verifiers. Two things
have never been observed in game and are the highest-value next step, in this order:

1. **Play one offline season to the end and capture it.** Does the season come back
   *underway* after a match, and does the ladder move the club from Division 10 to
   Division 9 after ten fixtures? The server settles promotion itself; what is unproven is
   whether the client adopts the division we report. `fut-season-request-beta2260` logs the
   divisions served next to the club's actual division on every request, so the capture
   answers it directly. If the ladder does not move, the fallback is to serve the current
   tier's *content* under whatever division id the client asks for.
2. **Run `FIFA14_PLAYER_STAT_PROBE` and screenshot one card.** Each card has five
   unlabelled stat slots; the order in `PLAYER_CARD_STAT_INDEX` is inferred. The probe
   serves 11/22/0/44/55 in slots 0–4; slot 2 must stay zero because FIFA treats any
   red-card total as an active suspension. Read-only.

After those: staff on the transfer market (§7.2), then the pack manager (#6) or tournament
names/icons (#8).

---

## 2. Where things live

| What | Path |
|---|---|
| **Active tree** | `<repo>` |
| Superseded tree | `<superseded tree>` — older revision, read for history only |
| Live save (SQLite) | `%LOCALAPPDATA%\FIFA14LocalFUTBeta\local-fut-beta-v2410.sqlite3` |
| Save backups | same folder, `backups\` |
| User settings | same folder, `local-fut-settings.json` |
| Latest capture (unzipped) | `<active tree>\artifacts\` — `redirect-probe.log`, `frida-pc-fut-nav-route-patch.log` |
| Tester-submitted zips | `<tester reports folder>\` (each contains a `REPORT.txt`) |
| Crash dumps | `<crash dump folder>\` |
| Older evidence, UI extracts | `<evidence folder>\` |
| Analysis scripts | `<scratchpad>` (see §9) |
| Game install | `<FIFA 14 install>` |
| GitHub | `dzevallos/f14-localfut`, public fork of `KyroGeorge2/FIFA-14-Local-FUT`. `gh` is installed and authed. |

**This handoff is the master copy.** The repo's `HANDOFF.md` is a genericised mirror —
replace the absolute paths above with `<repo>`, `<superseded tree>`, `<crash dump folder>`,
`<evidence folder>` and keep the `<!-- Generated … -->` header, then commit it with the
rest.

### Architecture

| File | Responsibility |
|---|---|
| `server/probe.py` | HTTP + Blaze routing, response emission. `build_fut_json_payload` is the single serialization choke point. |
| `server/local_identity.py` | `LocalIdentityStore`: items, squads, market, club, packs, cosmetics |
| `server/beta_identity.py` | `BetaIdentityStore` (subclass): economy, cups, **seasons**, match settlement |
| `server/fut_local_settings.py` | user settings loader — must never raise, never trust |
| `server/sitecustomize.py` | release adapter; subclasses the store at import to reshape two responses |
| `tools/frida_pc_fut_nav_route_patch_trace.py` | Frida agent (JS embedded in `agent = r"""…"""`) |
| `tools/verify_*.py` | the eight verifiers the launcher runs before startup |

---

## 3. The loop

**Run the suite after every behaviour change.** The launcher runs these before startup and
`throw`s on any non-zero exit, capturing the output into a variable — so a failing verifier
presents to the user as *the game will not start*, with no message.

```bash
cd <tree>/tools && for v in verify_fifa14_v237_install.py verify_fifa14_beta2.py \
  verify_fifa14_postmatch_beta2259.py verify_fifa14_consumables_beta224.py \
  verify_fifa14_pack_ui_performance_beta2250.py verify_fifa14_market_beta2250.py \
  verify_fifa14_regressions_beta2258.py verify_fifa14_postmatch_beta2256.py; do
  python "$v" >/dev/null 2>&1 && echo "PASS $v" || echo "FAIL $v"; done
```

(`verify_fifa14_pack_ui_performance_beta2244.py` fails pre-existing and is unused.)

**Releasing.** The working tree is not a git checkout. Clone the repo to a temp dir, copy
the changed files in, commit, push, then:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./tools/package_release.ps1 -Version "v0.4.6-beta"
gh release create v0.4.6-beta dist/FIFA-14-Local-FUT-v0.4.6-beta.zip \
  --repo dzevallos/f14-localfut --target main --title "0.4.6 beta" --notes-file notes.md
```

Two traps: `git push` of an **annotated tag** is rejected ("email privacy restrictions") —
let `gh release create` make the tag instead; and `--target <sha>` is rejected, it wants a
branch name. Check the built ZIP for `.pyc`/`__pycache__` (they embed the builder's
absolute path), `config.local.psd1` and `local-fut-settings.json` — all four have shipped
by accident before.

---

## 4. Operating rules

1. **`emit()` in probe.py prints JSON to stdout, and stdout IS `redirect-probe.log`.**
   Never `print()` to stdout from server code. Use `_diagnostic()` (stderr).
2. **A failing verifier stops the game booting** (see §3). Corollary: **verify every
   *setting*, not just the default.** A user picking dynamic rewards once failed three
   verifiers. When a value becomes configurable, the verifier must assert the *invariant*,
   not the shipped number.
3. **Verifiers encode recorded in-game observations.** Do not weaken a guard to fit an
   inference; get an observation first. But an assertion can encode *our own tuning* rather
   than a client contract, and a new capture beats it.
4. **The Frida JSON-key hook is expensive** (~7 ms/key). Never arm it across a gameplay
   transition; a ~14 s stall blows the Blaze keepalive. Guards: `HEAVY_TRACE_FORBIDDEN_KINDS`,
   `CHEAP_TRACE_KINDS`, an emit-budget gate, per-window cost accounting.
   **4a. The emit budget bounds the logging, not the work.** `emit()` caps at 1000 records
   per kind, but anything computed before the call runs forever. The crash handler was
   building a 32-frame `Backtracer.ACCURATE` walk plus a module lookup per frame for every
   exception — and every `OutputDebugString` arrives as one. A tester with a joystick FIFA
   does not recognise generated ~26/second for a whole match: "frame rate lower than usual",
   26 minutes of wall clock for a 6-minute match. Put the budget check *first*.
5. **Before writing to the DB or game files**: back up, and confirm neither `probe.py` nor
   `fifa14.exe` is running.
6. Assert-style crashes surface as `STATUS_BREAKPOINT` at an `int 3` with no message.
7. **Mine the captures before theorising.** An unhandled query token returns an *empty
   result*, not an error, so a parsing gap looks like a missing feature. `type=equippables`,
   `pos=CAM-CF`, `divisionList=11` and two dead market tabs were all found by grepping
   `redirect-probe.log`.
8. **Ask the client binary.** `CardsDLLzf.dll` holds the URL format strings and wire member
   names. Beyond strings: `pip install capstone` into `.venv` and use
   `scratchpad/pe_xref.py` (§9). Note the JSON member names live in one sorted `const char*`
   table (`0x1d2bf0`, 583 entries) and parsers refer to keys **by index**, so no xref will
   point at them — resolve a key id by its position in that table.
9. **Tabulate every datapoint before concluding.** Three releases in a row (0.4.2–0.4.4)
   fixed a symptom reasoned from the newest observation while the answer was sitting in the
   older ones. Write the table out — inputs against outcomes, including the runs that
   worked — and discard confounded rows explicitly.
10. **A test that passes once has not been run.** Anything touching the rotation or the
    clock must be swept across rotations before it is trusted; a market assertion passed
    locally and bricked the tester's startup because the card it bought happened to be
    listed in that rotation.
11. PowerShell 5.1: any native stderr line becomes a `NativeCommandError` under
    `-ErrorAction Stop`, even on exit 0. `-Include` with a wildcard-free `-LiteralPath` is
    silently ignored and yields *every* file (this deleted a whole staged tree once).

---

## 5. Proven client contracts — do not break these

Each of these cost a crash or a dead screen to learn.

**Offline Seasons — `season/user` & `season/list`.** Two rules, independently established:
- **`divisionList=11` must map to the entry tier in `season/list`.** The client's unplaced tier index is `0` (`11 - 11 = 0`). If `season/list` does not return a record under `divisionId: 11`, CardsDLL's `11 - divisionId == user_division` check (`0x100623e5`) finds no matching record and calls `_global.NOSEASONS` ("seasons are currently unavailable").
- **`seasonId: -1` sentinel for fresh/unsaved season.** When no save data exists, sending `seasonId: -1` triggers the fresh season initialization path at `0x1006223e`, selecting the active tier without attempting to deserialize null save buffers. When save data exists, sending the ladder ID resumes saved state via `0x100621cb`.
- **Persist `divisionList=11` as Division 10 internally.** The request value `11` is accepted as an alias on `PUT /season/<id>/division/11/user`, but `beta_season_progress` must retain a real ladder division. Older saves are normalized on the next season read without discarding their round or blob.

  | `seasonId` | `divisionId` | outcome |
  |---|---|---|
  | -1 | 11 (in list) / 10 (user) | **worked** (fresh season initialization at `0x1006223e`) |
  | 1 | 10 | bounce, then "unavailable" — **confounded**, that build also had the `assetId: 0` award bug |
  | 2 | 10 | `_global.FAIL` when list only has id 1 |
  | 1 | 11 | access violation reading null at `CardsDLLzf+0xc66dd` when data buffer was empty |
  | 12 | 11 | "seasons are currently unavailable" |

**Buffer before its version.** This parser family reads a response as a stream, and a
version member decodes *the buffer immediately before it*. The cup resume response had to
be reshaped to `{round, tournamentData, dataVersion}`; sending them the other way round
made the client read a length out of a `127.0.0.1` URL string and try to allocate
808,335,154 bytes (BUG-006). Season documents follow the same rule, and a buffer is only
ever sent when non-empty.

**Award shape.** Prize awards use `{"awardType": 1, "value": N, "halid": 0}`. The older
`assetId: 0` form made the client resolve award item 0 — its own no-item sentinel — and
abandon the screen. `FIFA14_SEASON_AWARD_MODE=legacy` restores it for an A/B.

**Post-match item rows.** `FutDestroyMatchServerResponse.items` must stay sparse per-player
match-stat rows, not full ItemData. For completed matches, explicitly include
`redCards: 0` and `suspension: 0` when the client did not submit those fields; omitted
zeroes can leave stale red-card state in the retail client after the result refresh.

**A fresh club has no active cosmetics** until the retail client selects them. Verifier-
pinned; do not fabricate one.

**A stadium id and its `carddbid` are not interchangeable.** Pairing one venue's id with
another's resource asks for a venue that does not exist, which is a crash. Resolve both
from the same catalogue row.

**Cup/season difficulty is the client's own AI ladder**: 0 Beginner … 5 Legendary.

---

## 6. Current state

All fixed and verifier-covered unless marked.

**Squads.** Create/copy (`POST /squad` with `"id":0` has a real create branch; the response
must carry the new id because the frontend navigates by it). Renames apply from a
player-less PUT and from a name on a sparse write. Empty `squadName` means unchanged. A new
squad does not become active unless it is the first.

**Offline Seasons.** The retail ladder, Division 10 (entry) to Division 1, ten fixtures
each, opposition hardening as it climbs.

| Division | Opponents (mean rating) | AI level | Promote / Title |
|---|---|---|---|
| 10 | ~60 | 1 | 9 / 12 |
| 9 | ~62 | 1 | 11 / 14 |
| 8 | ~65 | 1 | 13 / 16 |
| 7 | ~67 | 2 | 14 / 17 |
| 6 | ~70 | 2 | 16 / 19 |
| 5 | ~72 | 3 | 16 / 19 |
| 4 | ~75 | 3 | 18 / 21 |
| 3 | ~77 | 4 | 19 / 22 |
| 2 | ~79 | 4 | 21 / 24 |
| 1 | the elite | 5 | 23 / 26 |

Division 10 keeps its recorded retail contract (promote 9, title 12, no relegation out of
the bottom); relegation applies from Division 9 up. The last three fixtures of a season are
one difficulty step harder. Pools are in `SEASON_TEAM_POOLS`, regenerate with
`scratchpad/season_pools.py` — every club must have a badge *and* a kit in the extracted
match assets and ≥16 players in the catalogue, or its crest will not render.

*How progress works.* The client saves its own season state to
`PUT /season/<id>/division/<div>/user` as
`{"round":N,"dataVersion":1,"data":"<b64>","progressDataVersion":1,"progressData":"<b64>"}`
— once when the screen opens, before kickoff, and again after each match with the round
advanced. `beta_season_progress` (one row per club) stores it and `season/user` echoes it
back. `beta_season_history` backs `season/user/history`, which used to be answered with the
*active* season list. A season list asked about a division we do not have offers the entry
division's record — returning the whole ladder is the original "unavailable".

*How promotion works.* The server tallies fixtures itself (3/1/0, DNF counts as a loss)
because the client never reports the outcome. A match counts as a season fixture when it
carries no `tournamentId` and the Seasons screen saved within
`SEASON_MATCH_SAVE_WINDOW_SECONDS`. When the fixtures run out the season settles against
the served thresholds, pays the prize, writes history and opens the next season one
division up or down. The rollover is deliberately **lazy** — on the next season read, not
at settle — so the client's trailing round-11 write lands on the season it belongs to.

**Cups.** Per-cup opponents through the client's own `aigroup`/`groupId` mechanism
(`TOURNAMENT_TEAM_POOLS`, ~60/67/74/81 mean rating). Difficulty Amateur→Legendary. Resuming
a cup with no saved bracket no longer crashes. Prizes configurable.

**Market.** Rotating stock (deterministic hash of card and rotation). Consumables always
stocked at a flat price. Listings ordered **closest to expiry first** with a countdown that
ticks; expiry is anchored to the rotation so the order holds still and paging cannot
reshuffle underneath the client. A per-card continuous timer was tried and rejected — it
re-sorted the head of a 5,126-listing market every second. Consumables keep a flat
`expires: 3600` and one-of-each ordering on purpose.

**Player cards.** Goals, assists, yellows, reds and appearances accumulate into `statsList`
and `lifetimeStats` (plus their `statsArray` aliases, or the canonicaliser drops whichever
it was not given). Appearances credit the eleven that started, first settlement only.

**Club.** `type=equippables` returns kits/stadiums/badges. Venue defaults to **Forest Park,
stadium id 26 / carddbid 6200010**; configurable through `club.stadiumId` /
`club.stadiumName`, which the Frida agent reads too so the club's stadium and the one
forced onto the client cannot drift. `reset_club_to_starter()` reuses the fresh-profile
path, keeps the wallet, clears the W-D-L record, and must delete
`beta222_cosmetic_catalog_signature` or cosmetics are not rebuilt. `ownedItems` ≈ 1,844 on
a real install because the club owns the whole cosmetic catalogue — count *players* to
judge a wipe.

**Economy.** `MATCH_RESULT_FLAT_COINS` WIN 15000 / DRAW 1000 / LOSS 750, DNF configurable.
`MATCH_REWARD_MODE` flat or dynamic. Cup prizes 50k/25k → 2.5M/750k first clear / repeat.

**Settings** (`FUT_SETTINGS.cmd`, written to `local-fut-settings.json` beside the save):
`matchRewards`, `matchRewardMode`, `tournamentPrizes`, `market`, `club.stadiumId/stadiumName`,
`diagnostics.playerStatProbe`, `diagnostics.seasonSaveMode`. The loader validates and
clamps everything and **must never raise** — it is imported at startup, where an exception
becomes a window that closes silently.

> **Diagnostics must be settable from the settings file, not only an environment variable.**
> `RUN_FIFA14_LOCAL_BETA.cmd` relaunches itself elevated through ShellExecute, and Windows
> gives that elevated process a fresh environment — anything exported in a shell first
> never reaches the server. Env vars still work when the server is started directly and
> take priority.

---

## 7. Open work

### 7.1 Seasons — does the ladder climb? (the #1 item)
See §1. `GetUsersOfflineDivision` reads a member of the client's own season manager
(vtable+0x28 of the singleton at `0x101d62d8`), and the season-list request copies its
division vector out of that object (`+0x1c` → the `&divisionList=` builder at
`0x10151c60`). Whether it adopts the division `season/user` reports is unproven. Hedge in
place: the list serves the club's current division *alongside* whatever was asked for.
If a restored season breaks the screen, `diagnostics.seasonSaveMode: "round"` drops back to
the three members known to parse without losing the stored save.

### 7.2 Staff on the transfer market (#11, remainder)
Unblocked — the client ships the real tables, and `carddbid` *is* the FUT resource id, so
nothing has to be guessed (which removes the BUG-004 risk).

| table | cards | id range |
|---|---|---|
| `managercards` | 166 | 1000500–1000775 |
| `physiocards` | 42 | 4000001–4000042 |
| `headcoachcards` | 36 | 2000001–2000037 |
| `gkcoachcards` | 36 | 9000001–9000045 |
| `fitnesscoachcards` | 36 | 3000004–3000039 |

Already extracted to `server/fifa14-staff-catalog.v2411.json` (316 cards) by
`tools/scan_fifa14_staff_cards.py`. **Remaining: the wire shape** — no capture of a staff
ItemData exists. Model it on the consumable path (`_market_search_consumables` /
`_market_consumable_auction` / the buy-and-grant in `market_bid`), give it its own trade-id
base, expect one capture to settle it. `fcc_misccards` (22 rows, cardsubtype 231) is worth
a look at the same time.

The other empty tabs are *not* bugs: the club already owns all 1,173 kits / 587 badges / 61
stadiums, so there is nothing to sell — making them meaningful means provisioning a starter
club with only some cosmetics, which takes items away from existing clubs and is a product
decision. There is no ball *card* table in the client's data at all.

### 7.3 Player card stat slot order (#12, remainder) — CONFIRMED & RESOLVED
`PLAYER_CARD_STAT_INDEX` = `goals: 0, assists: 1, redCards: 2, yellowCards: 3, gamesPlayed: 4`.
Confirmed by direct disassembly of `CardsDLLzf.dll` at `0x1005f600`:
- `+0x10` -> `PLAYER_GOALS%i` (slot 0)
- `+0x11` -> `PLAYER_ASSISTS%i` (slot 1)
- `+0x12` -> `PLAYER_REDCARDS%i` (slot 2)
- `+0x13` -> `PLAYER_YELLOWCARDS%i` (slot 3)
- `+0x14` -> `PLAYER_GAMESPLAYED%i` (slot 4)
*Gotcha:* If `redCards > 0` on a card, FIFA 14 flags the player as suspended with a red card and blocks them from squad match selection. The debug probe `FIFA14_PLAYER_STAT_PROBE` previously injected 33 into slot 2, which caused the entire squad to appear suspended. Slot 2 is now 0 in probe sentinels.

### 7.4 BUG-005 — intermittent bail-out during match setup
"Could not reach Origin services" then a hang, once, 2026-08-14. Not a tracer stall (it
disarmed cleanly, Blaze pings held). Zero HTTP and zero Blaze traffic between MatchReady's
200 and the logout nav, so the client decided internally. Two matches immediately after
completed normally. The "Leaving Ultimate Team" half was ours and is fixed.

### 7.5 BUG-004 — "DB ERROR" player card in packs
The client's own sentinel record, not our data: `fifa14-player-catalog.v237.json` was
scraped from WeFUT and contains assetIds this install cannot resolve. Fix direction:
extract the client's real assetId set and filter `PLAYER_CATALOG` at startup. **The staff
extraction proves this path is readable** — `scan_fifa14_staff_cards.py` shows how to get
at `cards_ng_db.db` inside the archives.

### 7.6 #8 / BUG-003 — tournament and store names/icons blank (cosmetic)
Confirmed, **do not repeat**: the offer parser does consume `name`/`description`; the values
resolve as localization keys; `leaderboards.ENG_US.xml` is parsed but adding tokens changed
nothing; `storepackdescriptions.en_us.xml` is fetched 638 ms after the offers and never
reaches the FUT locstrings parser, so it has a separate unidentified consumer. **Do not
switch to trans-unit/XLIFF** — a verifier guards it, that format rendered NOT FOUND.
Remaining: instrument CardsDLL's generic response path, or decompress `Data\loc\locale.big`.

### 7.7 #6 — pack manager (feature)
Self-give and editable store packs are close to free; the generator exists and pack
definitions already carry price/contents/weighting. Tournament pack *rewards* are risky —
awarding an item means a different award type, which is what broke Seasons.

### 7.8 Smaller open questions
- Match length: the menu label follows our `matchlength`, gameplay ignores it (`HALF_LENGTH`
  lives in the client's `eGSParams`). Left at 6 so the label does not lie. A runtime
  override via the tracer would work; **not** an archive write.
- Does round difficulty reach the AI, or is it only a label? Bronze Cup round 1 is the
  cheapest test.
- Is Legendary (5) really the ceiling? The client clamps to its own `MIN_/MAX_DIFFICULTY_LEVEL`.
- The `groupId` round trip is inferred from a DLL format string; `/tournament/teams` is only
  requested for a *fresh* cup. `fut-tournament-teams-group-beta2260` logs it.

---

## 8. Diagnostics

Tracer emits, all read-only and signature-checked:

| Emit prefix | Purpose |
|---|---|
| `fifa-debug-string-beta2260` | fifa14.exe's own `OutputDebugString` text. **Read this first.** It named BUG-006 outright ("Out of memory, allocating 808335154 bytes"). |
| `fifa-debug-string-repeat-beta2261` | Repeat count for the previous line. A four-figure count means the game is shouting every frame. |
| `fifa14-native-exception-beta222` | Faulting address, registers and backtrace. Skips debug-print exceptions and is budget-gated (rule 4a). |
| `fifa-assert-reporter-beta2259` | Assertion expression/file/line before the `int 3`. |
| `fifa-match-entity-table-beta2259` | 22-entry match entity table; `valid=22` = healthy. |
| `cards-cup-resume-lookup-beta2259` | Cup resume registry: key wanted vs keys registered. |
| `cards-competition-trace-cost-beta2259` | Per-window key count and duration — proves or disproves a tracer stall. |
| `fut-season-request-beta2260` | Every season request, with award mode, served prizeSet, `divisions` served and the club's `current_division`. |
| `fut-market-search-query-beta2260` | Every market search with its query and what it served. |
| `fut-tournament-teams-group-beta2260` | The `/tournament/teams` query and served ids. |
| `fifa-prematch-fcc-logout-passthrough-beta2260` | The logout guard declined to rewrite a pre-kickoff logout. |

**Reading a crash.** Prefer the tracer's `fifa14-native-exception-beta222` record — it
carries the faulting address, registers and a module+offset backtrace, which is enough to
disassemble the exact instruction with `scratchpad/pe_xref.py`. Compute the module base
from any frame (`address - offset`) to convert a runtime address to an RVA, then add the
DLL's preferred base `0x10000000`. Minidumps in `dmp\` are a fallback: header → streams →
ExceptionStream/ModuleList/ThreadList, x86 CONTEXT at EIP@184, ESP@196, EBP@180. There is a
~256-byte code window around EIP and thread stacks, but no heap. `0x80000003`
STATUS_BREAKPOINT is the memory-exhausted abort path, not a separate fault.

---

## 9. Analysis scripts (`<scratchpad>`)

| Script | What it does |
|---|---|
| `pe_xref.py` | PE file-offset ↔ VA mapping, immediate and call xrefs, and a capstone window disassembler with string annotation. Needs `pip install capstone` in `.venv`. |
| `season_pools.py` | Regenerates `SEASON_TEAM_POOLS` from the player catalogue and match assets. Edit `TIERS` to change the ladder size. |
| `season_sim.py` | Drives a whole season against a throwaway store — save, settle, promote — and prints what `season/user` returns at each step. Run this after any season change. |

`tools/scan_fifa14_staff_cards.py` (in the repo) extracts staff cards from the installed
game; `tools/scan_fifa14_match_assets.py` does the same for kits, stadiums and badges.

---

## 10. Released

`0.4.5 beta` is current (Latest). Take nothing earlier: **0.4 and 0.4.1** can fail a
startup check at random, **0.4.2** crashes on entering Seasons, **0.4.3** reports "seasons
are currently unavailable", **0.4.4** has the wrong ladder shape. All four carry a banner
pointing forward. `0.2 beta` could not start at all; `0.2.1` fixed it.

Tester is `duckiest428`. Issues: **#1–5, #7 closed**; **#11 (partly), #12, #13** addressed
in 0.4.x and awaiting confirmation; **#6, #8, #9 (FIFA Point balance), #10 (chemistry not
live-updating)** open and untouched. Each of #11/#12/#13 has exactly one current comment
pointing at the latest release — if you supersede a release, update those comments rather
than stacking corrections on them.
