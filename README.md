# FIFA 14 Local FUT — adjusted fork

A fork of **[KyroGeorge2/FIFA-14-Local-FUT](https://github.com/KyroGeorge2/FIFA-14-Local-FUT)**
(BETA 2.25.9), with squad, tournament, market and economy work on top. Everything here
is local server behaviour, local tuning and read-only diagnostics against a game you
already own — nothing targets EA or FUT services.

Testing and feedback welcome, especially on the **[open questions](#open--testing-would-help)**.
Each one names the diagnostic that would settle it.

---

## Status

| Area | State |
|---|---|
| Squad create / copy / rename | **Fixed** |
| Resuming an "underway" cup | **Fixed** — used to crash the client |
| "Leaving Ultimate Team" hang | **Fixed** |
| Cup opponents and difficulty | **Reworked** — tiered per cup |
| Transfer market | **Reworked** — rotating stock, consumables always in stock, ordered by time remaining |
| Match / cup payouts | **Configurable**, no code editing |
| Offline Seasons | **Reworked** — eleven tiered divisions, progress saves, promotion and relegation. Opens and plays; the ladder needs one full season to confirm |
| Player card stats | **Fixed** — goals, assists, cards and appearances accumulate per card |
| Frame rate during a match | **Fixed** — a diagnostic hook was rebuilding a full stack trace for every debug line the game printed |
| Club venue | **Configurable** — defaults to Forest Park |
| Match length | Label only — gameplay ignores it |
| "DB ERROR" card in packs | Open — client-side catalogue gap |
| Store pack names/descriptions | Open — cosmetic |

All 8 verifiers pass. The launcher runs them before startup and refuses to boot on a
failure, so run them after any change:

```bash
cd tools && for v in verify_fifa14_v237_install.py verify_fifa14_beta2.py \
  verify_fifa14_postmatch_beta2259.py verify_fifa14_consumables_beta224.py \
  verify_fifa14_pack_ui_performance_beta2250.py verify_fifa14_market_beta2250.py \
  verify_fifa14_regressions_beta2258.py verify_fifa14_postmatch_beta2256.py; do
  python "$v" >/dev/null 2>&1 && echo "PASS $v" || echo "FAIL $v"; done
```

---

## What has been fixed

**New squads were never created.** "Create New Squad" posts `id: 0`, which resolved to
the *active* squad — so nothing was created, and "Copy Squad" overwrote squad 1
instead. There is now a real create branch, and the response carries the new squad's
id because the frontend navigates by it.

**Squad renames never stuck.** A rename sends the squad with no `players` array, and
that shape returned early — correct for the tournament handoff, which carries no name,
but it swallowed every rename. A name-only save now applies the rename without
touching the players.

**Opening an underway cup crashed the client.** Winning a round stores `round + 1` with
a blank bracket and waits for the client to send its own; being kicked out first left
the cup stranded at round 2 with no bracket. Serving that made FIFA read a length out
of the wrong buffer and try to allocate 808,335,154 bytes — those four bytes are the
ASCII `"27.0"` out of a `127.0.0.1` URL string. A cup with no bracket is no longer
resumable at any round.

**The "Leaving Ultimate Team" hang.** The post-match logout guard also pre-armed from
CreateMatch, so it hijacked a logout happening *before* kickoff and stranded a session
that was already tearing down. It now stands down unless a match could plausibly have
been played.

**The test-coin script silently did nothing.** Its grant was keyed on a fixed ledger
reference, so once that row existed it never granted again. It is now a top-up to a
target: a no-op at or above it, restores it below.

**Offline Seasons threw your progress away.** The client saves its season itself, before
and after every fixture, with a `PUT` the server answered by handing back the season
*list* — so the save was parsed as a list request and discarded, while `season/user`
replied from a seeded row nothing ever updated. It reported season 2 / division 10
against a list holding only season 1 / division 11: nothing matched, nothing was ever
underway. Progress now round-trips, and the ladder is eleven divisions with its own ten
opponents each, ~60 average rating at the bottom climbing to the actual top ten — every
division used to schedule the same ten European giants against a bronze starter squad.

**Player cards never recorded anything.** Goals and assists arrived with every match
result and were written to a member the client does not read, and appearances were not
counted at all, so every card sat on zero forever. They now accumulate into the two
arrays the client actually parses, for the eleven that started, once per match.

**Frame rate dropped during matches.** Windows raises an exception for every
`OutputDebugString`, and the crash tracer was building a 32-frame stack trace with a
module lookup per frame for each one — including a joystick the game does not recognise
warning about itself every frame. One tester capture logged 1,000 of those in 38
seconds and a 6-minute match that took 26 minutes of wall clock.

**Two Transfer Market tabs were always empty.** Player Training and Manager Leagues sent
their own spelling of the category and it was compared against the catalogue's spelling
without normalising either — so 63 and 24 cards respectively were in stock and
unreachable, while the three tabs whose spellings happened to match worked fine.

## What has been added

**Settings editor — `FUT_SETTINGS.cmd`.** Match rewards (win / draw / loss / dnf), cup
payouts, market rotation and consumable price, plus set-coin-balance and
clear-club-to-starter-squad. Tuning lives in a JSON file beside the save, so it
survives extracting a new build, and the loader validates and clamps everything — a bad
settings file cannot take the server down.

**Per-cup opponents.** All four cups drew the same fifteen European giants, so a bronze
starter club faced Barcelona in the Starter Cup. Each cup now has its own pool:

| Cup | Opponents | Rounds |
|---|---|---|
| Starter | lower-league (~60 rated) | Amateur → Semi-Pro |
| Bronze | second tier (~67) | Semi-Pro → Professional |
| Silver | established top flight (~74) | Professional → World Class |
| Gold | the elite (~81) | World Class → Legendary |

Every club is in the client's own shipped kit and badge sets, so the crest renders.
The mechanism was already in the protocol and unused: the client requests
`/teams?groupId=%d&count=%d`, and that `groupId` is the cup's `aigroup`.

**Rotating transfer market.** Every card used to be listed permanently (~42,000
auctions). A deterministic hash of *(card, rotation)* now lists a fraction at a time,
stable within a rotation so paging cannot reshuffle underneath you.

**Consumables on the market.** All user-facing kinds — position changes, chemistry
styles, contracts, fitness, healing, training — always in stock at a flat price, and
they do not rotate.

**`fifa-debug-string-beta2260`.** FIFA narrates itself through `OutputDebugString`;
those calls were reaching the tracer as exceptions with the text discarded. Capturing
it is what identified the cup crash above, in the client's own words.

---

## Open — testing would help

**Does the season ladder actually climb?** Seasons now open, play and save, and the
server settles promotion and relegation itself. What is unconfirmed is who owns the
division number: the client has its own `GetUsersOfflineDivision` and asks the season
list for a specific division, and whether it adopts the one `season/user` reports has
never been observed. Play a division-11 season to the end and check whether the next
`season/list` request asks for division 10. `fut-season-request-beta2260` logs the
divisions served next to the club's actual division on every request.

**Which stat is in which slot on a player card?** Goals and appearances now accumulate,
but each card carries five unlabelled stat slots and the order is inferred, not
recorded. Turn the probe on and every card reports 11/22/33/44/55 in those five slots,
so one look at a card detail screen decodes it. Your stored totals are untouched — the
probe only changes what is sent to the game.

Both switches live in **`FUT_SETTINGS.cmd` → 9 (Testing switches)**, which writes them
to the settings file beside your save:

```json
{ "diagnostics": { "playerStatProbe": true, "seasonSaveMode": "round" } }
```

They are deliberately not environment variables. The launcher relaunches itself
elevated, and Windows starts that elevated process with a fresh environment, so
anything exported in a shell beforehand never reaches the server. (`FIFA14_PLAYER_STAT_PROBE`
and `FIFA14_SEASON_SAVE_MODE` still work if you start the server directly from a
terminal, and win over the file.)

**Does round difficulty reach the AI, or is it only a label?** Match length is
label-only — gameplay reads the client's own settings — so difficulty may be too. The
Bronze Cup is the cheapest test: its first round moved from Amateur to Semi-Pro.

**Is Legendary (5) really the ceiling?** The client clamps to its own
`MIN_/MAX_DIFFICULTY_LEVEL`. If the Gold Cup's later rounds still read World Class,
the max is 4.

**The `groupId` round-trip** is inferred from a format string in the DLL; no capture of
`/tournament/teams` exists, because it is only requested when starting a fresh cup.
`fut-tournament-teams-group-beta2260` logs what actually arrives.

**An intermittent bail-out during match setup**, reported once as "could not reach
Origin services" followed by a hang. Not the tracer stall — that was ruled out — and
two matches immediately afterwards completed normally.

---

## Installation

1. Extract to a normal writable folder.
2. Run `INSTALL_PREREQUISITES.cmd` as Administrator once if dependencies are missing.
3. Run `RUN_FIFA14_LOCAL_BETA.cmd` as Administrator. The launcher auto-detects FIFA 14;
   if needed, paste the `Game` folder once and it is remembered in `config.local.psd1`.
4. Wait for the launcher to report ready before entering Ultimate Team.

This expects an existing legitimate FIFA 14 PC installation and does not include the
game. No crack, DRM bypass or executable is included.

`FUT_SETTINGS.cmd` changes payouts, prizes and market stock. `GIVE_TEST_COINS.cmd`
tops the club up to a target balance.

**Known workaround:** before a single-player tournament match, own and apply a stadium
card in My Club. A missing active stadium can produce the dark/void match presentation.

---

## Reporting

[HANDOFF.md](HANDOFF.md) is the full engineering context — what is fixed, what is open,
what has already been ruled out, and the operating rules worth reading before changing
anything. It is generated from the maintainer's working notes with local paths removed.

A useful report says what you did in-game and includes the debug ZIP from that run.
**Check the ZIP before attaching it to a public issue** — `redirect-probe.log` records
your whole local session.

## License

See [LICENSE](LICENSE), inherited from upstream. FIFA, FIFA 14, Ultimate Team,
EA SPORTS and related marks belong to their respective owners. This is an independent
preservation project, not affiliated with or endorsed by Electronic Arts.
