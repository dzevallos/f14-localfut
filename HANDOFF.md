<!--
Generated from the maintainer's working handoff. Absolute paths are genericised:
  <repo>   this repository
  %LOCALAPPDATA%\FIFA14LocalFUTBeta   the persistent save folder
-->
# FIFA 14 Local FUT — working handoff

Local/community FIFA 14 FUT server. Local tooling only: do not target EA/FUT services,
do not bypass protections, never print credentials.

**Start here next session: run one offline season and capture it (BUG-008).** The screen
opens and a match plays; what was still missing — the save coming back, and a ladder with
real tiers — is written and verifier-covered but unconfirmed in game. One capture answers
the last open question (who owns the offline division). Do that before the pack manager
(#6) or tournament names/icons (#8).

## Where things live

| What | Path |
|---|---|
| **Active tree** | `<repo>` |
| Superseded tree | `<superseded tree>` (older revision; ignore) |
| Live save (SQLite) | `%LOCALAPPDATA%\FIFA14LocalFUTBeta\local-fut-beta-v2410.sqlite3` |
| Save backups | same folder, `backups\` |
| User settings | same folder, `local-fut-settings.json` |
| Debug zip contents | `<active tree>\artifacts\` (`redirect-probe.log`, `frida-pc-fut-nav-route-patch.log`) |
| Crash dumps | `<crash dump folder>\` |
| Older evidence + UI static extracts | `<evidence folder>\` |
| GitHub | `dzevallos/f14-localfut`, public fork of `KyroGeorge2/FIFA-14-Local-FUT`. `gh` is installed and authed. |

Architecture: `server/probe.py` (HTTP + Blaze routes), `server/local_identity.py`
(`LocalIdentityStore`: items, squads, market, club), `server/beta_identity.py`
(`BetaIdentityStore`: economy, cups, seasons, matches), `server/fut_local_settings.py`
(user settings loader), `server/sitecustomize.py` (upstream release adapter, wraps the
store at import), `tools/frida_pc_fut_nav_route_patch_trace.py` (Frida agent, embedded
JS in `agent = r"""..."""`).

## Operating rules (learned the hard way)

1. **`emit()` in probe.py prints JSON to stdout, and stdout IS `redirect-probe.log`.**
   Never `print()` to stdout from server code. Use `_diagnostic()` (stderr).
2. **The launcher runs verifiers before startup and `throw`s on any non-zero exit**,
   capturing their output into a variable, so the window closes with nothing shown.
   Symptom: "crashes out without a log". Always run the suite after changing behaviour:
   ```bash
   cd <tree>/tools && for v in verify_fifa14_v237_install.py verify_fifa14_beta2.py \
     verify_fifa14_postmatch_beta2259.py verify_fifa14_consumables_beta224.py \
     verify_fifa14_pack_ui_performance_beta2250.py verify_fifa14_market_beta2250.py \
     verify_fifa14_regressions_beta2258.py verify_fifa14_postmatch_beta2256.py; do
     python "$v" >/dev/null 2>&1 && echo "PASS $v" || echo "FAIL $v"; done
   ```
   (`verify_fifa14_pack_ui_performance_beta2244.py` fails pre-existing and is unused.)
   **Corollary: verify every *setting*, not just the default.** A user picking dynamic
   rewards once failed three verifiers, which would have stopped the game booting.
3. **Verifiers encode recorded in-game observations.** Do not weaken a guard to fit an
   inference; get an observation first. But an assertion can encode *our own tuning*
   rather than a client contract, and a new capture beats it: the "ten season records"
   check was our ladder size, and the client asking for division 11 superseded it.
4. **The Frida JSON-key hook is expensive** (~7 ms/key). Never arm it across a gameplay
   transition; a ~14 s stall blows the Blaze keepalive. Guards in place:
   `HEAVY_TRACE_FORBIDDEN_KINDS`, `CHEAP_TRACE_KINDS`, an emit-budget gate, per-window
   cost accounting.
   **4a. The emit budget does not bound the *work*, only the logging.** `emit()` caps at
   1000 records per kind, but everything computed before the call still runs forever. The
   crash exception handler built a 32-frame `Backtracer.ACCURATE` walk plus a module
   lookup per frame on every exception — and every `OutputDebugString` reaches it as a
   DBG_PRINTEXCEPTION_C. A tester with a joystick FIFA does not recognise generated ~26
   of those a second for a whole match (2026-08-16 report A: "frame rate lower than
   usual", 26 minutes of wall clock for a 6-minute match). Put the budget check *first*,
   and never let a diagnostic pay full price for an event another hook already records.
5. **Before writing to the DB or game files**: back up, and confirm neither `probe.py`
   nor `fifa14.exe` is running.
6. Assert-style crashes surface as `STATUS_BREAKPOINT` at an `int 3` with no message.
7. **Mine the captures before theorising.** Three bugs were found purely by grepping
   real queries out of `redirect-probe.log`: `type=equippables`, `pos=CAM-CF`,
   `divisionList=11`. An unhandled query token returns an *empty result*, not an error,
   so it looks like a broken feature rather than a parsing gap.
8. **Ask the client binary.** `CardsDLLzf.dll` contains the URL format strings and wire
   member names. `/teams?groupId=%d&count=%d` and the season routes were both settled by
   grepping it, not by guessing. Beyond strings: `pip install capstone` into `.venv` and
   `scratchpad/pe_xref.py` maps file offset ↔ VA, finds immediate/call xrefs and
   disassembles a window with string annotations. That is how the season-list URL builder
   (`0x10151c60`) was traced back to a division vector on the client's own season manager.
   The JSON member names live in one sorted `const char*` table (`0x1d2bf0`, 583 entries),
   so a parser refers to keys by index, not by string — no xref will point at them.
9. PowerShell 5.1: any native stderr line becomes a `NativeCommandError` under
   `-ErrorAction Stop`, even on exit 0. `-Include` with a wildcard-free `-LiteralPath`
   is silently ignored and yields *every* file (this deleted a whole staged tree once).

## Current state

Everything below is fixed and verifier-covered unless marked otherwise.

**Squads.** Create/copy squad (`POST /squad` with `"id":0` now has a real create branch;
the response must carry the new id because the frontend navigates by it). Renames apply
from a player-less PUT, and from a name carried on a sparse write. An empty `squadName`
means unchanged. A new squad does not become active unless it is the first.

**Seasons.** Eleven divisions, 11 (entry) to 1, ten fixtures each, its own club pool per
division. Progress is the client's own save round-tripped through
`beta_season_progress`; the server tallies points and settles promotion, relegation and
the prize itself. See BUG-008 below — written and verifier-covered, not yet confirmed in
game.

**Cups.** Per-cup opponents via the client's own `aigroup`/`groupId` mechanism
(`TOURNAMENT_TEAM_POOLS`, ~60/67/74/81 average rating). Difficulty ladder Amateur to
Legendary (index: 0 Beginner … 5 Legendary; the tile shows round 1). Resuming a cup with
no saved bracket no longer crashes the client. Prizes and payouts configurable.

**Market.** Rotating stock (deterministic hash of card and rotation; a fraction listed at
a time). Consumables always stocked at a flat price, and buying one puts a usable item in
the club. Listings shuffled per rotation rather than sorted by rating. Compound positions
(`pos=CAM-CF`) work.

**Club.** `type=equippables` returns kits/stadiums/badges. `reset_club_to_starter()`
reuses the fresh-profile provisioning path, keeps the wallet, and clears the W-D-L record.
It must also delete `beta222_cosmetic_catalog_signature` or cosmetics are not rebuilt.
`ownedItems` is ~1,844 on a real install because the club owns the whole shipped cosmetic
catalogue; count *players* to judge a wipe.

**Economy.** `MATCH_RESULT_FLAT_COINS` WIN 15000 / DRAW 1000 / LOSS 750, DNF configurable
(default 0). `MATCH_REWARD_MODE` flat or dynamic; dynamic restores the minutes-scaled plus
stat-derived payout, which was never removed, only overridden at the last step. Cup prizes
50k/25k, 100k/50k, 250k/100k, 2.5M/750k first clear / repeat. Round coins follow the WIN
payout by design.

**Settings.** `FUT_SETTINGS.cmd` edits rewards, mode, cup payouts, market stock, coin and
FIFA Point balances, and clears the club. `server/fut_local_settings.py` overlays a JSON
file next to the save; it must **never raise and never trust** (it is imported at startup,
and rule 2 turns an exception into a window that closes silently). `FIFA14_LOCAL_SETTINGS`
points it elsewhere for tests.

**Release.** `PACKAGE_RELEASE.cmd` verifies the *staged package* before zipping and
refuses to build if the verifiers fail. It excludes `config.local.psd1`, `*.lnk`,
`local-fut-settings.json`, and prunes `__pycache__` **last**, after anything that runs
Python. A `.pyc` embeds the absolute path it was compiled from, so it leaks the builder's
user name. Both of those were shipped once each; check the artifact, not the working tree.

## Next: BUG-008 — Offline Seasons

Symptom history: interacting with Seasons returned to the FUT menu; then "seasons are
currently unavailable"; then (2026-08-16) **the screen opens and a match plays**, but the
result never came back — no "underway", no saved progress — and the one season on offer
scheduled Barcelona, Real and Bayern against a bronze starter squad.

*Faults 1 and 2 (fixed, confirmed by the 2026-08-16 capture).* The `assetId: 0` prize award
and the ladder that did not contain division 11. The screen now renders: `season/list`
returns one division-11 record, `season/user` is requested, the fixture list draws, and a
match kicks off and settles. `FIFA14_SEASON_AWARD_MODE=legacy` still restores the old award
form.

*Fault 3 (fixed, unconfirmed in game).* **Every `/season/*` route except `season/user`
returned the season list.** The client saves its season with
`PUT /season/<id>/division/<div>/user`, body
`{"round":N,"dataVersion":1,"data":"<b64>","progressDataVersion":1,"progressData":"<b64>"}`
— it writes once before kickoff and again after the match with the round advanced (the
2026-08-16 capture has round 1 before, round 2 after, `data` a 4-byte-length-prefixed gzip
of its own season state). That write was parsed as a season-list request and thrown away,
and `season/user` answered from `beta_offline_seasons`, a seeded tally nothing updated —
so it reported **season 2 / division 10 against a list holding only season 1 / division
11**. Nothing resolved, nothing was underway.

Now: `beta_season_progress` (one row per club) stores round + both opaque blobs and is
echoed back by `season/user`; `beta_season_history` backs `season/user/history`, which
used to answer with the *active* list. Routes added for the save/load, `season/<id>/reset`,
and history.

*Fault 4 (fixed, unconfirmed in game).* All eleven divisions scheduled the same ten
European giants, so the ladder had tiers on paper only. `SEASON_TEAM_POOLS` now gives each
division its own ten clubs, ~60 mean rating at division 11 climbing to ~82 at division 1,
picked only from clubs the installed client can render (badge *and* kit in the extracted
match assets, ≥16 players in the catalogue). Regenerate with
`scratchpad/season_pools.py`. Difficulty ramps 1→5 across the ladder, +1 for the closing
three fixtures.

*Promotion.* The server tallies season fixtures itself (3/1/0; a DNF is a loss) because the
client never reports the outcome. A match counts as a season fixture when it carries no
`tournamentId` and the Seasons screen saved within `SEASON_MATCH_SAVE_WINDOW_SECONDS`
(the captured gap is under a second). When the fixtures run out the season settles on the
served thresholds — championship/promotion/maintenance/relegation — pays that prize into
the wallet, writes history and opens the next season one division up or down. The rollover
is deliberately **lazy** (it happens on the next season read, not at settle) so the
client's trailing round-11 write lands on the season it belongs to.

**The one open question: who owns the offline division.** `GetUsersOfflineDivision` reads
a member of the client's own season manager (vtable+0x28 of the singleton at
`0x101d62d8`), and the season-list request copies its division vector straight out of that
object (`+0x1c` → the `&divisionList=` builder at `0x10151c60`). Whether the client takes
that number from `season/user`'s `divisionId` is still unproven; in the 2026-08-16 capture
it kept asking for 11 while we answered 10, but our answer was unresolvable, so that
proves nothing. Hedge in place: the list always serves the club's current division
*alongside* whatever was asked for, so a promoted season's new `seasonId` resolves against
a record the client has actually seen.

**Next step:** play a season on the fixed build and capture. `fut-season-request-beta2260`
now logs `divisions` served and `current_division` on every season request — if a promotion
happens and the next `season/list` asks for the new division, the client follows us and the
ladder is done; if it keeps asking for the old one, serve the current tier's *content*
under the requested division id instead. If the screen instead breaks on the restored save,
`FIFA14_SEASON_SAVE_MODE=round` drops `season/user` back to the three members the capture
proves parse, without losing the stored progress.

*Wire order matters here.* `season/user` is FutSeasonLoadData (`?type=offline` sits next to
`RS4:FutSeasonLoadDataServerResponse`), and this parser family reads a response as a
stream: the cup resume had to be reshaped to `{round, tournamentData, dataVersion}` because
`dataVersion` decodes the buffer *immediately before it* (that is BUG-006's 808 MB
allocation). The season document follows the same rule — each buffer first, then its
version — and a buffer is only ever sent when it is non-empty.

## Outstanding

### BUG-005 — intermittent bail-out during match setup
"Could not reach Origin services" then a hang, once, on 2026-08-14. **Not the rule-4
tracer stall** (it disarmed cleanly; Blaze pings held a 30 s cadence). Zero HTTP and zero
Blaze traffic between MatchReady's 200 and the logout nav, so the client decided
internally. Two matches immediately afterwards completed normally, so it is intermittent,
not a hard block. The "Leaving Ultimate Team" hang part was ours and is fixed: the
post-match logout guard pre-armed from CreateMatch and hijacked a pre-kickoff logout; it
now stands down unless a match could plausibly have been played
(`PRE_ARM_MIN_ELAPSED_MS`, `fifa-prematch-fcc-logout-passthrough-beta2260`).

### #6 — pack manager (GitHub, feature)
Self-give packs and editable store packs are close to free: the generator exists and pack
definitions already carry price/contents/weighting. Tournament pack *rewards* are risky:
cups award coins through a prize schema, and awarding an item means a different award
type, which is exactly what broke Seasons.

### #8 / BUG-003 — tournament and store names/icons blank (cosmetic)
Same root cause. Confirmed, **do not repeat**: the offer parser does consume `name` and
`description`; the values resolve as localization keys (blank, not the literal token);
`leaderboards.ENG_US.xml` is parsed and accepted but adding tokens changed nothing;
`storepackdescriptions.en_us.xml` is fetched 638 ms after the offers but never reaches the
FUT locstrings parser, so it has a separate unidentified consumer. **Do not switch to
trans-unit/XLIFF** (`verify_fifa14_v237_install.py` guards it; that format rendered NOT
FOUND). Remaining options: instrument CardsDLL's generic response path to find the
consumer of that 4,186-byte buffer, or decompress `Data\loc\locale.big` for a shipped
example.

### BUG-004 — "DB ERROR" player card in packs
The card is the client's own sentinel record, not our data. `fifa14-player-catalog.v237.json`
was scraped from WeFUT so it contains assetIds the installed client cannot resolve. 0 of
2,397 owned items had an assetId missing from *our* catalog, so the gap is client-side.
Fix direction: extract the client's real assetId set and filter `PLAYER_CATALOG` at startup.
Blocker: no `data\db` folder in the install; the player DB is inside the BIG archives.

### Market gaps (#11, #13)
The 2026-08-16 tester capture (`reports/…results(1).zip`) has all 45 market searches the
Transfer Market tabs make, with what each one served. Two were **our bug and are fixed**:
the query token was compared raw against a normalized catalogue name, so `cat=GKTraining`,
`cat=position` and `cat=playStyle` matched by luck (63/60/57) while `cat=playerTraining`
(catalogue "Training", 21 cards) and `cat=managerLeagueModifier` ("Manager League", 8)
served nothing. Both now go through `_consumable_filter_category`; an unknown category
still returns empty rather than falling back to everything.

The other five tabs are still empty, and **not for want of code** — there is nothing
legitimate to put in them. Each now says so once per session through `_diagnostic`
(`_market_unstocked_family`) instead of returning a silent empty page that reads as a
broken feature:

- `type=clubInfo&cat=kit|badge` and `type=stadium`: **the club already owns the entire
  shipped cosmetic catalogue** — 1,173 kits, 587 badges, 61 stadiums, counted straight out
  of the tester's own save. An empty tab is the correct answer; there is nothing left to
  sell. Making these tabs meaningful means provisioning a starter club with *some*
  cosmetics instead of all of them, which takes items away from existing clubs — a
  product decision, not a bug fix.
- `type=staff`: **unblocked — the client ships the real tables.** `manager-catalog.v237.json`
  was the wrong source (scraped metadata, `liveEmissionEnabled: false`, unverified IDs).
  `cards_ng_db.db` — the same archive the kit/stadium/badge scan already reads — has five
  staff card tables whose `carddbid` *is* the FUT resource id, so nothing has to be
  guessed and the BUG-004 risk goes away:

  | table | cards | id range | payload |
  |---|---|---|---|
  | `managercards` | 166 | 1000500-1000775 | talkrating, negotiation, nation, formationid |
  | `physiocards` | 42 | 4000001-4000042 | attribute, amount |
  | `headcoachcards` | 36 | 2000001-2000037 | attribute, amount |
  | `gkcoachcards` | 36 | 9000001-9000045 | attribute, amount |
  | `fitnesscoachcards` | 36 | 3000004-3000039 | posbonus, fieldpos, amount |

  `tools/scan_fifa14_staff_cards.py` extracts them read-only into
  `server/fifa14-staff-catalog.v2411.json` (316 cards, ratings 54-88, no duplicate
  resource ids). Regenerate with
  `python scan_fifa14_staff_cards.py --game-root "<FIFA 14 folder>" --output ../server/fifa14-staff-catalog.v2411.json`.

  **Remaining for #11's staff tabs: the wire shape.** No capture of a staff ItemData
  exists, so how the client wants a manager/coach/physio card serialised is the open
  question — model it on the proven consumable listing path
  (`_market_search_consumables` / `_market_consumable_auction` / the buy-and-grant in
  `market_bid`), give it its own trade-id base like `MARKET_CONSUMABLE_TRADE_ID_BASE`,
  and expect one capture to settle it. `fcc_misccards` (22 rows, cardsubtype 231, an
  `amount` that looks like a coin value) is worth a look at the same time.
- `type=ball`: still nothing. There is no FUT ball *card* table in `cards_ng_db.db`;
  `teamballs` (131 rows) and `career_leagueballs` live in `fifa_ng_db.db` and describe
  which match ball a team or league plays with, not a purchasable item, and `dlcballs`
  is empty. FIFA 14 FUT may simply not sell balls.

**#13 — market ordering (fixed).** Listings are now ordered by time remaining, closest
first, and `expires` is a genuine countdown rather than a constant out of a five-value
table. Each listing ends after the rotation it belongs to, staggered by a per-card offset
inside that card's own duration (`_market_listing_remaining`).

The obvious implementation — a per-card cycle running continuously — was written first and
thrown away, because it re-sorted the head of a 5,126-listing market every second and
page one was permanently a wall of identical `60s` rows. Anchoring expiry to the rotation
gives all three properties at once: the numbers tick down second by second, the *order*
holds still for the whole rotation so paging cannot shuffle underneath the client, and
nothing drains away mid-rotation. Turning stock over stays the rotation's job.

Consumables keep their flat `expires: 3600` and their one-of-each ordering on purpose: a
shop counter should not run out mid-browse, and with every row on the same timer an expiry
sort would be a no-op that only made the tab harder to browse.

Not done, and deliberately: the real-time market the tester also asked for (expiries and
bids that persist across sessions, a bidding bot). That is a much larger feature and they
put it behind the ordering fix themselves.

### #12 — player stats (fixed; the slot order still needs one look)
Root cause, confirmed by reading the item payloads out of the tester's save: every card's
`statsList` and `lifetimeStats` (five entries each) was all zeroes after matches.
`_apply_match_end_items_locked` did read the per-item `goals`/`assists` the client submits
in `/match/end`, but wrote them to `payload["lifetimeGoals"]` — a member the client's JSON
key table does not contain, so nothing ever read it — and never touched the two arrays the
client *does* parse (`lifetimeStats` is key id 268, `statsList` id 503). Games played was
not counted anywhere at all.

Now `_bump_card_stats` accumulates goals, assists, yellows, reds and one appearance per
card into both arrays (and their `statsArray`/`lifetimeStatsArray` aliases, or the
canonicaliser drops whichever it was not given). Appearances go to the eleven that
actually started — the same `_matchReadyItemIds` set the contract decrement uses — and
only on the first settlement, so a retry cannot double-count. `_array_values` no longer
clamps a stat at 99 (`PLAYER_STAT_MAX`); that ceiling belongs to attributes.

**Open: which slot is which.** `PLAYER_CARD_STAT_INDEX` currently reads
`goals, assists, redCards, yellowCards, gamesPlayed`. That is an inference from a verified
precedent, not an observation: CardsDLLzf.dll lays its match-stat enum names out in
*reverse* enum order (`0x1afbbc`..`0x1afc54` reads OFFSIDES, RED_CARDS … SHOTS_ON_TARGET
against a captured member order of goals, shotsOnTarget … redCards, offsides), and the
same reversal applied to the frontend's player block (`0x191f10`..`0x191f68`:
PLAYER_GAMESPLAYED, PLAYER_YELLOWCARDS, PLAYER_REDCARDS, PLAYER_ASSISTS, PLAYER_GOALS)
puts goals first. Read the block forwards instead and the mapping is the exact opposite,
so do not argue it — **run `FIFA14_PLAYER_STAT_PROBE=1`, open any card, and read the
labels.** Slots 0..4 come out as 11/22/33/44/55, so one screenshot decodes it; flip the
constant if it disagrees. The probe is applied in `build_fut_json_payload`, i.e. at the
serialization boundary, precisely because `_canonical_player_payload` has twenty call
sites and most of them write their result back — injecting sentinels there would persist
them over the user's real career totals.

### Club venue / default stadium
**The default venue is Forest Park, stadium ID 26 / carddbid 6200010, rating 64.**
Recovered from the maintainer's own save, which had Forest Park equipped: its
`activeStadium` item was id 26. That is also the only way to get this mapping — see
point 3.

The venue is now one setting instead of four hardcoded copies. `club.stadiumId` and
`club.stadiumName` in the settings file override `DEFAULT_STARTER_STADIUM_ID` / `_NAME`,
and the Frida agent reads the same file (`__LOCAL_OFFLINE_STADIUM_ID__` is substituted at
build time) so the venue the club owns and the one forced onto the client's native offline
stadium provider can no longer drift apart.

Three things to know before changing it:

1. **An id and a resource id are not interchangeable.** `_configured_stadium_row` resolves
   a configured id out of the shipped catalogue so assetId and carddbid come from the same
   row, and refuses an id the client does not ship (keeping Town Park and saying so). The
   first attempt at this patched the id into Town Park's row and produced assetId 42 with
   carddbid 6200016 — a venue that does not exist, which is a crash, not a wrong picture.
2. **The verifier pins the invariant, not the number** — active stadium == the resolved
   starter stadium, id and carddbid from one row. Pinning "34 / 6200016" would make
   changing the setting stop the game booting (rule 2's corollary).
3. **Stadium names are not in the install, and the client ignores ours.** Searching every
   file for "Town Park" — a name this build already used — finds nothing, in any encoding.
   The save proves the other half: our label for stadium 26 was the generated placeholder
   "Stadium 26" while the game displayed "Forest Park", so **the client renders stadium
   names from its own localization** and `name` is a server-side label only. Consequence:
   a venue can only be identified by *id*, and the reliable way to learn an id is to
   equip the stadium in game and read `activeStadium` out of the save:

   ```sql
   SELECT payload FROM items WHERE item_type='stadium';  -- the one with itemState=activeStadium
   ```

A club that has active cosmetics but no active stadium now falls back to the configured
venue rather than serving a short actives list. Deliberately not applied to a *fresh*
club: having no active cosmetics at all until the retail client selects them is a verified
contract, and fabricating one there would fake a choice the user has not made.

### Match length
Menu label follows our `matchlength`; gameplay ignores it (`HALF_LENGTH` is in the client's
`eGSParams`). Left at 6 so the label does not lie. Would need a runtime override via the
tracer, **not** an archive write.

### Unconfirmed inferences
- Whether round difficulty reaches the AI or is only a label (match length is label-only,
  so it may be too). Bronze Cup round 1 is the cheapest test.
- Whether Legendary (5) is the client's ceiling; it clamps to its own
  `MIN_/MAX_DIFFICULTY_LEVEL`.
- The `groupId` round trip, inferred from the DLL format string. No capture of
  `/tournament/teams` exists because it is only requested when starting a *fresh* cup.
  `fut-tournament-teams-group-beta2260` logs it.

## Diagnostics currently armed (tracer, all read-only, signature-checked)

| Emit prefix | Purpose |
|---|---|
| `fifa-debug-string-beta2260` | fifa14.exe's own `OutputDebugString` text. **Read this first.** It named BUG-006 outright ("Out of memory, allocating 808335154 bytes"). Boot spam is normal; a repeat now collapses into `fifa-debug-string-repeat-beta2261` with a count instead of burning the 1000-line budget in 17 seconds. |
| `fifa-debug-string-repeat-beta2261` | How many times the previous line repeated. A four-figure count here is the game shouting about something every frame — an unrecognised input device did exactly that in the 2026-08-16 report. |
| `fifa-match-entity-table-beta2259` | 22-entry match entity table; `valid=22` = healthy. Regression canary for the duplicate-player crash. |
| `fifa-assert-reporter-beta2259` | Assert reporter; prints the assertion expression/file/line before the `int 3`. Gave the allocation category/name for BUG-006. |
| `cards-cup-resume-lookup-beta2259` | Cup resume registry lookup; key wanted vs keys registered. |
| `cards-competition-trace-cost-beta2259` | Per-window key count and duration; use to prove/disprove a tracer stall. |
| `fut-season-request-beta2260` | Every season request, with award mode, served prizeSet, the `divisions` served and the club's `current_division`. The last two answer whether the client follows the division we report. |
| `fut-tournament-teams-group-beta2260` | The `/tournament/teams` query and served ids. |
| `fifa-prematch-fcc-logout-passthrough-beta2260` | The logout guard declined to rewrite a pre-kickoff logout. |

Minidump analysis: dumps are minidumps with a ~256-byte code window around EIP plus thread
stacks (no heap). A small pure-Python parser (header → streams → ExceptionStream/ModuleList/
ThreadList, x86 CONTEXT: EIP@184, ESP@196, EBP@180) is enough for the faulting instruction,
registers and module+RVA. `0x80000003` = STATUS_BREAKPOINT is the memory-exhausted abort
path, not a separate fault.

## Released

`0.3 beta` is current (Latest). `0.2 beta` could not start at all; `0.2.1` fixed it and its
notes point forward. Tester `duckiest428` filed 8 issues; #1,2,3,4,5,7 are shipped, #6 and
#8 remain. All issues have replies. Regenerate the repo's `HANDOFF.md` from this file with
`scratchpad/make_repo_handoff.py` (it genericises local paths) before pushing.
