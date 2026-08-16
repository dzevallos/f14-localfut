#!/usr/bin/env python3
"""Read-only extractor for the client's own FUT staff card tables.

dzevallos/f14-localfut#11 asked why the Transfer Market's staff tabs are empty.
The answer was "we have no data we trust": `manager-catalog.v237.json` is scraped
reference metadata carrying `liveEmissionEnabled: false`, because its resource
IDs were never verified against this build -- and shipping an assetId the client
cannot resolve is exactly the BUG-004 "DB ERROR" card mechanism.

The client ships the real thing. `cards_ng_db.db` (inside cards0.big/patch.big,
the same archive the kit/stadium/badge scan already reads) has five staff card
tables with their own `carddbid`, which *is* the FUT resource id:

    managercards        166 rows   1000500+   talkrating/negotiation/nation/formationid
    headcoachcards       36 rows   2000001+   attribute/amount
    fitnesscoachcards    36 rows   3000004+   posbonus/fieldpos/amount
    physiocards          42 rows   4000001+   attribute/amount
    gkcoachcards         36 rows   9000001+   attribute/amount

Nothing here is guessed: every id comes out of the user's own installation, so a
card built from these rows is one the client can resolve.

Usage:
    python scan_fifa14_staff_cards.py --game-root "<FIFA 14 folder>" \\
        --output ../server/fifa14-staff-catalog.v2411.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scan_fifa14_match_assets import parse_db_candidates, row_int, safe_rows, unique_tables

SCHEMA = "fifa14-v2411-fut-staff-catalog"

# table -> (itemType on the wire, the client's own market `cat` token)
STAFF_TABLES = {
    "managercards": ("manager", "manager"),
    "headcoachcards": ("headCoach", "headCoach"),
    "gkcoachcards": ("gkCoach", "GKCoach"),
    "fitnesscoachcards": ("fitnessCoach", "fitnessCoach"),
    "physiocards": ("physio", "physio"),
}
# Columns worth carrying. Everything else in these tables is presentation the
# client already owns (names resolve from its own DB by assetid).
CARRIED = (
    "carddbid", "assetid", "value", "rare", "amount", "attribute",
    "talkrating", "negotiation", "nation", "formationid", "posbonus", "fieldpos",
)


def extract(game_root: Path) -> dict[str, Any]:
    databases = parse_db_candidates(
        game_root,
        ("patch.big", "cards0.big"),
        "cards_ng_db.db",
        ("cards_ng_db-meta.xml", "cards_ng_db_meta.xml", "fifa_ng_db-meta.xml"),
        cards=True,
    )
    families: dict[str, list[dict[str, Any]]] = {}
    diagnostics: list[str] = []
    seen: set[str] = set()
    for candidate in databases:
        database = candidate[0] if isinstance(candidate, tuple) else candidate
        try:
            tables = unique_tables(database)
        except Exception as error:  # a candidate that will not parse is not fatal
            diagnostics.append(f"skipped an unreadable card database: {error}")
            continue
        for table in tables:
            name = str(table.name).lower()
            if name not in STAFF_TABLES or name in seen:
                continue
            seen.add(name)
            item_type, category = STAFF_TABLES[name]
            rows: list[dict[str, Any]] = []
            for raw in safe_rows(database, table):
                resource = row_int(raw, "carddbid", default=0)
                asset = row_int(raw, "assetid", default=resource)
                if resource <= 0 or asset <= 0:
                    continue
                entry: dict[str, Any] = {
                    "resourceId": resource,
                    "definitionId": resource,
                    "carddbid": resource,
                    "assetId": asset,
                    "itemType": item_type,
                    "cat": category,
                }
                for column in CARRIED:
                    if column in ("carddbid", "assetid"):
                        continue
                    value = raw.get(column)
                    if value is None:
                        continue
                    try:
                        entry[column] = int(value)
                    except (TypeError, ValueError):
                        continue
                rows.append(entry)
            rows.sort(key=lambda row: int(row["resourceId"]))
            families[item_type] = rows
            diagnostics.append(f"{name}: {len(rows)} card(s)")
    missing = sorted({t for t in STAFF_TABLES} - seen)
    if missing:
        diagnostics.append(f"tables not found in this installation: {missing}")
    return {
        "schema": SCHEMA,
        "note": (
            "Extracted read-only from the installed cards_ng_db.db. Every resourceId is "
            "the client's own carddbid, so these cards resolve rather than rendering as "
            "the DB ERROR sentinel."
        ),
        "staff": families,
        "counts": {key: len(value) for key, value in families.items()},
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only FIFA 14 FUT staff card scanner")
    parser.add_argument("--game-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    document = extract(Path(args.game_root).resolve())
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(document["counts"].values())
    print(json.dumps({"output": str(output), "total": total, **document["counts"]}, indent=1))
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
