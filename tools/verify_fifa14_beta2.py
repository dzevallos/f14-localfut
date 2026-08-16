from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import beta_identity as beta_identity_module
import fut_local_settings
from beta_identity import (
    BetaIdentityStore,
    OFFLINE_COMPETITION_TEAM_IDS,
    OFFLINE_TOURNAMENTS,
    TOURNAMENT_TEAM_POOLS,
    MATCH_RESULT_FLAT_COINS,
)
from local_identity import MARKET_CONSUMABLE_BUY_NOW, PACK_DEFINITIONS, PLAYER_CATALOG
from probe import HttpProbe


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "beta.sqlite3"
        report = Path(td) / "fifa14-match-assets-v2411-beta222.json"
        report.write_text(json.dumps({
            "catalog": {
                "kits": [
                    {"assetId": 16, "resourceId": 6500000, "definitionId": 6500000, "carddbid": 6500000,
                     "teamid": 1, "category": 2, "teamkittypetechid": 0, "value": 80, "weightrare": 10, "year": 0,
                     "itemType": "kit", "name": "Team 1 Home Kit"}
                ],
                "stadiums": [
                    {"assetId": 35, "resourceId": 6200017, "definitionId": 6200017, "carddbid": 6200017,
                     "stadiumid": 35, "category": 4, "value": 66, "weightrare": 0, "year": 0,
                     "itemType": "stadium", "name": "Stadium 35"}
                ],
                "badges": [
                    {"assetId": 1, "resourceId": 6100000, "definitionId": 6100000, "carddbid": 6100000,
                     "badgeDBid": 6100000, "teamid": 1, "category": 1, "value": 75, "weightrare": 10, "year": 0,
                     "itemType": "custom", "name": "Badge 1"}
                ],
            }
        }), encoding="utf-8")
        beta_identity_module.MATCH_ASSET_REPORT = report
        store = BetaIdentityStore(str(db), "existing")
        compact_squads = store.squad_list_compact()
        if set(compact_squads) != {"activeSquadId", "squad"}:
            fail(f"/squad/list must use retail activeSquadId+squad envelope: {compact_squads}")
        if compact_squads.get("squad") and any("players" in row or "actives" in row for row in compact_squads.get("squad", []) if isinstance(row, dict)):
            fail(f"/squad/list must stay metadata-only; details are fetched separately: {compact_squads}")
        active_detail = store.active_squad_document()
        if active_detail and len(active_detail.get("players", [])) != 23:
            fail(f"/squad/active must return one full 23-slot SquadDetailsResponse: {active_detail}")
        profile = store.beta_profile_summary()
        if profile["coins"] != 0 or profile["fifaPoints"] != 0:
            fail(f"fresh BETA wallet must be 0/0, got {profile}")
        if profile["ownedItems"] != 29 or profile["squadPlayers"] != 23:
            fail(f"starter club must contain 23 players plus 3 proven and 3 scanned cosmetics, got {profile}")
        squad = store.squad_list()["squadList"][0]["players"]
        items = [row["itemData"] for row in squad if row.get("itemData", {}).get("id")]
        if len(items) != 23:
            fail("starter squad serialization lost players")
        if any(int(row.get("rating", 99)) >= 65 for row in items):
            fail("starter squad contains a non-bronze player")
        if sum(1 for row in items if row.get("preferredPosition") == "GK") != 1:
            fail("starter XI must contain exactly one bronze GK in this source catalogue")
        if any(not bool(row.get("untradeable", False)) for row in items):
            fail("starter cards must be untradeable")
        squad_doc = store.squad_list()["squadList"][0]
        if int(squad_doc.get("chemistry", -1)) != 54 or int(squad_doc.get("starRating", -1)) != 61:
            fail(f"starter squad native scalar state missing: {squad_doc}")
        actives = squad_doc.get("actives")
        if actives != []:
            fail(f"fresh BETA 2.22 must not fake active cosmetics before the retail client selects them: {actives}")

        # Exercise the real HTTP parse_qs shape as well as scalar helper calls.
        # BETA 2.14 verified only scalar values, which hid the live type=["kit"]
        # projection bypass.
        kit_inventory = store.club_items({
            "year": ["2014"], "type": ["kit"], "level": ["any"], "count": ["11"]
        }).get("itemData", [])
        stadium_inventory = store.club_items({
            "year": ["2014"], "type": ["stadium"], "level": ["any"], "count": ["11"]
        }).get("itemData", [])
        badge_inventory = store.club_items({
            "year": ["2014"], "type": ["badge"], "level": ["any"], "count": ["11"]
        }).get("itemData", [])
        if len(kit_inventory) != 3 or len(stadium_inventory) != 2 or len(badge_inventory) != 1:
            fail(f"full cosmetic My Club inventory contract is not populated: kits={len(kit_inventory)} stadiums={len(stadium_inventory)} badges={len(badge_inventory)}")
        if any(str(row.get("itemState")) != "free" for row in kit_inventory + stadium_inventory + badge_inventory):
            fail(f"fresh selectable cosmetics must start free: {kit_inventory + stadium_inventory + badge_inventory}")
        if store.club_items({"type": "2"}).get("total") != 3 or store.club_items({"type": "4"}).get("total") != 2:
            fail("numeric kit/stadium query aliases do not expose the selectable club items")
        if store.club_items({"type": "badges"}).get("total") != 1 or badge_inventory[0].get("itemType") != "custom":
            fail(f"badge/custom My Club alias or wire family is wrong: {badge_inventory}")
        # The Club Items tab asks for every equippable in one query. The captured
        # request is `/club?year=2014&type=equippables&level=any&count=11`; that
        # token matched no branch, so the tab returned an empty page no matter
        # what was searched (dzevallos/f14-localfut#2).
        equippables = store.club_items({
            "year": ["2014"], "type": ["equippables"], "level": ["any"], "count": ["11"],
        })
        equippable_families = {row.get("itemType") for row in equippables.get("itemData", [])}
        if int(equippables.get("total", 0)) != len(kit_inventory) + len(stadium_inventory) + len(badge_inventory):
            fail(f"type=equippables must return every equippable club item: {equippables.get('total')}")
        if equippable_families != {"kit", "stadium", "custom"}:
            fail(f"type=equippables must span the equippable families, got {equippable_families}")
        first_page = [row.get("id") for row in equippables.get("itemData", [])[:2]]
        second_page = store.club_items({
            "year": ["2014"], "type": ["equippables"], "level": ["any"],
            "start": ["2"], "count": ["11"],
        }).get("itemData", [])
        if set(first_page).intersection(row.get("id") for row in second_page):
            fail("equippable paging repeats items across pages")

        home = next(row for row in kit_inventory if int(row.get("assetId", -1)) == 14)
        away = next(row for row in kit_inventory if int(row.get("assetId", -1)) == 15)
        # The club's venue is configurable (`club.stadiumId`), so find the one the
        # server actually resolved rather than Town Park's old literal 34.
        starter_stadium_id = int(beta_identity_module._resolved_match_assets()["stadium"]["assetId"])
        stadium_card = next(
            (row for row in stadium_inventory if int(row.get("assetId", -1)) == starter_stadium_id),
            None,
        )
        if stadium_card is None:
            fail(f"the club does not own its own starter stadium {starter_stadium_id}: "
                 f"{[row.get('assetId') for row in stadium_inventory]}")

        # BETA 2.13's guessed 5/7/8 cardsubtypeid + clubInfo experiment crashed
        # the retail PC client at kit-search response parse time. BETA 2.22 uses
        # a deliberately conservative, canonical wire projection instead.
        if home.get("itemType") != "kit" or away.get("itemType") != "kit":
            fail(f"kit ItemData must serialize in the kit family: {kit_inventory}")
        if stadium_card.get("itemType") != "stadium":
            fail(f"stadium ItemData must serialize in the stadium family: {stadium_card}")
        if int(home.get("category", -1)) != 2 or int(home.get("teamkittypetechid", -1)) != 0:
            fail(f"home-kit retail classification lost: {home}")
        if int(away.get("category", -1)) != 3 or int(away.get("teamkittypetechid", -1)) != 1:
            fail(f"away-kit retail classification lost: {away}")
        if int(stadium_card.get("category", -1)) != 4 or int(stadium_card.get("stadiumid", -1)) != starter_stadium_id:
            fail(f"stadium retail classification lost: {stadium_card}")
        # category/teamkittypetechid/stadiumid are retail classification members
        # present in the BETA 2.14 response that rendered correctly.  FCC database
        # bookkeeping must not leak onto the wire, and the old 2/3/4 subtype alias
        # remains forbidden.
        risky_wire_keys = {
            "cardsubtypeid", "carddbid", "cardassetid", "weightrare",
            "teamkitid", "year", "value", "cardsSource",
        }
        for cosmetic in kit_inventory + stadium_inventory + badge_inventory:
            leaked = risky_wire_keys.intersection(cosmetic)
            if leaked:
                fail(f"database-only cosmetic keys leaked onto My Club wire: {sorted(leaked)} / {cosmetic}")
            keys = list(cosmetic.keys())
            if keys[:4] != ["id", "itemId", "timestamp", "itemType"]:
                fail(f"cosmetic parser-safe prefix/order regressed: {keys}")
            if keys.index("itemType") > keys.index("assetId") or keys.index("itemType") > keys.index("resourceId"):
                fail(f"itemType must precede cosmetic identity fields: {keys}")
        if (int(home.get("rating", -1)), int(away.get("rating", -1)), int(stadium_card.get("rating", -1))) != (89, 89, 64):
            fail(f"retail fcc value/rating projection is wrong: {kit_inventory + stadium_inventory}")
        required_common = {
            "id", "timestamp", "rating", "assetId", "resourceId", "itemState",
            "rareflag", "formation", "leagueId", "injuryType", "injuryGames",
            "lastSalePrice", "fitness", "training", "suspension", "contract",
            "discardValue", "itemType", "owners",
        }
        for cosmetic in kit_inventory + stadium_inventory:
            if not required_common.issubset(cosmetic):
                fail(f"cosmetic ItemData common envelope is incomplete: {cosmetic}")

        badge_card = badge_inventory[0]
        viewed = store.view_items([int(home["id"]), int(away["id"]), int(stadium_card["id"]), int(badge_card["id"])])
        viewed_rows = viewed.get("itemData", [])
        if [int(row.get("id", 0)) for row in viewed_rows] != [int(home["id"]), int(away["id"]), int(stadium_card["id"]), int(badge_card["id"])]:
            fail(f"ViewCards exact-id lookup lost requested order/identity: {viewed}")
        for viewed_item in viewed_rows:
            if str(viewed_item.get("itemType", "")).lower() in {"kit", "stadium", "custom"}:
                keys = list(viewed_item.keys())
                if keys[:4] != ["id", "itemId", "timestamp", "itemType"] or "cardsubtypeid" in viewed_item:
                    fail(f"ViewCards cosmetic wire projection regressed: {viewed_item}")

        # Use the *actual retail FIFA 14 My Club activation documents captured
        # from BETA 2.16.  Kits use generic itemState=active plus slot 101/102;
        # stadium activation has no slot number.  Older local-only synthetic
        # state names hid the persistence bug this test is meant to prevent.
        activation_updates = [
            {"id": int(home["id"]), "itemState": "active", "activateSlotNumber": "101"},
            {"id": int(away["id"]), "itemState": "active", "activateSlotNumber": "102"},
            {"id": int(stadium_card["id"]), "itemState": "active"},
            {"id": int(badge_card["id"]), "itemState": "active"},
        ]
        activation = store.move_items(activation_updates)
        if len(activation.get("itemData", [])) != 4 or not all(row.get("success") for row in activation["itemData"]):
            fail(f"retail cosmetic activation failed: {activation}")
        badge_ack = next((row for row in activation["itemData"] if row.get("itemState") == "activeBadge"), None)
        if badge_ack is None:
            fail(f"badge activation acknowledgement missing: {activation}")
        if (int(badge_ack.get("assetId", -1)) != 1 or int(badge_ack.get("badge", -1)) != 1 or
                int(badge_ack.get("badgeId", -1)) != 1 or int(badge_ack.get("teamId", -1)) != 1 or
                int(badge_ack.get("definitionId", -1)) != 6100000):
            fail(f"badge activation acknowledgement lacks retail badge identity: {badge_ack}")
        active_badge_wire = store.view_items([int(badge_card["id"])])["itemData"][0]
        if (int(active_badge_wire.get("badgeId", -1)) != 1 or
                int(active_badge_wire.get("badgeResourceId", -1)) != 6100000 or
                int(active_badge_wire.get("badgeDefinitionId", -1)) != 6100000):
            fail(f"active badge wire aliases are incomplete: {active_badge_wire}")

        with closing(sqlite3.connect(db)) as badge_con:
            badge_con.row_factory = sqlite3.Row
            club_badge = badge_con.execute("SELECT badge_id FROM clubs LIMIT 1").fetchone()
            badge_setting = badge_con.execute("SELECT badge_resource_id FROM beta_club_settings LIMIT 1").fetchone()
            if club_badge is None or int(club_badge["badge_id"]) != 1 or badge_setting is None or int(badge_setting["badge_resource_id"]) != 6100000:
                fail(f"active badge did not persist to club/settings: club={club_badge} settings={badge_setting}")

        squad_doc = store.squad_list()["squadList"][0]
        actives = squad_doc.get("actives")
        # BETA 2.22 deliberately keeps activeBadge out of squad.actives: the
        # BETA 2.20 client wrote back a GK-only squad after parsing that shape.
        # Badge state remains persistent in club/settings and is bridged directly
        # to the native BADGE_ID UI writers instead.
        if not isinstance(actives, list) or len(actives) != 3:
            fail(f"UI-facing squad must keep only the proven home/away/stadium actives: {actives}")
        active_states = {str(row.get("itemState")) for row in actives}
        if active_states != {"activeHomeKit", "activeAwayKit", "activeStadium"}:
            fail(f"active kit/stadium states are wrong after manual selection: {actives}")
        active_by_state = {str(row.get("itemState")): row for row in actives}
        if int(active_by_state["activeHomeKit"].get("assetId", -1)) != 14 or active_by_state["activeHomeKit"].get("itemType") != "kit":
            fail(f"active home kit lost its safe wire identity: {active_by_state['activeHomeKit']}")
        if int(active_by_state["activeAwayKit"].get("assetId", -1)) != 15 or active_by_state["activeAwayKit"].get("itemType") != "kit":
            fail(f"active away kit lost its safe wire identity: {active_by_state['activeAwayKit']}")
        if int(active_by_state["activeHomeKit"].get("resourceId", -1)) != 6300000:
            fail(f"home kit must keep unique fcc carddbid 6300000 as resource identity: {active_by_state['activeHomeKit']}")
        if int(active_by_state["activeAwayKit"].get("resourceId", -1)) != 6400000:
            fail(f"away kit must keep unique fcc carddbid 6400000 as resource identity: {active_by_state['activeAwayKit']}")
        # The venue is a user setting (`club.stadiumId`), so pin the invariant
        # rather than the number: the active stadium must be exactly the resolved
        # starter stadium, id and fcc carddbid from the same row. Asserting "34
        # and 6200016" instead would turn changing the setting into a game that
        # will not boot, and asserting only the id would let a configured id be
        # paired with Town Park's resource -- a venue the client cannot render,
        # which is a crash rather than a wrong picture. Default stays Town Park
        # ID 34 / carddbid 6200016.
        resolved_stadium = beta_identity_module._resolved_match_assets()["stadium"]
        expected_stadium_id = int(resolved_stadium.get("assetId", 34))
        expected_resource = int(resolved_stadium.get("resourceId", 6200016))
        active_stadium = active_by_state["activeStadium"]
        if int(active_stadium.get("assetId", 0)) != expected_stadium_id or active_stadium.get("itemType") != "stadium":
            fail(f"active stadium is not the resolved venue {expected_stadium_id}: {active_stadium}")
        if int(active_stadium.get("resourceId", -1)) != expected_resource:
            fail(f"stadium {expected_stadium_id} must keep its own fcc carddbid {expected_resource}, "
                 f"not another venue's: {active_stadium}")
        if int(active_stadium.get("stadiumid", active_stadium.get("StadiumId", -1))) != expected_stadium_id:
            fail(f"stadium item id disagrees with its assetId: {active_stadium}")
        for cosmetic in actives:
            leaked = risky_wire_keys.intersection(cosmetic)
            if leaked:
                fail(f"native/debug-only keys leaked onto squad active cosmetic wire: {sorted(leaked)} / {cosmetic}")


        seasons = store.offline_seasons_list()
        season_rows = seasons.get("seasons", [])
        # The ladder length is local tuning, not a client contract. What the
        # client does pin down is which division it asks for: the 2026-08-15
        # capture requested divisionList=11, and returning a ladder without it
        # is what produced "seasons are currently unavailable". Assert the
        # ladder is contiguous, descending, and contains the entry division.
        ladder = [int(row["divisionId"]) for row in season_rows]
        if ladder != sorted(ladder, reverse=True) or len(set(ladder)) != len(ladder):
            fail(f"season divisions must descend without repeats: {ladder}")
        if ladder != list(range(ladder[0], ladder[0] - len(ladder), -1)):
            fail(f"season divisions must be contiguous so promotion has somewhere to go: {ladder}")
        if ladder != list(range(10, 0, -1)):
            fail(f"the retail ladder is Division 10 down to 1: {ladder}")
        if ladder[0] != store.entry_season_division():
            fail(f"the ladder must start at the entry division {store.entry_season_division()}: {ladder}")
        if [int(row["id"]) for row in season_rows] != list(range(1, len(season_rows) + 1)):
            fail(f"season list ids must be 1-based and in order: {[r['id'] for r in season_rows]}")
        first = season_rows[0]
        required_season = {
            "id", "type", "divisionId", "numMatches", "matchLengthMin", "matches",
            "prizeSet", "elgOperation", "elgReq", "trophyResourceId", "trophyUseCount",
            "visStartDays", "visEndDays", "startDateTime", "endDateTime",
            "untilStartSeconds", "untilEndSeconds",
        }
        if not required_season.issubset(first):
            fail(f"entry-division native season record is incomplete: {first}")
        guessed_season_keys = {
            "seasonId", "division", "name", "matchesPlayed", "matchesToPlay",
            "pointsToWinTitle", "pointsToPromote", "pointsToAvoidRelegation",
            "points", "won", "draw", "lost", "coinsPerWin", "trophiesWon",
        }
        if guessed_season_keys.intersection(first):
            fail(f"legacy guessed season keys leaked back onto the wire: {first}")
        if int(first["id"]) != 1 or first["type"] != "OFFLINE":
            fail(f"first native season record must map ID 1 to an OFFLINE season: {first}")
        if int(first.get("trophyResourceId", 0)) != -1:
            fail(f"BETA 2.22 season no-trophy sentinel must be -1: {first}")
        matches = first.get("matches")
        if int(first["numMatches"]) != 10 or int(first["matchLengthMin"]) != 6 or not isinstance(matches, list) or len(matches) != 10:
            fail(f"Division 10 native match contract is wrong: {first}")
        required_match = {"teamId", "difficulty", "rewardMult", "roundId", "coins"}
        if any(not required_match.issubset(match) for match in matches):
            fail(f"Division 10 native match record is incomplete: {matches}")
        if [int(match["roundId"]) for match in matches] != list(range(10)):
            fail(f"season match roundId values must be internal 0..9: {matches}")
        valid_team_ids = {int(player.get("teamId", 0)) for player in PLAYER_CATALOG}
        if any(int(match["teamId"]) not in valid_team_ids for match in matches):
            fail(f"season schedule contains a team absent from the retail player catalogue: {matches}")
        prizes = first.get("prizeSet")
        if not isinstance(prizes, list) or [p.get("prizeLevel") for p in prizes] != [
            "RELEGATION", "MAINTENANCE", "PROMOTION", "CHAMPIONSHIP"
        ]:
            fail(f"Division 10 prizeSet enum/order is wrong: {prizes}")
        # Division 10's thresholds are known from the retail frontend (12/9/0),
        # so check that division specifically rather than whichever record now
        # happens to be first -- the ladder starts at the entry division.
        division_ten = next((row for row in season_rows if int(row["divisionId"]) == 10), None)
        if division_ten is None:
            fail(f"the ladder no longer contains Division 10: {ladder}")
        ten_thresholds = {
            p["prizeLevel"]: int(p.get("thresholdPoint", -1)) for p in division_ten["prizeSet"]
        }
        if ten_thresholds != {"RELEGATION": 0, "MAINTENANCE": 0, "PROMOTION": 9, "CHAMPIONSHIP": 12}:
            fail(f"Division 10 native thresholds are wrong: {ten_thresholds}")
        thresholds = {p["prizeLevel"]: int(p.get("thresholdPoint", -1)) for p in prizes}
        if not (thresholds["CHAMPIONSHIP"] > thresholds["PROMOTION"] > thresholds["RELEGATION"] - 1):
            fail(f"entry-division thresholds are not ordered: {thresholds}")
        for prize in prizes:
            mappings = prize.get("awardMappings")
            if not isinstance(mappings, list) or not mappings or not isinstance(mappings[0].get("awards"), list):
                fail(f"season awardMappings must be array -> awards array: {prize}")
        season_user = store.offline_season_user()
        # season/user and season/list have to agree on the division, because the
        # client asks the list for the division it believes it is in. Them
        # drifting apart is exactly what left the screen with nothing to show.
        # season/user must never name a division the client does not have. 11 is
        # its "not placed yet" value in the *request*; reporting it back as the
        # club's current division is what both 0.4 failures had in common -- a
        # null-deref crash on entry (0.4.2) and "seasons are currently
        # unavailable" (0.4.3). The one document ever observed to work was
        # seasonId 2 / divisionId 10 / round 1, and seasonId names the ladder
        # record for that division, so the two stay self-consistent.
        served_ids = {int(row["id"]) for row in season_rows}
        if not 1 <= int(season_user["divisionId"]) <= 10:
            fail(f"season/user must report a division the client recognises (10..1): {season_user}")
        # Against the list the client actually holds -- it asks about one division
        # at a time, so this is the single entry record, not the whole ladder.
        held_ids = {int(r["id"]) for r in store.offline_seasons_list({"divisionList": ["11"]})["seasons"]}
        if int(season_user["seasonId"]) in held_ids:
            fail(f"a season with no saved state must not claim a seasonId the client holds: {season_user}")
        if season_user != {"seasonId": 2, "divisionId": 10, "round": 1}:
            fail(f"fresh season/user drifted off the one document proven to work: {season_user}")
        # The screen names the divisions it wants; serve those.
        asked = store.offline_seasons_list({"divisionList": [str(store.entry_season_division())]})
        if [int(r["divisionId"]) for r in asked["seasons"]] != [store.entry_season_division()]:
            fail(f"a divisionList filter must be honoured: {[r['divisionId'] for r in asked['seasons']]}")
        # 11 is the client's "not placed yet" value, and a division we do not
        # have must offer the entry season rather than the whole ladder -- dumping
        # every record is what produced "seasons are currently unavailable".
        for unplaced in ("11", "99"):
            offered = store.offline_seasons_list({"divisionList": [unplaced]})["seasons"]
            if [int(r["divisionId"]) for r in offered] != [store.entry_season_division()]:
                fail(f"divisionList={unplaced} must offer only the entry division: "
                     f"{[r['divisionId'] for r in offered]}")

        # BETA 2.26.1 -- offline seasons persist and the ladder has real tiers.
        #
        # Every division used to schedule the same ten European giants, so the
        # entry division opened against Barcelona/Real/Bayern; and the client's
        # own save (PUT /season/<id>/division/<div>/user, captured 2026-08-16)
        # was accepted and thrown away, so a played season never came back.
        entry_teams = [int(m["teamId"]) for m in season_rows[0]["matches"]]
        top_teams = [int(m["teamId"]) for m in season_rows[-1]["matches"]]
        if set(entry_teams) & set(top_teams):
            fail(f"entry and top division must not share opponents: {entry_teams} / {top_teams}")
        if any(int(m["difficulty"]) < 1 or int(m["difficulty"]) > 5 for row in season_rows for m in row["matches"]):
            fail("season fixture difficulty must stay inside the client's 0..5 AI ladder")
        entry_hardest = max(int(m["difficulty"]) for m in season_rows[0]["matches"])
        top_easiest = min(int(m["difficulty"]) for m in season_rows[-1]["matches"])
        if entry_hardest >= top_easiest:
            fail(f"the ladder must get harder: entry peaks at {entry_hardest}, top opens at {top_easiest}")
        for row in season_rows:
            pool = {int(m["teamId"]) for m in row["matches"]}
            if not pool.issubset(valid_team_ids):
                fail(f"division {row['divisionId']} schedules a club absent from the catalogue: {sorted(pool)}")

        saved = store.update_offline_season_user(
            1, store.entry_season_division(),
            {"round": 2, "dataVersion": 1, "data": "QUJD", "progressDataVersion": 1, "progressData": "AwAAAAsIAA=="},
        )
        if list(saved) != ["round", "dataVersion", "data", "progressDataVersion", "progressData"]:
            fail(f"a season save must be echoed in the client's own write order: {saved}")
        resumed = store.offline_season_user()
        if int(resumed["round"]) != 2 or resumed.get("data") != "QUJD":
            fail(f"a saved season must come back underway at the round it saved: {resumed}")
        if list(resumed).index("data") > list(resumed).index("dataVersion"):
            fail(f"dataVersion decodes the buffer before it, so data must come first: {list(resumed)}")
        if int(resumed["divisionId"]) > 10:
            fail(f"a saved season must still report a division the client recognises: {resumed}")
        # A save for a division the club is not in belongs to a finished season.
        before_stale = store.offline_season_user()
        stale = store.update_offline_season_user(9, 3, {"round": 7, "dataVersion": 1, "data": "Wlpa"})
        if store.offline_season_user() != before_stale:
            fail(f"a season save for another division must not move the ladder: {stale}")
        if os.environ.get("FIFA14_SEASON_SAVE_MODE"):
            fail("FIFA14_SEASON_SAVE_MODE must not be set for the default contract")
        os.environ["FIFA14_SEASON_SAVE_MODE"] = "round"
        try:
            minimal = store.offline_season_user()
        finally:
            os.environ.pop("FIFA14_SEASON_SAVE_MODE", None)
        if set(minimal) != {"seasonId", "divisionId", "round"}:
            fail(f"the round-only fallback must send exactly the three proven members: {minimal}")
        if int(minimal["round"]) != 2:
            fail(f"the round-only fallback must keep the saved round: {minimal}")
        if store.offline_season_history() != {"seasons": []}:
            fail("history must be empty until a season finishes, not a copy of the active list")
        store.reset_offline_season(1, store.entry_season_division())
        after_reset = store.offline_season_user()
        if int(after_reset["round"]) != 1 or int(after_reset["divisionId"]) > 10:
            fail(f"a season reset must return the club to round 1 in a real division: {after_reset}")

        # BETA 2.22 keeps the proven native PC tournament schema but exposes
        # four independent knockout cups. `rounds` remains an ARRAY; names are
        # intentionally NOT sent in this JSON because the retail list parser
        # does not consume a name member. The exact-build frontend hook supplies
        # the local display names instead.
        os.environ.pop("FIFA14_TOURNAMENT_MODE", None)
        tournaments = store.offline_tournaments_list()
        tournament_rows = tournaments.get("tournament", [])
        if len(tournament_rows) != 4:
            fail(f"BETA 2.22 default tournament catalogue must contain four cups: {tournaments}")
        required_cup = {
            "id", "type", "treeType", "aigroup", "eligibilityOperation", "elgReq",
            "numTeams", "numRounds", "matchlength", "rounds", "awardSet", "lock",
            "unlockreq", "triesMax", "triesPeriod", "triesRemaining", "nextReset",
            "starttime", "endtime", "timeUntilStart", "timeUntilEnd", "visStart",
            "visEnd", "trophyResourceId", "trophyUserCount",
        }
        guessed_cup_keys = {"tournamentId", "name", "level", "prize", "repeatPrize", "currentRound", "entryFee", "active", "won"}
        # Prizes are local tuning, so take them from the definitions rather than
        # from literals. What this protects is the parser-native awardSet shape
        # and that the advertised value is the configured one.
        expected_prizes = {
            int(row["tournamentId"]): int(row["prize"]) for row in OFFLINE_TOURNAMENTS
        }
        if [int(row.get("id", 0)) for row in tournament_rows] != [1, 2, 3, 4]:
            fail(f"tournament IDs/order regressed: {tournament_rows}")
        for cup in tournament_rows:
            if not required_cup.issubset(cup):
                fail(f"native tournament record is incomplete: {cup}")
            if guessed_cup_keys.intersection(cup):
                fail(f"legacy guessed tournament keys leaked back onto the wire: {cup}")
            if cup["type"] != "offline" or cup["treeType"] != "knockout" or cup["lock"] != "UNLOCKED":
                fail(f"tournament enum mapping is wrong: {cup}")
            rounds = cup.get("rounds")
            if not isinstance(rounds, list) or len(rounds) != 4:
                fail(f"tournament rounds MUST be a four-record array: {rounds}")
            if any(not {"id", "difficulty", "rewardMultiplier", "coins"}.issubset(r) for r in rounds):
                fail(f"tournament native round schema is incomplete: {rounds}")
            expected_awards = [{"awardType": 1, "value": expected_prizes[int(cup["id"])], "halid": 0}]
            if cup.get("awardSet", {}).get("awards") != expected_awards:
                fail(f"tournament awardSet is not parser-native: {cup}")
        if store.offline_tournament_user_list().get("tournamentId") != []:
            fail("tournament user-list must remain empty until a cup is actually entered")
        teams = store.offline_tournament_teams(15)
        if list(teams) != ["teamId"] or len(teams.get("teamId", [])) != 15:
            fail(f"tournament/teams must return only a 15-element teamId array: {teams}")
        if teams["teamId"] != list(OFFLINE_COMPETITION_TEAM_IDS):
            fail(f"tournament team pool without a groupId must stay the shared fallback: {teams}")
        if any(int(team_id) not in valid_team_ids for team_id in teams["teamId"]):
            fail(f"tournament team pool contains an unknown retail club: {teams}")
        # Each cup draws its own opponents: the client asks
        # /teams?groupId=<aigroup>&count=15, so aigroup has to be distinct per
        # cup and every pool has to be a full, renderable, in-catalogue bracket.
        cup_groups = {int(cup["id"]): int(cup["aigroup"]) for cup in tournament_rows}
        if sorted(cup_groups.values()) != sorted(set(cup_groups.values())):
            fail(f"cups must not share an AI group or they field the same clubs: {cup_groups}")
        seen_clubs: set[int] = set()
        tier_strength: list[tuple[int, float]] = []
        for cup_id, group_id in sorted(cup_groups.items()):
            if group_id not in TOURNAMENT_TEAM_POOLS:
                fail(f"cup {cup_id} advertises AI group {group_id} with no team pool")
            pool = store.offline_tournament_teams(15, group_id=group_id)["teamId"]
            if len(pool) != 15 or len(set(pool)) != 15:
                fail(f"cup {cup_id} needs fifteen distinct opponents for a 16-team bracket: {pool}")
            if any(int(team_id) not in valid_team_ids for team_id in pool):
                fail(f"cup {cup_id} pool contains a club with no players in the catalogue: {pool}")
            if seen_clubs.intersection(pool):
                fail(f"cup {cup_id} repeats clubs from an easier cup: {sorted(seen_clubs.intersection(pool))}")
            seen_clubs.update(pool)
            ratings_by_team: dict[int, list[int]] = {}
            for player in PLAYER_CATALOG:
                team_id = int(player.get("teamId", 0))
                if team_id in set(pool):
                    ratings_by_team.setdefault(team_id, []).append(int(player.get("rating", 0)))
            best_eighteen = [
                sum(sorted(ratings, reverse=True)[:18]) / len(sorted(ratings, reverse=True)[:18])
                for ratings in ratings_by_team.values() if ratings
            ]
            tier_strength.append((cup_id, sum(best_eighteen) / len(best_eighteen)))
        # The whole point of the tiers: a bronze starter club must not draw the
        # European elite in the Starter Cup. Assert the ladder climbs, not the
        # exact means, so the pools can be retuned without a false regression.
        if [cup for cup, _ in tier_strength] != sorted(cup_groups):
            fail(f"tier strengths were not measured in cup order: {tier_strength}")
        for (lower_cup, lower), (higher_cup, higher) in zip(tier_strength, tier_strength[1:]):
            if higher <= lower + 2:
                fail(
                    f"cup {higher_cup} ({higher:.1f}) is not a meaningful step up from cup "
                    f"{lower_cup} ({lower:.1f}); the cup ladder no longer scales"
                )
        if tier_strength[0][1] > 65:
            fail(f"Starter Cup opponents average {tier_strength[0][1]:.1f}; too strong for a bronze club")
        tournament_progress = store.update_offline_tournament_user(1, {
            "round": 1, "dataVersion": 1, "tournamentData": "fixture-data",
            "progressDataVersion": 1, "progressData": "AAAAAA==",
        })
        if tournament_progress.get("tournamentId") != 1 or tournament_progress.get("tournamentData") != "fixture-data":
            fail(f"tournament/user/1 progress was not persisted/echoed: {tournament_progress}")
        if store.offline_tournament_user(1) != {"tournamentId": 1}:
            fail(f"first-round zero progress must be treated as a fresh tournament, got {store.offline_tournament_user(1)}")
        if store.offline_tournament_user_list().get("tournamentId") != []:
            fail(f"first-round zero progress must NOT advertise Underway: {store.offline_tournament_user_list()}")

        # Progress in another cup must survive a DNF in tournament 1.
        store.update_offline_tournament_user(2, {
            "round": 2, "dataVersion": 1, "tournamentData": "cup-two-data",
            "progressDataVersion": 1, "progressData": "AQAAAA==",
        })

        os.environ["FIFA14_TOURNAMENT_MODE"] = "empty"
        if store.offline_tournaments_list().get("tournament") != []:
            fail("explicit empty tournament fallback no longer works")
        if store.offline_tournament_user_list().get("tournamentId") != []:
            fail("empty tournament fallback must hide resumable user state")
        os.environ.pop("FIFA14_TOURNAMENT_MODE", None)

        # BETA 2.4 still returned 404 for the trophy bundle immediately before
        # season/user and the native unavailable popup. BETA 2.22's controlled
        # compatibility response is a minimal structurally valid empty BIGF.
        trophy_path = "/fut/items/images/trophies/pc/item.big"
        if not HttpProbe._is_fut_static_archive_path(trophy_path):
            fail("trophy item.big is not classified as a FUT static archive")
        if not HttpProbe._is_fut_trophy_archive_path(trophy_path):
            fail("trophy item.big is not classified as the Offline Seasons trophy bundle")
        if not HttpProbe._is_fut_trophy_archive_path("/fut/items/images/trophies/pc/.big"):
            fail("degenerate tournament trophy .big path must receive the safe empty BIGF")
        if HttpProbe._is_fut_static_archive_path("/ut/game/fifa14/tournament/list"):
            fail("dynamic FUT route was incorrectly classified as static archive")
        empty_big = HttpProbe._empty_bigf_archive()
        if len(empty_big) != 16 or empty_big[:4] != b"BIGF":
            fail(f"empty trophy BIGF has wrong magic/size: {empty_big!r}")
        if int.from_bytes(empty_big[4:8], "big") != 16:
            fail("empty trophy BIGF declared size is not 16")
        if int.from_bytes(empty_big[8:12], "big") != 0:
            fail("empty trophy BIGF must contain zero entries")
        if int.from_bytes(empty_big[12:16], "big") != 16:
            fail("empty trophy BIGF header size is not 16")

        if os.environ.get("FIFA14_SEASON_ITEM0_MODE") is not None:
            os.environ.pop("FIFA14_SEASON_ITEM0_MODE", None)
        # The route handler's default is deliberately a compatibility probe: item
        # ID 0 (the PC client's no-reward sentinel) gets 200 + {}. Non-zero item
        # IDs remain strict misses. Live BETA 2.22 logging differentiates both.

        offers = store.store_pack_types().get("purchase", [])
        if not offers:
            fail("store pack catalogue is empty")
        expected_art = {
            1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3,
            101: 3, 102: 3, 103: 3, 104: 3, 105: 3, 106: 2, 107: 3,
        }
        expected_group = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3,
                          101: 3, 102: 3, 103: 3, 104: 3, 105: 3, 106: 2, 107: 3}
        for offer in offers:
            pack_type = int(offer.get("packType", 0))
            asset = int(offer.get("assetId", 0))
            group_asset = int(offer.get("displayGroupAssetId", 0))
            if asset != expected_art.get(pack_type):
                fail(f"Store safe tier asset mismatch for pack {pack_type}: {offer}")
            if group_asset != expected_group.get(pack_type):
                fail(f"Store display grouping changed for pack {pack_type}: {offer}")
            if not str(offer.get("description", "")).startswith("FUT_STORE_PACK_"):
                fail(f"Store description must be a retail loc token, got {offer.get('description')}")
            if not str(offer.get("name", "")).startswith("LOCAL_PACK_NAME_"):
                fail(f"Store name must be a local loc token, got {offer.get('name')}")

        partial_update = store.save_squad({"id": 1, "captain": int(items[0]["id"]), "kicktakers": [int(items[0]["id"])]})
        if not partial_update.get("squadList") or len(partial_update["squadList"][0].get("players", [])) != 23:
            fail(f"captain/kicktakers-only squad PUT must preserve the 23-player squad: {partial_update}")

        # BETA 2.22 migration guard: an existing user club/squad must survive even
        # if an older build lost the beta_starter_provisioned metadata marker.
        # BETA 2.19's old fallback deleted squads/items whenever that marker was
        # absent, which made extraction-to-extraction persistence fragile.
        with closing(sqlite3.connect(db)) as persist_con, persist_con:
            before_slots = [tuple(row) for row in persist_con.execute(
                "SELECT slot_index,item_id,asset_id,resource_id FROM squad_players ORDER BY squad_id,slot_index"
            ).fetchall()]
            before_items = int(persist_con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            persist_con.execute("DELETE FROM schema_meta WHERE meta_key='beta_starter_provisioned'")
        store = BetaIdentityStore(str(db), "existing")
        with closing(sqlite3.connect(db)) as persist_con:
            after_slots = [tuple(row) for row in persist_con.execute(
                "SELECT slot_index,item_id,asset_id,resource_id FROM squad_players ORDER BY squad_id,slot_index"
            ).fetchall()]
            after_items = int(persist_con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            marker = persist_con.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='beta_starter_provisioned'"
            ).fetchone()
            guard = persist_con.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='beta222_persistent_club_guard'"
            ).fetchone()
        if after_slots != before_slots or after_items != before_items:
            fail(f"existing club migration reset persistent squad/items: before_slots={before_slots} after_slots={after_slots} before_items={before_items} after_items={after_items}")
        if marker != ("1",) or guard != ("1",):
            fail(f"persistent club migration guard markers missing: starter={marker} guard={guard}")

        # The exact BETA 2.20 failure was a first PUT with only the goalkeeper
        # resolved and bogus 4 chemistry / 6 rating.  It must be a no-op.
        before_sparse = store.squad_list()["squadList"][0]
        before_sparse_ids = [int((row.get("itemData") or {}).get("id", 0) or 0) for row in before_sparse.get("players", [])]
        sparse_players = []
        for index in range(23):
            sparse_players.append({
                "index": index,
                "itemData": {"id": before_sparse_ids[0] if index == 0 else 0},
                "kitNumber": 1 if index == 0 else 0,
            })
        store.save_squad({
            "id": int(before_sparse.get("id", 1)),
            "squadName": "Starter XI", "formation": "f442",
            "chemistry": 4, "starRating": 6, "rating": 6,
            "players": sparse_players,
        }, requested_id=int(before_sparse.get("id", 1)))
        after_sparse = store.squad_list()["squadList"][0]
        after_sparse_ids = [int((row.get("itemData") or {}).get("id", 0) or 0) for row in after_sparse.get("players", [])]
        if after_sparse_ids != before_sparse_ids or int(after_sparse.get("chemistry", -1)) != int(before_sparse.get("chemistry", -2)) or int(after_sparse.get("starRating", -1)) != int(before_sparse.get("starRating", -2)):
            fail(f"GK-only sparse squad write corrupted persistent squad state: before={before_sparse} after={after_sparse}")
        required_squad_fields = {"rating", "valid", "newsquad", "kicktakers", "tactics", "dreamSquad", "custom"}
        if not required_squad_fields.issubset(after_sparse):
            fail(f"outgoing SquadDetails compatibility fields are incomplete: {after_sparse.keys()}")

        # A real squad edit must survive closing/reopening the identity store, not
        # merely remain visible in one in-memory instance. Swap two populated
        # slots, save the complete retail-shaped squad and reopen from disk.
        persisted_doc = store.squad_list()["squadList"][0]
        persisted_players = json.loads(json.dumps(persisted_doc.get("players", [])))
        populated_indices = [
            i for i, entry in enumerate(persisted_players)
            if isinstance(entry, dict) and int((entry.get("itemData") or {}).get("id", 0) or 0) > 0
        ]
        if len(populated_indices) < 2:
            fail(f"persistence test requires two populated squad slots: {persisted_players}")
        first_i, second_i = populated_indices[:2]
        first_item = persisted_players[first_i].get("itemData")
        second_item = persisted_players[second_i].get("itemData")
        persisted_players[first_i]["itemData"] = second_item
        persisted_players[second_i]["itemData"] = first_item
        save_payload = {
            "id": int(persisted_doc.get("id", 1)),
            "squadName": persisted_doc.get("squadName", "Starter XI"),
            "formation": persisted_doc.get("formation", "f442"),
            "chemistry": int(persisted_doc.get("chemistry", 0)),
            "starRating": int(persisted_doc.get("starRating", persisted_doc.get("rating", 0))),
            "players": persisted_players,
        }
        store.save_squad(save_payload, requested_id=int(persisted_doc.get("id", 1)))
        store = BetaIdentityStore(str(db), "existing")
        reopened_players = store.squad_list()["squadList"][0].get("players", [])
        reopened_first = int((reopened_players[first_i].get("itemData") or {}).get("id", 0) or 0)
        reopened_second = int((reopened_players[second_i].get("itemData") or {}).get("id", 0) or 0)
        if reopened_first != int((second_item or {}).get("id", 0) or 0) or reopened_second != int((first_item or {}).get("id", 0) or 0):
            fail(f"saved squad edit did not survive store reopen: first={reopened_first} second={reopened_second}")

        empty_reset = store.reset_match({})
        if empty_reset.get("status") != "reset" or store.metrics()["activeMatches"] != 0:
            fail("bootstrap match/reset must not create a phantom active match")
        create_response = store.create_match({"squadId": 0, "type": "OFFLINE", "tournamentId": 1})
        if set(create_response) != {"squad", "startDateTime"}:
            fail(f"CreateMatch must emit only the two parser-native top-level fields: {create_response.keys()}")
        create_squad = create_response.get("squad", {})
        if int(create_squad.get("chemistry", -1)) != 54 or int(create_squad.get("starRating", -1)) != 61:
            fail(f"CreateMatch lost squad scalar state: {create_squad}")
        if len(create_squad.get("players", [])) != 23 or len(create_squad.get("actives", [])) != 3:
            fail(f"CreateMatch did not embed full local squad/cosmetics: {create_squad}")
        match_players = [
            int((row.get("itemData") or {}).get("id", 0) or 0)
            for row in create_squad.get("players", [])[:18]
            if int((row.get("itemData") or {}).get("id", 0) or 0) > 0
        ]
        starters = match_players[:11]
        before_contract_rows = store.view_items(match_players).get("itemData", [])
        before_contracts = {int(row.get("id", 0)): int(row.get("contract", 0) or 0) for row in before_contract_rows}
        ready_response = store.match_ready({"items": [{"id": item_id} for item_id in starters]})
        if set(ready_response) != {"squad", "startDateTime"} or len(ready_response.get("squad", {}).get("players", [])) != 23:
            fail(f"items-only match-ready handoff must return the parser-native squad/startDateTime shape: {ready_response}")
        empty_match = store.settle_match({})
        if empty_match.get("settled") or int(empty_match.get("rewardCoins", -1)) != 0 or int(empty_match.get("credits", -1)) != 0:
            fail(f"empty /match acknowledgement incorrectly settled/paid: {empty_match}")
        dnf_reward = store._reward_breakdown({"minutesPlayed": 23, "goalsFor": 1, "goalsAgainst": 0, "dnf": 1}, 1.25)
        if int(dnf_reward.get("totalCoins", -1)) != 0:
            fail(f"DNF match must not receive normal BETA completion reward: {dnf_reward}")

        destroy = store.settle_match_end({
            "endReason": "QUIT",
            "matchData": "verify-destroy-match-data",
            "items": [
                {"id": item_id, "fitness": 97 - (index % 2)}
                for index, item_id in enumerate(match_players)
            ],
        })
        expected_destroy_keys = {"endReason", "secondsPlayed", "matchDifficulty", "items", "matchData"}
        if set(destroy) != expected_destroy_keys:
            fail(f"QUIT DestroyMatch must mirror the retail DNF branch exactly: {destroy}")
        if destroy.get("endReason") != "QUIT" or destroy.get("matchData") != "verify-destroy-match-data":
            fail(f"DestroyMatch must echo QUIT + native matchData: {destroy}")
        returned_items = destroy.get("items")
        if not isinstance(returned_items, list) or len(returned_items) != len(match_players) or "myMatchStats" in destroy or "opponentMatchStats" in destroy:
            fail(f"QUIT must return match-stat items and omit both matchStats objects: {destroy}")
        allowed_match_stat_keys = {"id", "shots", "goals", "yellowCards", "redCards", "suspension", "injuryType", "injuryGames", "fitness", "assists"}
        for row in returned_items:
            if not isinstance(row, dict) or not set(row).issubset(allowed_match_stat_keys):
                fail(f"DestroyMatch items leaked ItemData fields into the match-stat parser: {row}")
        returned_by_id = {int(row.get("id", 0)): row for row in returned_items if isinstance(row, dict)}
        if any("contract" in row or "assetId" in row or "itemType" in row for row in returned_items if isinstance(row, dict)):
            fail(f"DestroyMatch match-stat items must never contain card ItemData: {returned_items}")
        after_contract_rows = store.view_items(match_players).get("itemData", [])
        after_contracts = {int(row.get("id", 0)): int(row.get("contract", 0) or 0) for row in after_contract_rows}
        for item_id in starters:
            expected = max(0, int(before_contracts.get(item_id, 0)) - 1)
            if int(after_contracts.get(item_id, -1)) != expected:
                fail(f"starter contract was not decremented exactly once in persistent ItemData: id={item_id} before={before_contracts.get(item_id)} after={after_contracts.get(item_id)}")
        for item_id in match_players[11:]:
            if int(after_contracts.get(item_id, -1)) != int(before_contracts.get(item_id, 0)):
                fail(f"bench/reserve contract changed despite not starting: id={item_id} before={before_contracts.get(item_id)} after={after_contracts.get(item_id)}")
        record_after_dnf = store.match_record()
        if (int(record_after_dnf.get("wins", -1)), int(record_after_dnf.get("draws", -1)), int(record_after_dnf.get("losses", -1))) != (0, 0, 1):
            fail(f"server-side local FUT record did not become 0-0-1 after QUIT: {record_after_dnf}")
        resumable_after_dnf = store.offline_tournament_user_list().get("tournamentId")
        if resumable_after_dnf != [2]:
            fail(f"DNF in cup 1 must clear only cup 1 and preserve cup 2 progress: {resumable_after_dnf}")
        cup1_after_dnf = store.offline_tournament_user(1)
        if cup1_after_dnf != {"tournamentId": 1}:
            fail(f"DNF cup 1 state was not reset to a fresh/non-resumable cup: {cup1_after_dnf}")
        cup2_after_dnf = store.offline_tournament_user(2)
        if (int(cup2_after_dnf.get("round", 0)) != 2 or
                cup2_after_dnf.get("tournamentData") != "cup-two-data" or
                cup2_after_dnf.get("progressData") != "AQAAAA=="):
            fail(f"DNF in cup 1 incorrectly destroyed cup 2 progress: {cup2_after_dnf}")

        store.reset_match({"matchId": "verify-match", "mode": "single-player", "difficulty": "Professional"})
        if store.metrics()["activeMatches"] != 1:
            fail("explicit match reset/bootstrap did not create active match")
        result = store.settle_match({
            "matchId": "verify-match",
            "completed": 1,
            "minutesPlayed": 90,
            "goalsFor": 3,
            "goalsAgainst": 1,
            "shotsOnTarget": 8,
            "successfulTackles": 12,
            "corners": 5,
            "passAccuracy": 78,
            "possession": 55,
            "manOfTheMatch": 1,
            "fouls": 4,
            "yellowCards": 1,
            "offsides": 2,
            "multiplier": 1.0,
        })
        # A completed win pays the configured flat amount and the wallet must
        # move by exactly that. Assert against the constant so a retune does
        # not read as a regression.
        # The payout mode is a user setting, and the launcher runs this suite
        # before startup -- so asserting the flat amount unconditionally would
        # mean selecting dynamic rewards stopped the game from booting.
        if beta_identity_module.MATCH_REWARD_MODE == "dynamic":
            expected_win = int(result["reward"]["totalCoins"])
            if expected_win <= 0 or int(result["reward"]["skillAward"]) == 0:
                fail(f"dynamic rewards must pay a stat-derived amount: {result['reward']}")
        else:
            expected_win = int(MATCH_RESULT_FLAT_COINS["WIN"])
        if result["rewardCoins"] != expected_win or result["credits"] != expected_win:
            fail(f"FUT14 reward regression: expected {expected_win}, got {result}")
        replay = store.settle_match({"matchId": "verify-match", "completed": 1, "minutesPlayed": 90})
        if not replay.get("idempotent") or replay["credits"] != expected_win:
            fail(f"match reward paid more than once: {replay}")

        # A bronze pack can now be bought from earned match coins. It must debit
        # the wallet and produce a ledger entry, while the original dev profile
        # remains unrelated because this is a dedicated BETA DB.
        bronze_price = int(PACK_DEFINITIONS[1]["priceCoins"])
        expected_after_pack = expected_win - bronze_price
        pack = store.purchase_pack(1, currency="COINS")
        if int(pack.get("credits", -1)) != expected_after_pack:
            fail(f"{bronze_price}-coin Bronze Pack should leave {expected_after_pack} "
                 f"from {expected_win}, got {pack.get('credits')}")
        ledger = store.wallet_ledger(20)["transactions"]
        reasons = {row["reason"] for row in ledger}
        if not {"MATCH_REWARD", "PACK_PURCHASE"}.issubset(reasons):
            fail(f"missing wallet ledger reasons: {reasons}")
        metrics = store.metrics()
        if metrics["packsOpenedToday"] != 1 or metrics["matchesCompletedToday"] != 1 or metrics["matchesAbandonedToday"] != 1:
            fail(f"BETA counters invalid: {metrics}")
        if metrics["coinsInCirculation"] != expected_after_pack:
            fail(f"economy circulation should equal {expected_after_pack} in one-account verifier, "
                 f"got {metrics['coinsInCirculation']}")

        # A cup at a later round with no saved bracket must never be advertised or
        # served as resumable. Winning a non-final round stores round+1 with a
        # blank tournamentData and waits for the client to PUT the bracket; a
        # client that never returns leaves exactly this state, and serving it
        # crashed FIFA on 2026-08-15 (808,335,154-byte EASTL vector, out of
        # memory) because the empty buffer put the response members in the order
        # the stream parser cannot read.
        crash_store = BetaIdentityStore(str(Path(td) / "beta-resume.sqlite3"), "existing")
        if crash_store._tournament_progress_is_resumable(2, "", ""):
            fail("a later round with no saved bracket must not count as resumable")
        if crash_store._tournament_progress_is_resumable(4, "   ", "AAAAAA=="):
            fail("whitespace-only tournamentData is not a bracket")
        if not crash_store._tournament_progress_is_resumable(2, "saved-bracket", ""):
            fail("a later round WITH a saved bracket must stay resumable")
        with closing(sqlite3.connect(Path(td) / "beta-resume.sqlite3")) as poison, poison:
            poison.execute(
                "INSERT INTO beta_tournament_progress (persona_id,tournament_id,round_value,"
                "data_version,tournament_data,progress_data_version,progress_data,updated_at) "
                "VALUES (1000001,4,2,1,'',1,'',0) "
                "ON CONFLICT(persona_id,tournament_id) DO UPDATE SET round_value=2,tournament_data='',progress_data=''"
            )
        if crash_store.offline_tournament_user_list().get("tournamentId") != []:
            fail("a bracket-less cup must not be advertised as Underway")
        served = crash_store.offline_tournament_user(4)
        if served != {"tournamentId": 4}:
            fail(f"a bracket-less cup must not be served as a resume document: {served}")

        # The developer test float is a top-up, not a one-shot grant. It used to be
        # keyed on a fixed build reference, so once that ledger row existed the tool
        # silently did nothing for the life of the save.
        float_store = BetaIdentityStore(str(Path(td) / "beta-float.sqlite3"), "existing")
        first = float_store.ensure_consumables_beta_test_balance(1_000_000)
        if int(first.get("balanceAfter", 0)) != 1_000_000 or first.get("idempotent"):
            fail(f"test float did not top the club up to the target: {first}")
        repeat = float_store.ensure_consumables_beta_test_balance(1_000_000)
        if int(repeat.get("granted", -1)) != 0 or not repeat.get("idempotent"):
            fail(f"test float must not stack once the club is at the target: {repeat}")
        if int(float_store.credits()["credits"]) != 1_000_000:
            fail(f"repeat top-up moved the balance: {float_store.credits()}")
        spent = int(PACK_DEFINITIONS[1]["priceCoins"])
        float_store.purchase_pack(1, currency="COINS")
        if int(float_store.credits()["credits"]) != 1_000_000 - spent:
            fail("test-float club could not spend normally after the top-up")
        restored = float_store.ensure_consumables_beta_test_balance(1_000_000)
        if int(restored.get("granted", 0)) != spent or int(restored.get("balanceAfter", 0)) != 1_000_000:
            fail(f"a spent test float must be restorable for the next test round: {restored}")
        grants = [
            row for row in float_store.wallet_ledger(20)["transactions"]
            if row["reason"] == "BETA_CONSUMABLES_TEST_GRANT"
        ]
        if len(grants) != 2:
            fail(f"each top-up needs its own ledger row: {grants}")

        # BUG-002: "Create New Squad" is POST /ut/game/fifa14/squad with "id":0 and
        # no id in the path -- the exact bodies captured in redirect-probe.log. It
        # used to resolve to the *active* squad, so a new squad was never created
        # and the "COPY <name>" variant overwrote squad 1. Run it against its own
        # DB so the economy assertions above stay on a single-squad club.
        squads_db = Path(td) / "beta-squads.sqlite3"
        squads_store = BetaIdentityStore(str(squads_db), "existing")
        default_squad = squads_store.squad_list()["squadList"][0]
        default_ids = [int((row.get("itemData") or {}).get("id", 0) or 0) for row in default_squad["players"]]
        created = squads_store.save_squad({
            "id": 0, "squadName": "usa", "chemistry": 0, "starRating": 0, "rating": 0,
            "formation": "f442", "manager": [{"id": 0}], "players": [],
        }, requested_id=None)
        new_id = int(created.get("createdSquadId", 0) or 0)
        if new_id <= int(default_squad["id"]):
            fail(f"POST /squad with id 0 must INSERT a new squad, got createdSquadId={created.get('createdSquadId')}")
        listing = {int(row["id"]): row for row in squads_store.squad_list()["squadList"]}
        if len(listing) != 2:
            fail(f"create must leave the original squad in place: {sorted(listing)}")
        untouched = listing[int(default_squad["id"])]
        if [int((row.get("itemData") or {}).get("id", 0) or 0) for row in untouched["players"]] != default_ids:
            fail("creating a squad rewrote the existing squad's players")
        if untouched["squadName"] != default_squad["squadName"] or not untouched["active"]:
            fail(f"the existing squad must stay named and active while a new squad is empty: {untouched['squadName']}")
        fresh = listing[new_id]
        if fresh["squadName"] != "usa" or fresh["active"]:
            fail(f"new squad metadata wrong: name={fresh['squadName']} active={fresh['active']}")
        if len(fresh["players"]) != 23 or any(int((row.get("itemData") or {}).get("id", 0) or 0) for row in fresh["players"]):
            fail(f"a new squad must be 23 empty slots: {fresh['players']}")

        # Building it up one card at a time is a run of PUT /squad/{id} bodies that
        # each carry an empty squadName and far fewer than eleven players. Neither
        # the sparse guard nor the bootstrap auto-fill may touch them.
        for count in (1, 3, 6):
            partial = [{"index": i, "itemData": {"id": default_ids[i] if i < count else 0}, "kitNumber": 0}
                       for i in range(23)]
            squads_store.save_squad({
                "id": new_id, "squadName": "", "formation": "f4141",
                "chemistry": 0, "starRating": 0, "players": partial,
            }, requested_id=new_id)
            built = squads_store.squad_detail(new_id)
            placed = sum(1 for row in built["players"] if int((row.get("itemData") or {}).get("id", 0) or 0) > 0)
            if placed != count:
                fail(f"partial squad build lost players: sent {count}, stored {placed}")
            if built["squadName"] != "usa":
                fail(f"a nameless PUT renamed the squad to {built['squadName']!r}")
        if [int((row.get("itemData") or {}).get("id", 0) or 0)
                for row in squads_store.squad_detail(int(default_squad["id"]))["players"]] != default_ids:
            fail("building a second squad disturbed the first one")

        # FIFA fields both teams through a fixed 22-entry entity table, so the six
        # player squad above must never reach the match builder.
        match_squad = squads_store.create_match({"squadId": 0, "type": "OFFLINE"})["squad"]
        fieldable = sum(1 for row in match_squad.get("players", []) if int((row.get("itemData") or {}).get("id", 0) or 0) > 0)
        if fieldable < 11:
            fail(f"CreateMatch served a squad that cannot field eleven players: {fieldable}")
        squads_store.reset_match({})

        # Renaming from the squad selector PUTs the squad with no players array.
        # save_squad used to return early on that shape -- correct for the retail
        # tournament handoff (captain/kicktakers, no name) but it also swallowed
        # every rename, so a new name never stuck.
        renamed_players = squads_store.squad_detail(new_id)["players"]
        squads_store.save_squad({"id": new_id, "squadName": "Renamed XI"}, requested_id=new_id)
        after_rename = squads_store.squad_detail(new_id)
        if after_rename["squadName"] != "Renamed XI":
            fail(f"a name-only squad PUT must rename the squad: {after_rename['squadName']!r}")
        if after_rename["players"] != renamed_players:
            fail("renaming a squad must not disturb its players")
        squads_store.save_squad(
            {"id": new_id, "captain": default_ids[0], "kicktakers": [default_ids[0]]},
            requested_id=new_id,
        )
        if squads_store.squad_detail(new_id)["squadName"] != "Renamed XI":
            fail("the player-less tournament handoff PUT must not touch the name")
        # A rename can also ride along on a write the sparse guard rejects. The
        # guard still has to protect the players, but the typed name is not part
        # of the parser hiccup: every captured sparse write has an empty name.
        blank = [{"index": i, "itemData": {"id": 0}, "kitNumber": 0} for i in range(23)]
        before_sparse_rename = squads_store.squad_detail(int(default_squad["id"]))["players"]
        squads_store.save_squad({
            "id": int(default_squad["id"]), "squadName": "Sparse Rename",
            "formation": "f442", "chemistry": 0, "starRating": 0, "players": blank,
        }, requested_id=int(default_squad["id"]))
        sparse_renamed = squads_store.squad_detail(int(default_squad["id"]))
        if sparse_renamed["squadName"] != "Sparse Rename":
            fail(f"a rename carried on a sparse write was swallowed: {sparse_renamed['squadName']!r}")
        if sparse_renamed["players"] != before_sparse_rename:
            fail("the sparse-write guard stopped protecting the 23 slots")

        # "Copy Squad" posts the same id 0 with a full 23-slot players array.
        copied = squads_store.save_squad({
            "id": 0, "squadName": "COPY  Local XI", "chemistry": 56, "starRating": 60, "rating": 60,
            "formation": "f4141", "manager": [{"id": 0}],
            "players": json.loads(json.dumps(default_squad["players"])),
        }, requested_id=None)
        copy_id = int(copied.get("createdSquadId", 0) or 0)
        if copy_id in (0, new_id, int(default_squad["id"])):
            fail(f"copy-squad POST must create a third squad, got {copy_id}")
        copy_detail = squads_store.squad_detail(copy_id)
        if [int((row.get("itemData") or {}).get("id", 0) or 0) for row in copy_detail["players"]] != default_ids:
            fail("copied squad did not reproduce the source players")
        if [int((row.get("itemData") or {}).get("id", 0) or 0)
                for row in squads_store.squad_detail(int(default_squad["id"]))["players"]] != default_ids:
            fail("copy-squad POST overwrote the squad it copied")

        # Clearing the club must land on the *same* starter state a first run
        # provisions -- it reuses that code path rather than defining a second
        # idea of "starter" -- and must keep the wallet, because setting a
        # balance is a separate decision from wiping the cards.
        reset_store = BetaIdentityStore(str(Path(td) / "beta-reset.sqlite3"), "existing")
        fresh_profile = reset_store.beta_profile_summary()
        reset_store.ensure_consumables_beta_test_balance(40_000)
        # Buying puts a real owned row in the club, which is what the reset has
        # to clear; an unopened pack alone would not prove anything.
        bought_consumable = reset_store.market_search(
            {"type": ["consumables"], "start": ["0"], "num": ["1"]}
        )["auctionInfo"][0]
        reset_store.market_bid(int(bought_consumable["tradeId"]), MARKET_CONSUMABLE_BUY_NOW)
        reset_store.save_squad({
            "id": 0, "squadName": "Doomed", "formation": "f442", "players": [],
        }, requested_id=None)
        coins_before_reset = int(reset_store.credits()["credits"])
        if int(reset_store.beta_profile_summary()["ownedItems"]) <= int(fresh_profile["ownedItems"]):
            fail("the reset fixture did not actually add anything to the club")
        after_reset = reset_store.reset_club_to_starter()
        if int(after_reset["ownedItems"]) != int(fresh_profile["ownedItems"]):
            fail(f"reset club is not the provisioned starter club: {after_reset['ownedItems']} "
                 f"items vs {fresh_profile['ownedItems']} on a first run")
        if int(after_reset["squadPlayers"]) != int(fresh_profile["squadPlayers"]):
            fail(f"reset starter squad is incomplete: {after_reset['squadPlayers']}")
        if int(reset_store.credits()["credits"]) != coins_before_reset:
            fail("clearing the club must not change the coin balance")
        reset_squads = reset_store.squad_list()["squadList"]
        if len(reset_squads) != 1 or reset_squads[0]["squadName"] != "Starter XI":
            fail(f"reset left extra squads behind: {[s['squadName'] for s in reset_squads]}")
        if reset_store.offline_tournament_user_list().get("tournamentId") != []:
            fail("reset left a cup advertised as underway")
        # A fresh club reads 0-0-0. The hub derives the record from settled match
        # sessions, so leaving those behind gave a brand-new club somebody else's
        # results (dzevallos/f14-localfut#7).
        if reset_store.match_record() != {"wins": 0, "draws": 0, "losses": 0}:
            fail(f"a cleared club must have no W-D-L record: {reset_store.match_record()}")
        fut_user_after_reset = reset_store.ensure_fut_user()
        if any(int(fut_user_after_reset.get(key, -1)) != 0 for key in ("wins", "draws", "losses")):
            fail(f"/user still publishes a stale record after a club reset: {fut_user_after_reset}")
        if len(reset_store.create_match({"squadId": 0, "type": "OFFLINE"})["squad"]["players"]) != 23:
            fail("a reset club cannot start a match")

        # The settings file overlays the built-in tuning. What matters is that a
        # malformed or hostile file can never take the server down: the launcher
        # runs this suite before startup and throws away its own output, so an
        # exception here is a window that closes with no message.
        settings_file = Path(td) / "settings-probe.json"
        os.environ["FIFA14_LOCAL_SETTINGS"] = str(settings_file)
        try:
            settings_file.write_text('{"matchRewardMode": "dynamic"}', encoding="utf-8")
            if fut_local_settings.load_settings(refresh=True).get("matchRewardMode") != "dynamic":
                fail("the dynamic payout mode must be accepted")
            settings_file.write_text('{"matchRewardMode": "sideways"}', encoding="utf-8")
            if "matchRewardMode" in fut_local_settings.load_settings(refresh=True):
                fail("an unknown payout mode must be ignored, not passed through")
            settings_file.write_text('{"matchRewards": {"WIN": -5, "DRAW": "abc"}, '
                                     '"market": {"rotationFraction": 99999}}', encoding="utf-8")
            loaded = fut_local_settings.load_settings(refresh=True)
            if loaded.get("matchRewards", {}).get("WIN") != 0:
                fail(f"a negative payout must clamp to zero, got {loaded}")
            if "DRAW" in loaded.get("matchRewards", {}):
                fail(f"a non-numeric payout must be dropped, not coerced: {loaded}")
            if loaded.get("market", {}).get("rotationFraction") != 64:
                fail(f"an absurd rotation fraction must clamp: {loaded}")
            settings_file.write_text("{ not json at all", encoding="utf-8")
            if fut_local_settings.load_settings(refresh=True) != {}:
                fail("a malformed settings file must be ignored, not partially applied")
            settings_file.write_text('["not", "an", "object"]', encoding="utf-8")
            if fut_local_settings.load_settings(refresh=True) != {}:
                fail("a non-object settings file must be ignored")
            settings_file.unlink()
            if fut_local_settings.load_settings(refresh=True) != {}:
                fail("a missing settings file must read as no overrides")
        finally:
            os.environ.pop("FIFA14_LOCAL_SETTINGS", None)
            fut_local_settings.load_settings(refresh=True)

        # sqlite3.Connection.__exit__ commits/rolls back but does NOT close the
        # underlying handle. Windows therefore keeps the TemporaryDirectory DB
        # locked unless we explicitly close it before cleanup.
        with closing(sqlite3.connect(db)) as con, con:
            beta_schema = con.execute("SELECT meta_value FROM schema_meta WHERE meta_key='beta_schema'").fetchone()
            if not beta_schema or beta_schema[0] != "fifa14-local-fut-v2.41.1-beta2.24":
                fail("BETA schema marker missing")

        print(json.dumps({
            "status": "ok",
            "starterPlayers": 23,
            "provenStarterCosmetics": 3,
            "syntheticScannedCosmetics": 3,
            "startingCoins": 0,
            "startingFifaPoints": 0,
            "offlineSeasons": len(season_rows),
            "offlineTournamentsDefault": len(tournament_rows),
            "offlineTournamentRounds": len(rounds),
            "storeSafeTierAssets": {str(int(row["packType"])): int(row["assetId"]) for row in offers},
            "verificationMatchReward": expected_win,
            "postPackCoins": expected_after_pack,
            "walletLedgerEntries": len(ledger),
            "metrics": metrics,
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
