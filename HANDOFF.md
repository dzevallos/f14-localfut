<!--
Generated from the maintainer's working handoff. Absolute paths are genericised:
  <repo>   this repository
  %LOCALAPPDATA%\FIFA14LocalFUTBeta   the persistent save folder
-->
# FIFA 14 Local FUT — working handoff

Continuing debugging of a local/community FIFA 14 FUT server. Local tooling only:
do not target EA/FUT services, do not bypass protections, never print credentials.

## Where things live

| What | Path |
|---|---|
| **Active tree** | `<repo>` |
| Superseded tree | `<superseded tree>` (older revision; ignore) |
| Live save (SQLite) | `%LOCALAPPDATA%\FIFA14LocalFUTBeta\local-fut-beta-v2410.sqlite3` |
| Save backups | same folder, `backups\` (`.pre-dnf-reset`, `.pre-cup-reset`, `.pre-coin-clear`) |
| Debug zip contents | `<active tree>\artifacts\` (`redirect-probe.log`, `frida-pc-fut-nav-route-patch.log`) |
| Crash dumps | `<crash dump folder>\` |
| Older evidence + UI static extracts | `<evidence folder>\` |

Architecture: `server/probe.py` (HTTP + Blaze routes), `server/local_identity.py`
(`LocalIdentityStore`), `server/beta_identity.py` (`BetaIdentityStore`, economy/tournaments),
`server/sitecustomize.py` (upstream release adapter, wraps the store at import),
`tools/frida_pc_fut_nav_route_patch_trace.py` (Frida agent, embedded JS in `agent = r"""..."""`).

## Operating rules (learned the hard way)

1. **`emit()` in probe.py prints JSON to stdout, and stdout IS `redirect-probe.log`.**
   Never `print()` to stdout from server code — it corrupts the log. Use the
   `_diagnostic()` helper in `local_identity.py` (writes to stderr).
2. **The launcher runs 7 verifiers before startup and `throw`s on any non-zero exit**,
   capturing their output into a variable — so the window closes with nothing shown.
   Symptom: "crashes out without a log". **Always run the verifier suite after changing
   server behaviour:**
   ```bash
   cd <tree>/tools && for v in verify_fifa14_v237_install.py verify_fifa14_beta2.py \
     verify_fifa14_postmatch_beta2259.py verify_fifa14_consumables_beta224.py \
     verify_fifa14_pack_ui_performance_beta2250.py verify_fifa14_market_beta2250.py \
     verify_fifa14_regressions_beta2258.py verify_fifa14_postmatch_beta2256.py; do
     python "$v" >/dev/null 2>&1 && echo "PASS $v" || echo "FAIL $v"; done
   ```
   (`verify_fifa14_pack_ui_performance_beta2244.py` fails pre-existing and is unused.
   `verify_fifa14_beta2.py` is the squad one — it now also carries the BUG-002 create/copy block.)
3. **Verifiers encode recorded in-game observations.** Twice this session I reasoned from
   static analysis that one was wrong; both times the verifier was right. Do not weaken a
   guard to fit an inference — get an in-game observation first.
4. **The Frida JSON-key hook is expensive** (~7 ms/key: `cstring` + 32-frame
   `Backtracer.ACCURATE` + send). Never arm it across a gameplay transition — a ~14 s stall
   blows FIFA's Blaze keepalive and produces "Origin services unavailable" then an endless
   "Leaving Ultimate Team". Guards now in place: `HEAVY_TRACE_FORBIDDEN_KINDS`,
   `CHEAP_TRACE_KINDS`, an emit-budget gate, and per-window cost accounting.
5. **Before writing to the DB or game files**: back up, and confirm neither `probe.py` nor
   `fifa14.exe` is running.
6. Assert-style crashes surface as `STATUS_BREAKPOINT` at an `int 3` with no message. A hook
   on fifa14.exe's assert reporter is armed (see below) and will print the assertion text.

## Fixed and verified this session

- **BUG-001 — position change did nothing / "broke the server".** `/item/resource/<id>`
  returned `{}`, so the client never re-bound the card. Now returns `{"itemData":[...]}`.
  Added position validation, an off-catalog target guard, an `except Exception` → HTTP 500
  arm (a bare `KeyError` used to escape `do_POST` and drop the socket with no response), and
  `bind_listener()` for actionable port-conflict messages. Confirmed in-game.
- **Match-load crash (access violation, `fifa14.exe+0x1300840`, reading `0xcdcdcded`).**
  The squad contained the **same footballer twice** (two Clint Dempsey cards, assetId 155897).
  FIFA drops the duplicate, builds that team with 10 players, then walks a hard-coded
  22-entry table and reads the slot it never wrote. Guard added: `_clear_duplicate_squad_slots_locked`,
  a `save_squad` write guard, dedupe in `_repair_active_squad_locked`, swept at startup and in
  `squad_list` (which is what `create_match` serves from). Verified: entity table now reports
  `valid=22, uninitialized=0`.
- **Forfeit hang / "Origin services unavailable"** — caused by the tracer stall (rule 4).
- **Economy + difficulty tuning** (all in the constants block at the top of `beta_identity.py`;
  rebuilt from the constants on every request, so a change needs only a restart — no save
  migration, and an in-progress cup keeps its round).
  Flat match payouts `WIN 15000 / DRAW 1000 / LOSS 750` (`MATCH_RESULT_FLAT_COINS`). DNF is not
  in that table and does not need to be: `_reward_breakdown` zeroes both the completion and
  skill components for an abandoned match before the table is consulted.
  `OFFLINE_TOURNAMENT_ROUND_COINS` tracks the WIN payout, so every cup round advertises **and**
  pays 15000 — keep those two equal or the cup screen starts lying.

  | Cup | rounds (difficulty) | first clear | repeat |
  |---|---|---:|---:|
  | Starter | Amateur, Amateur, Semi-Pro, Semi-Pro | 50,000 | 25,000 |
  | Bronze | Semi-Pro ×2, Professional ×2 | 100,000 | 50,000 |
  | Silver | Professional ×2, World Class ×2 | 250,000 | 100,000 |
  | Gold | World Class, Legendary ×3 | 2,500,000 | 750,000 |

  Round difficulty is the client's own AI ladder index: **0 Beginner, 1 Amateur, 2 Semi-Pro,
  3 Professional, 4 World Class, 5 Legendary**. Anchored on an in-game observation — the four
  cup tiles read Amateur/Amateur/Semi-Pro/Professional when their round-1 values were 1/1/2/3,
  so the tile shows round 1 and the level ramps through the bracket. `repeatPrize` is now set
  per cup (`TOURNAMENT_REPEAT_PRIZE_RATE` is gone — the ratios vary), with an import-time check
  because a missing `repeatPrize` would silently pay 0 for every re-win.
  **Unconfirmed:** whether difficulty drives the AI or is only a label, the same open question
  as match length below. Bronze round 1 (Amateur → Semi-Pro) is the cheapest test. And the
  client clamps to its own `MIN_/MAX_DIFFICULTY_LEVEL`, so 5 is an assumed ceiling — if the Gold
  Cup's later rounds still read World Class, its max is 4.
  Every verifier now derives its expected payouts, prizes and round difficulty from these
  constants, so retuning any of them is not supposed to fail the suite. If it does, the failure
  is real.
- **Transfer market: rotating stock + a consumables counter** (`local_identity.py`, the `MARKET_*`
  constants). The market is synthetic and derived from the catalogue on every search, so it used
  to list all ~42,000 player auctions permanently.
  - **Rotation.** A card is live only in some rotations: membership is
    `blake2b(resourceId:rotation) % MARKET_ROTATION_FRACTION == 0`, with the rotation index being
    `now // MARKET_ROTATION_SECONDS` (30 min, 1-in-8 → ~5,200 listings). Deterministic on
    purpose: it needs no storage and, more importantly, cannot reshuffle between pages of one
    search. Set `MARKET_ROTATION_FRACTION = 1` to list everything again.
    Rotation gates **search only** — an already-known tradeId still buys, so a watched or
    part-bid listing does not evaporate mid-flow.
  - **Consumables** deliberately do *not* rotate: all 129 user-facing rows
    (`MARKET_CONSUMABLE_CATALOG`, which drops the client's internal position/playstyle records)
    are always stocked, 3 copies each, at a flat `MARKET_CONSUMABLE_BUY_NOW` = 500. Position
    changes and chemistry styles are tools; gating them behind a rotation would make the squad
    screen unusable for hours. Listings sort by copy index first so the opening page shows one of
    every kind rather than three rows of the same card.
    Buying uses its own trade-id space (`MARKET_CONSUMABLE_TRADE_ID_BASE`, between the synthetic
    player and user-listing bases) and skips the duplicate guard — owning several contracts is
    normal. Verified end to end: buy → New Items → assign to club → counted by `consumable_stats`.
  - **Unverified:** no capture of a non-player market search exists, so the `type` token vocabulary
    (`development` / `training` / `consumable[s]`) is inferred from `club_items`, which was built
    from real captures. `fut-market-search-query-beta2260` now logs every market query — check the
    `type_token` on the next capture. If the client sends something else, the consumable branch
    silently returns players.
- **Per-cup AI opponents** (`TOURNAMENT_TEAM_POOLS`). All four cups used to serve the same
  fifteen European giants, so a bronze starter club drew Barcelona in the Starter Cup. The
  mechanism was already in the protocol and unused: **CardsDLLzf.dll formats the request as
  `/teams?groupId=%d&count=%d`, and `groupId` is the cup's `aigroup`** — which we hard-coded to
  0 for every cup. Each cup now advertises its own `aigroup` (1-4) and `offline_tournament_teams`
  keys off it; an unknown or absent group falls back to `OFFLINE_COMPETITION_TEAM_IDS`, which is
  unchanged and still what Offline Seasons uses.

  | Cup | group | opponents | mean rating of best XVIII |
  |---|---|---|---:|
  | Starter | 1 | lower-league | 60.2 |
  | Bronze | 2 | second tier / smaller top flight | 66.7 |
  | Silver | 3 | established top flight | 74.4 |
  | Gold | 4 | the elite | 81.2 |

  Every club is in the client's own shipped kit **and** badge sets (587 clubs from `cards0.big`,
  per `fifa14-match-assets-v2411-beta222.json`) so the crest renders, and has ≥16 players in
  `PLAYER_CATALOG`, which is where the strength figures come from. The verifier asserts the
  *ladder* (each tier a real step up, Starter ≤ 65, no club in two tiers) rather than the means,
  so the pools can be retuned without a false regression.
  **A cup already in progress keeps the bracket baked into its `tournamentData` blob** — new
  opponents only appear in a cup started fresh.
  **Unverified:** no capture of `/tournament/teams` exists, so the `groupId` round-trip is
  inferred from the format string in the DLL. `fut-tournament-teams-group-beta2260` now logs the
  full query and the served ids — check it in the next capture. If `group_id` comes back `null`,
  the client is not sending it and every cup silently falls back to the shared pool.
- **Save edits (user-approved, all backed up):** DNF modifier reset 0.27 → 1.25; stale
  round-2 cup cleared; coin balance 97,190,165 → 0 **plus** the
  `local_test_balance_seeded_v24011` marker set, or the launcher re-seeds 500,000.
  Later: Starter Cup cleared again (it had reached round 4) using the server's own reset writes
  — the LOSS/DNF branch of `_settle_tournament_result_locked` — so a fresh cup could be tested;
  `won` left at 0 so its first-clear prize is still available. Coin grants of 15,000 and then a
  top-up to 1,000,000.
- **User settings, no code edits: `tools\fut_settings.cmd`** (menu; `tools/fut_settings.py` is the
  implementation). Edits match rewards (win/draw/loss/**dnf**), cup payouts, market rotation and
  the consumable price, plus two save actions: set the coin balance, and clear the club back to
  the starter squad.
  The tuning still lives as constants in `beta_identity.py` / `local_identity.py` — that is what
  the verifiers assert against — and `server/fut_local_settings.py` overlays a JSON file at
  import. The file sits next to the save
  (`%LOCALAPPDATA%\FIFA14LocalFUTBeta\local-fut-settings.json`) so it survives extracting a new
  build; `FIFA14_LOCAL_SETTINGS` points it elsewhere for tests.
  **The loader must never raise and never trust.** It is imported during startup, and the
  launcher discards its own output on failure (rule 2), so an exception there is a window that
  closes with no message. Malformed JSON, a non-object top level, and out-of-range or
  non-numeric values are each ignored or clamped with a stderr diagnostic; anything the file does
  not mention keeps the built-in. Covered in `verify_fifa14_beta2.py`.
  Because the launcher runs the verifier suite *before* startup and those verifiers derive their
  expectations from the constants, a user's settings are validated before the game boots.
  DNF was previously hard-zero in two places; both now read the configured value (default 0), and
  `sitecustomize.py`'s QUIT/DNF normaliser reports it too — the LOSS *shape* is what keeps FIFA
  out of fcc_logout, not the numbers, and reporting 0 while crediting the wallet would make the
  result screen contradict the balance.
- **`reset_club_to_starter()`** (BetaIdentityStore) reuses the fresh-profile branch of
  `ensure_beta_starter_club()` rather than defining a second idea of "starter", so a cleared club
  is identical to a first-run one. It keeps the wallet (clearing cards and setting a balance are
  separate decisions) and clears cup progress, packs, market listings and extra squads.
  It must also delete **`beta222_cosmetic_catalog_signature`**: the cosmetic seeder returns early
  on that marker, so leaving it gives you 23 players and no kits/badges/stadium.
  Note `ownedItems` on a real install is ~1,844 because the club owns the whole shipped cosmetic
  catalogue (1,173 kits + 587 badges + 61 stadiums, matching the asset scan). Count *players* to
  judge a wipe — the verifier fixture's stub asset report only has 3 cosmetics, hence its 29.
- **Test float: `tools/give_test_coins.ps1 [-Coins <target>]`** (was `give_100m_test_coins.ps1`,
  which despite the name asked for 1M). It **tops the club up to** the target instead of granting
  once. The old version keyed its grant on a fixed build reference
  (`BETA_CONSUMABLES_TEST_GRANT / BUILD / 2.41.1-beta2.24.2`), so once that ledger row existed —
  which it has since 2026-08-13 — every later run silently granted nothing. That is why it
  "stopped working". Re-running at or above the target is still a no-op, so it cannot stack.
  **Do not wire this into startup**: the launcher calls `prepare_fifa14_beta_state.py` *without*
  `--test-coins`, and topping up on every launch is the bug `ensure_local_test_balance()` exists
  to prevent (pack charges silently reappearing after a restart).
  One trap worth knowing, since the verifier caught it in the first attempt at this fix:
  `_wallet_write_locked` treats a repeated `(reason, reference_type, reference_id)` as
  already-applied and **returns the old row reporting success without moving the balance**. Two
  top-ups in the same second collided on a timestamp-only reference. The reference now includes
  the starting balance, and the method raises if the write comes back idempotent.
- **Upstream (in `sitecustomize.py`, not mine):** tournament resume fixed by returning exactly
  `round`, `tournamentData`, `dataVersion` **in that order** (CardsDLL parses responses as a
  stream — member order is part of the contract); QUIT/DNF normalised to a LOSS-shaped
  response. Also Blaze handler timeout 120→300 s.

## Fixed, pending in-game confirmation

### BUG-002 — new squads are not created (code fix done; verifiers pass, not yet seen in-game)
`POST /ut/game/fifa14/squad` with `{"id":0,...}` and no id in the path is now an explicit create.
Changes:
- `save_squad` (`local_identity.py`): `creating = requested_id in (None, 0) and document["id"] == 0`
  → its own INSERT, skips the sparse guard, and returns the listing with an extra
  **`createdSquadId`** key. A new squad does **not** become active unless it is the persona's
  first (an empty active squad would otherwise be what `create_match` fields).
- An empty `squadName` now means *unchanged* instead of `"Local XI"` — every retail
  `PUT /squad/{id}` omits the name, so the old coercion renamed a squad on the next player swap.
- The club/squad `pile` sweep asks "is this card in **any** squad", not just the saved one.
- `probe.py` responds with `squad_detail(createdSquadId)` — the frontend navigates by the id in
  that response (log: it GETs `/squad/{id}` immediately after), so returning the active squad is
  what bounced the UI back to the selector.
- `_repair_active_squad_locked`'s auto-fill now only runs for a persona with **one** squad. It
  used to refill any active squad holding <11 players from the club, which would overwrite a
  half-built new squad on every single-player PUT.
- That auto-fill was also the only thing keeping an under-filled squad away from the 22-entry
  entity table, so `create_match` now serves `playable_squad_document()` — the active squad if it
  can field 11, otherwise the fullest squad that can (`MIN_MATCH_SQUAD_PLAYERS`).
Regression coverage added to `verify_fifa14_beta2.py` (own temp DB, at the end of `main`): create,
copy, incremental build, name persistence, squad-1 untouched, CreateMatch fieldability.
**Still to check in-game:** the new squad appears in the selector and opens; and whether the
client needs `valid:false` / `newsquad:1` for an empty squad — `squad_list` reports `valid:true`
for every squad, which was fine when squad 1 was always full.

### BUG-007 — squad renames never stuck (fixed)
`save_squad` returned early on any PUT with no `players` array. That is right for the retail
tournament handoff (captured keys: `id`, `captain`, `kicktakers`) but the squad selector renames
through the *same* player-less shape — with `squadName` set — so every rename was swallowed. A
rename riding on a write the sparse guard rejects was swallowed too. Now: a player-less PUT that
carries a name goes to `_rename_squad` (metadata only, players untouched), and the sparse branch
applies a non-empty incoming name before it bails out. Every captured sparse write has an empty
`squadName`, so a non-empty one is a user rename, not the BETA 2.20 parser hiccup.

### BUG-006 — opening an "underway" cup crashed the client (fixed; the 2026-08-15 dump)
Symptom: kicked out of the Gold Cup mid-run, and afterwards loading the underway cup crashes.
Root cause, start to finish — `fifa-debug-string-beta2260` caught the client saying it:
```
02:50:51.602  GET /tournament/user/4 -> {"tournamentId":4,"round":2,"dataVersion":1,
                                         "tournamentData":"","progressDataVersion":1,"progressData":""}
02:50:51.644  parser consumes "tournamentId"
02:50:51.651  parser consumes "round"
02:50:51.657  parser consumes "dataVersion"      <- never reaches tournamentData
02:50:51.673  assert reporter: category "Global", name "EASTL vector"
02:50:51.676  Out of memory, allocating 808335154 bytes under name 'EASTL vector'
02:50:51.696  Stopping..                          -> STATUS_BREAKPOINT 0x1d42367, thread 20396
```
`808335154 == 0x302e3732 ==` the ASCII bytes **`"27.0"`** — read out of the middle of a
`127.0.0.1` URL string. With `tournamentData` empty the release adapter in `sitecustomize.py`
stops reshaping (`if not tournament_data: return saved`), so the raw six-member record goes out
in the wrong order; `dataVersion` decodes the buffer that happens to precede it instead of the
bracket, and takes its length from a URL.
How the save got there: winning a non-final round stores `round_value + 1` with a deliberately
blank `tournamentData` and waits for the client to PUT its own bracket. Kicked out before that
PUT → round 2 with no bracket, permanently. `_tournament_progress_is_resumable` returned True on
`round > 1` alone, so the cup was still advertised as Underway and served as a resume document.
**Fix:** that predicate now requires a non-empty `tournamentData` at any round. Both the list and
the read go through it, so a bracket-less cup is neither advertised nor served, and the client
starts the cup fresh instead. Save unpoisoned (backup `.pre-goldcup-unpoison`); the code fix
alone was enough — the cup already read as not-underway before the row was cleared.
`verify_fifa14_postmatch_beta2259.py` asserted the *old* behaviour, so read this before assuming
it regressed: it settled a won round and then required the cup to be advertised, without ever
performing the PUT the real client sends straight afterwards — it was asserting on a transient
state that only exists between settlement and the client's next request. It now checks that the
cup is **not** advertised until the bracket lands, performs the client's PUT (as captured on
2026-08-14), and only then requires the resume. The recorded observation it protects — a WIN must
not look like a fresh tournament — is intact.
Not implicated: the cup tiers. `/tournament/teams` was never requested this session
(`fut-tournament-teams-group-beta2260` count 0), so the `groupId` round-trip is *still*
unobserved — teams are only fetched when a cup is started fresh.

## Outstanding

### BUG-005 — resuming a tournament at round 2 bails before kickoff (2026-08-14 capture)
Symptom: launch round 2 of an in-progress cup → "could not reach Origin services" → hang on
"Leaving Ultimate Team". **This is not rule 4's tracer stall** — do not re-diagnose it as one.
What the capture proves (`redirect-probe.log` L662-686, tracer L1330-1380):
- Timeline: GET `/tournament/user/1` (round 2) 04:14:43 → PUT same 04:14:44 → POST `/match`
  04:14:47 → 9 healthy entity-table builds (`valid=22, uninitialized=0`) 04:14:55-04:15:01 →
  PUT `/squad/1` (captain/kicktakers) → PUT `/match` (MatchReady) 04:15:01.6, **200 at
  04:15:02.177** → nav to `fcc_logout` at 04:15:04.166.
- **Zero HTTP and zero Blaze traffic in the failure window.** The client did not try and fail to
  reach anything; it decided internally from data it already had. Blaze pings ran a perfect 30 s
  cadence to 04:14:37 and every match-path request answered in under a second.
- Ruled out: tracer stall (the heavy JSON-key trace disarmed at the transition —
  `cards-competition-trace-disarmed-on-transition-beta2259`, 6 keys, 3.4 s window); the
  duplicate-player crash (entity table clean, no WER event, the process never crashed); an
  assert (`fifa-assert-reporter-beta2259` armed, never fired); a server error (all 200s, resume
  document in the proven `round, tournamentData, dataVersion` order,
  `cards-cup-resume-lookup-beta2259` matched keys 1-4 with `will_fault: false`).
- The saved bracket blob was decompressed (4-byte length + gzip): it carries the bracket team IDs
  and no coin/prize figures, so the payout retune did not invalidate it.
- **The hang was ours.** `fifa-postmatch-fcc-logout-redirect-beta223` rewrote the logout view to
  GameHub with **0** `/match/end` requests in the whole session. The guard is meant for a
  completed match, but it also pre-arms from CreateMatch/MatchReady for 45 minutes, so it
  hijacked a bail-out that happened before kickoff and stranded a session that was already
  tearing down. Fixed: a pre-armed guard (no `/match/end` observed) now stays out of the way for
  the first `PRE_ARM_MIN_ELAPSED_MS` (90 s) and emits
  `fifa-prematch-fcc-logout-passthrough-beta2260` instead. A guard armed by a real `/match/end`
  is exempt and behaves as before.
- **Next capture should name the cause.** fifa14.exe narrates itself through OutputDebugString;
  those calls were already visible as `DBG_PRINTEXCEPTION_C` (0x40010006) system exceptions —
  40 in the session, **nine of them landing exactly on the failing MatchReady** — but the
  exception record does not carry the text. `hookDebugOutput` now reads it at the source and
  emits `fifa-debug-string-beta2260` (strings only, no backtrace, emit-budget capped). Re-run the
  same steps and read those lines around MatchReady first.
- Not yet known: whether a round-**1** launch still works. That comparison is the cheapest way to
  tell a resume-specific bug from a match-launch bug, and there is no known-good match capture to
  diff against (the last completed match predates every log in `artifacts/`).

### BUG-008 — Offline Seasons kicks back to the FUT menu (never worked; one hypothesis armed)
User report 2026-08-15: any interaction with Seasons returns to the FUT menu.
**No capture contains a single `/season/*` request** — not in any of the four logs — so the
current failure mode is unobserved on this build. Do not diagnose this from the existing logs.
What is already known, from the comment at the `/fut/items/pc/N.json` route in `probe.py`:
BETA 2.3 recorded the screen asking for `/fut/items/pc/0.json` **once per division immediately
after parsing season/list**, then abandoning before `season/user` was ever consumed. Same symptom.
The likely trigger, and what changed: season prize awards declared `assetId: 0`, so the client
tried to resolve award item 0 (its no-item sentinel) ten times and gave up. The cup ladder has no
such problem and uses a different award shape — `{"awardType": 1, "value": N, "halid": 0}`, an
award-type enum with no asset to resolve — which is proven in-game because cups render, pay and
settle. `_coin_award` now emits that shape. **This is a hypothesis with a named test, not a
confirmed fix.**
- `FIFA14_SEASON_AWARD_MODE=legacy` restores the `assetId` form, so both can be tried in one
  sitting without a rebuild.
- `fut-season-request-beta2260` logs every season request with the award mode and the prizeSet it
  served. The next capture separates the two candidate failures: **no such emit at all** means
  the screen never asks and the award shape is irrelevant — look upstream at the view/hub gate;
  **the emit followed by `/fut/items/pc/0.json` lookups** means the award change did not take.
Confirmed from the client binary (same method that settled `groupId`): CardsDLLzf.dll contains
`ut/%s/season`, `ut/%s/season/user`, `ut/%s/season/%%s/user`, `ut/%s/season/%%s/reset` and
`?divisionId=%d&seasonId=%d`, and the member names `prizeSet`, `prizeLevel`, `thresholdPoint`,
`awardMappings`, `numMatches`, `matchLengthMin`, `divisionId`, `seasonId` — so the record schema
itself is right. Both award spellings (`awardType`/`halid` and `type`/`assetId`/`halId`) exist in
the binary, which is why the strings alone cannot decide this and a capture is needed.

### BUG-004 — "DB ERROR" player card in packs
The card (rating 99, RWB, all attributes 1, England flag, Messi bio) is the **client's own
sentinel record**, not our data — no catalog entry matches those values, and the bio is a
separate record-0 fallback. Cause: `fifa14-player-catalog.v237.json` was built by scraping
WeFUT (16,515 rows → 10,330 players), so it contains assetIds the installed client's DB cannot
resolve. Verified: 0 of 2,397 owned items had an assetId missing from *our* catalog, so the gap
is on the client side. Fix direction: extract the client's real assetId set and filter
`PLAYER_CATALOG` at startup (same pattern as the existing `legend_client_ready` gate). Blocker:
there is no `data\db` folder in the install — the player DB is inside the BIG archives, so it
must be extracted first (`tools/scan_fifa14_client_db.py` only reads a loose file today).

### BUG-003 — store packs show no name/description (cosmetic; a lot already ruled out)
Confirmed by tracing, **do not repeat these**:
- The offer parser *does* consume `name` (13 offers + 26 currency names = 39) and
  `description` (13) — so the members reach it, and it never descends into the
  `packList`/`packTypes` compatibility copies.
- The values are resolved as **localization keys** (blank, not the literal token).
- `leaderboards.ENG_US.xml` **is** parsed and accepted; pack tokens were added to it and it
  changed nothing → the Store does **not** resolve against the locstrings table.
- `storepackdescriptions.en_us.xml` is fetched 638 ms *after* the offers (the designed flow)
  but **never reaches the FUT locstrings parser** → it has a separate, unidentified consumer.
- Response headers for both documents are identical.
- **Do not switch it to trans-unit/XLIFF**: `verify_fifa14_v237_install.py` guards the source
  (`if '<trans-unit id=' in probe_source`) because that format rendered visible NOT FOUND text.
Remaining options: instrument CardsDLL's generic response path to find the consumer of that
4,186-byte buffer, or decompress `Data\loc\locale.big` to find a shipped example of the format.

### Match length
Menu label follows our `matchlength`, gameplay ignores it (still 6 minutes even with FIFA's own
Game Settings → Half Length at 4). `HALF_LENGTH` is in the client's `eGSParams` block. Left at
upstream's 6 so the label doesn't lie. Would need a runtime override via the tracer — **not** an
archive write.

## Diagnostics currently armed (tracer, all read-only, signature-checked)

| Emit prefix | Purpose |
|---|---|
| `fifa-match-entity-table-beta2259` | 22-entry match entity table at build time; `valid=22` = healthy. Regression canary for the duplicate-player crash. |
| `fifa-assert-reporter-beta2259` | fifa14.exe assert reporter — prints the assertion expression/file/line before the `int 3`. |
| `cards-cup-resume-lookup-beta2259` | Cup resume registry lookup; logs the key wanted vs the keys registered. |
| `cards-competition-trace-cost-beta2259` | Per-window key count and duration — use this to prove/disprove a tracer stall. |
| `fifa-debug-string-beta2260` | fifa14.exe's own `OutputDebugString` text. The client's account of what it thinks went wrong; read this first. It named BUG-006 outright ("Out of memory, allocating 808335154 bytes"). Boot spam from the DLC plugin loader is normal — ~110 lines per session, well inside the emit cap. |
| `fifa-prematch-fcc-logout-passthrough-beta2260` | The post-match logout guard declined to rewrite a logout because no match had been played yet (see BUG-005). |

Minidump analysis: dumps are minidumps with only a ~256-byte code window around EIP plus thread
stacks (no heap). A small pure-Python parser (header → streams → ExceptionStream/ModuleList/
ThreadList, x86 CONTEXT: EIP@184, ESP@196, EBP@180) is enough to get the faulting instruction,
registers and module+RVA — that's how both crashes were identified.
