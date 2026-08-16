<!--
Generated from the maintainer's working handoff. Absolute paths are genericised:
  <repo>   this repository
  %LOCALAPPDATA%\FIFA14LocalFUTBeta   the persistent save folder
-->
# FIFA 14 Local FUT — working handoff

Local/community FIFA 14 FUT server. Local tooling only: do not target EA/FUT services,
do not bypass protections, never print credentials.

**Start here next session: Offline Seasons (BUG-008).** It is the only feature still
broken end to end, the root cause is found, and the fix is written but unconfirmed in
game. Do that before the pack manager (#6) or tournament names/icons (#8).

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
5. **Before writing to the DB or game files**: back up, and confirm neither `probe.py`
   nor `fifa14.exe` is running.
6. Assert-style crashes surface as `STATUS_BREAKPOINT` at an `int 3` with no message.
7. **Mine the captures before theorising.** Three bugs were found purely by grepping
   real queries out of `redirect-probe.log`: `type=equippables`, `pos=CAM-CF`,
   `divisionList=11`. An unhandled query token returns an *empty result*, not an error,
   so it looks like a broken feature rather than a parsing gap.
8. **Ask the client binary.** `CardsDLLzf.dll` contains the URL format strings and wire
   member names. `/teams?groupId=%d&count=%d` and the season routes were both settled by
   grepping it, not by guessing.
9. PowerShell 5.1: any native stderr line becomes a `NativeCommandError` under
   `-ErrorAction Stop`, even on exit 0. `-Include` with a wildcard-free `-LiteralPath`
   is silently ignored and yields *every* file (this deleted a whole staged tree once).

## Current state

Everything below is fixed and verifier-covered unless marked otherwise.

**Squads.** Create/copy squad (`POST /squad` with `"id":0` now has a real create branch;
the response must carry the new id because the frontend navigates by it). Renames apply
from a player-less PUT, and from a name carried on a sparse write. An empty `squadName`
means unchanged. A new squad does not become active unless it is the first.

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

Symptom history: interacting with Seasons returned to the FUT menu; after the first fix,
"seasons are currently unavailable". Two faults, both addressed, **neither confirmed in
game**.

*Fault 1 (confirmed fixed by capture).* Season prize awards declared `assetId: 0`, so the
screen resolved award item 0 per division and abandoned before `season/user`. `_coin_award`
now emits the cup ladder's proven `{"awardType": 1, "value": N, "halid": 0}`. The
2026-08-15 capture proves the flow moved: item lookups became `/fut/items/pc/-1.json`,
`season/user` was requested for the first time, and the tracer shows 981 keys parsed from
the list then `seasonId`/`divisionId`/`round` cleanly. No crash, no error string.
`FIFA14_SEASON_AWARD_MODE=legacy` restores the old form.

*Fault 2 (the blocker).* The client asks
`GET /season/list?active=true&count=99&divisionList=11&type=offline`. The ladder was
divisions 10..1, so nothing matched. Nothing in `/user` carries an offline division (the
client has `divisionOffline`/`offlineDivision`/`GetUsersOfflineDivision`), so it defaults
to 11. Fixes: division 11 is now the entry division (ladder 11..1);
`offline_seasons_list(query)` honours `divisionList` and falls back to the whole ladder for
an unknown division; `offline_season_user` derives its division from the ladder entry and
its `seasonId` selects that record. **The list and season/user must agree on the division
or the screen has nothing to show.**

**Next step:** run Seasons on 0.3 beta and capture. Expect one division-11 record from
`season/list`, then `season/user`, then a playable screen. `fut-season-request-beta2260`
logs every season request with the award mode and the prizeSet served. If it opens, the
remaining work is whether a season match actually starts and settles.

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

### Market gaps
The client searches the market with `type=stadium` (captured), which returns nothing
because only players and consumables are listed. Making cosmetics tradeable is a feature.

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
| `fifa-debug-string-beta2260` | fifa14.exe's own `OutputDebugString` text. **Read this first.** It named BUG-006 outright ("Out of memory, allocating 808335154 bytes"). ~110 lines/session of DLC loader boot spam is normal. |
| `fifa-match-entity-table-beta2259` | 22-entry match entity table; `valid=22` = healthy. Regression canary for the duplicate-player crash. |
| `fifa-assert-reporter-beta2259` | Assert reporter; prints the assertion expression/file/line before the `int 3`. Gave the allocation category/name for BUG-006. |
| `cards-cup-resume-lookup-beta2259` | Cup resume registry lookup; key wanted vs keys registered. |
| `cards-competition-trace-cost-beta2259` | Per-window key count and duration; use to prove/disprove a tracer stall. |
| `fut-season-request-beta2260` | Every season request, with award mode and served prizeSet. |
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
