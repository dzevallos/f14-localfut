#!/usr/bin/env python3
"""Menu-driven settings editor for the local FIFA 14 FUT server.

Everything here is either a value in the settings file (read by the server at
startup) or a direct, backed-up edit to the save. Nothing needs the tree to be
re-coded.

Safety rules this tool keeps, because they are the ones that have bitten before:
  * never touch the save while fifa14.exe or the server is running;
  * back the save up before every write, into the same backups\\ folder the rest
    of the project uses;
  * validate before writing -- the launcher runs the verifier suite at startup
    and refuses to boot if anything is off, so a bad settings file would show up
    as a window that closes with no message.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import fut_local_settings as settings_module  # noqa: E402

SAVE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FIFA14LocalFUTBeta"
SAVE_PATH = SAVE_DIR / "local-fut-beta-v2410.sqlite3"
BACKUP_DIR = SAVE_DIR / "backups"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _defaults() -> dict:
    """The built-in tuning, read with any settings file ignored."""
    env = dict(os.environ)
    env["FIFA14_LOCAL_SETTINGS"] = str(Path(os.devnull))
    code = (
        "import json,sys;sys.path.insert(0,r'%s');"
        "import beta_identity as b, local_identity as l;"
        "print(json.dumps({'matchRewards':b.MATCH_RESULT_FLAT_COINS,"
        "'tournamentPrizes':{int(t['tournamentId']):{'name':t['name'],'prize':int(t['prize']),"
        "'repeatPrize':int(t['repeatPrize'])} for t in b.OFFLINE_TOURNAMENTS},"
        "'matchRewardMode':b.MATCH_REWARD_MODE,"
        "'market':{'rotationFraction':l.MARKET_ROTATION_FRACTION,"
        "'rotationMinutes':l.MARKET_ROTATION_SECONDS//60,"
        "'consumablePrice':l.MARKET_CONSUMABLE_BUY_NOW}}))" % SERVER
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise SystemExit(f"could not read the built-in tuning:\n{result.stderr.strip()}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _effective(defaults: dict, saved: dict) -> dict:
    """What the server will actually use: defaults with the settings file on top."""
    rewards = dict(defaults["matchRewards"])
    rewards.setdefault("DNF", 0)
    rewards.update(saved.get("matchRewards") or {})
    prizes = {int(k): dict(v) for k, v in defaults["tournamentPrizes"].items()}
    for tournament_id, override in (saved.get("tournamentPrizes") or {}).items():
        prizes.setdefault(int(tournament_id), {}).update(override)
    market = dict(defaults["market"])
    market.update(saved.get("market") or {})
    return {
        "matchRewards": rewards,
        "matchRewardMode": saved.get("matchRewardMode", defaults.get("matchRewardMode", "flat")),
        "tournamentPrizes": prizes,
        "market": market,
    }


def _game_is_running() -> str:
    try:
        listing = subprocess.run(
            ["tasklist", "/fi", "imagename eq fifa14.exe", "/nh"],
            capture_output=True, text=True, timeout=20,
        ).stdout.lower()
        if "fifa14.exe" in listing:
            return "fifa14.exe"
    except Exception:
        pass
    try:
        listing = subprocess.run(
            ["wmic", "process", "where", "name like '%python%'", "get", "commandline"],
            capture_output=True, text=True, timeout=20,
        ).stdout.lower()
        if "probe.py" in listing:
            return "the local FUT server (probe.py)"
    except Exception:
        pass
    return ""


def _require_idle() -> bool:
    running = _game_is_running()
    if running:
        print(f"\n  !! {running} is running. Close it first -- editing the save under a")
        print("     running game loses whatever it writes next.")
        return False
    return True


def _backup(label: str) -> Path | None:
    if not SAVE_PATH.is_file():
        print(f"\n  !! no save at {SAVE_PATH}\n     Launch the game once first.")
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"local-fut-beta-v2410.pre-{label}-{stamp}.sqlite3"
    shutil.copy2(SAVE_PATH, target)
    print(f"  backed up to {target.name}")
    return target


def _store():
    from beta_identity import BetaIdentityStore
    return BetaIdentityStore(str(SAVE_PATH), "existing")


def _ask_int(prompt: str, current: int, low: int = 0, high: int = 2_000_000_000) -> int | None:
    raw = input(f"  {prompt} [{current:,}]: ").strip().replace(",", "").replace("_", "")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        print("  not a number; leaving it unchanged.")
        return None
    if not low <= value <= high:
        print(f"  must be between {low:,} and {high:,}; leaving it unchanged.")
        return None
    return value


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------
def show(defaults: dict, saved: dict) -> None:
    effective = _effective(defaults, saved)
    mode = effective.get("matchRewardMode", "flat")
    mode_mark = "*" if "matchRewardMode" in saved else " "
    print(f"\n {mode_mark} Match rewards  [mode: {mode}]")
    if mode == "dynamic":
        print("     minutes played plus the stat bonus; the amounts below are unused")
    for key in ("WIN", "DRAW", "LOSS", "DNF"):
        value = int(effective["matchRewards"].get(key, 0))
        mark = "*" if key in (saved.get("matchRewards") or {}) else " "
        print(f"   {mark} {key:<5} {value:>12,}")
    print("\n  Tournament payouts (first clear / repeat)")
    for tournament_id in sorted(effective["tournamentPrizes"]):
        row = effective["tournamentPrizes"][tournament_id]
        mark = "*" if str(tournament_id) in {str(k) for k in (saved.get("tournamentPrizes") or {})} else " "
        print(f"   {mark} {row.get('name', tournament_id):<13} {int(row['prize']):>10,} / {int(row['repeatPrize']):>9,}")
    market = effective["market"]
    print("\n  Transfer market")
    print(f"     1 card in {market['rotationFraction']} listed, rotating every {market['rotationMinutes']} min")
    print(f"     consumables always stocked at {int(market['consumablePrice']):,} coins")
    if SAVE_PATH.is_file():
        try:
            store = _store()
            summary = store.beta_profile_summary()
            players = int(store.hub_data().get("clubPlayers", 0))
            print(f"\n  Club: {players} players ({summary.get('squadPlayers')} in the active squad), "
                  f"{int(summary.get('coins', 0)):,} coins")
        except Exception as error:
            print(f"\n  (could not read the save: {error})")
    print(f"\n  * = overridden in {settings_module.settings_path()}")
    print("  Settings changes take effect the next time the server starts.")


def edit_reward_mode(defaults: dict, saved: dict) -> dict:
    current = _effective(defaults, saved).get("matchRewardMode", "flat")
    print(f"\n  How a completed match pays. Currently: {current}")
    print()
    print("    flat     the fixed win / draw / loss amounts, decided only by the result")
    print("    dynamic  the original FIFA payout: an award that scales with minutes")
    print("             played, plus a bonus from goals, shots, tackles, corners,")
    print("             passing, possession and clean sheets, minus fouls and cards")
    print()
    print("  Roughly, with the amounts set to 15,000 / 1,000 / 750:")
    print("    a 1-0 win pays 15,000 flat, or about 630 dynamic")
    print("    a 5-0 win pays 15,000 flat, or about 830 dynamic")
    print()
    print("  Dynamic ignores the win/draw/loss amounts. Cup round coins follow WIN either way.")
    answer = input(f"  mode (flat/dynamic) [{current}]: ").strip().lower()
    if not answer:
        print("  unchanged.")
        return saved
    if answer not in {"flat", "dynamic"}:
        print("  not a mode; leaving it unchanged.")
        return saved
    saved["matchRewardMode"] = answer
    print(f"  payout mode set to {answer}.")
    return saved


def edit_match_rewards(defaults: dict, saved: dict) -> dict:
    effective_all = _effective(defaults, saved)
    effective = effective_all["matchRewards"]
    if effective_all.get("matchRewardMode") == "dynamic":
        print("\n  Note: the payout mode is dynamic, so these amounts are not used.")
        print("  Change the mode from the menu to make them apply again.")
    print("\n  Coins paid for a completed match. Blank keeps the current value.")
    print("  DNF is what an abandoned/quit match pays; it is 0 by default.")
    changes = dict(saved.get("matchRewards") or {})
    for key in ("WIN", "DRAW", "LOSS", "DNF"):
        value = _ask_int(key, int(effective.get(key, 0)))
        if value is not None:
            changes[key] = value
    if changes:
        saved["matchRewards"] = changes
    print("\n  Note: cup rounds advertise and pay the WIN amount, by design.")
    return saved


def edit_tournament_prizes(defaults: dict, saved: dict) -> dict:
    effective = _effective(defaults, saved)["tournamentPrizes"]
    print("\n  Cup payouts. Blank keeps the current value.")
    changes = {str(k): dict(v) for k, v in (saved.get("tournamentPrizes") or {}).items()}
    for tournament_id in sorted(effective):
        row = effective[tournament_id]
        print(f"\n  {row.get('name', tournament_id)}")
        first = _ask_int("first clear", int(row["prize"]), low=1)
        repeat = _ask_int("repeat win ", int(row["repeatPrize"]), low=1)
        entry = changes.get(str(tournament_id), {})
        if first is not None:
            entry["prize"] = first
        if repeat is not None:
            entry["repeatPrize"] = repeat
        if entry:
            changes[str(tournament_id)] = entry
    if changes:
        saved["tournamentPrizes"] = changes
    return saved


def edit_market(defaults: dict, saved: dict) -> dict:
    market = _effective(defaults, saved)["market"]
    print("\n  Transfer market stock. Blank keeps the current value.")
    print("  A fraction of 1 lists the entire catalogue at once (~42,000 cards).")
    changes = dict(saved.get("market") or {})
    fraction = _ask_int("list 1 card in N", int(market["rotationFraction"]), low=1, high=64)
    if fraction is not None:
        changes["rotationFraction"] = fraction
    minutes = _ask_int("rotate every N minutes", int(market["rotationMinutes"]), low=1, high=1440)
    if minutes is not None:
        changes["rotationMinutes"] = minutes
    price = _ask_int("consumable price", int(market["consumablePrice"]), low=0)
    if price is not None:
        changes["consumablePrice"] = price
    if changes:
        saved["market"] = changes
    return saved


def _set_balance(currency: str) -> None:
    """Set the coin or FIFA Point balance. Both live on the club row."""
    if not _require_idle() or not SAVE_PATH.is_file():
        return
    coins = currency == "COINS"
    column = "coins" if coins else "fifa_points"
    label = "coin" if coins else "FIFA Point"
    store = _store()
    balances = store.currencies()
    current = int(balances["credits"] if coins else balances["fifaPoints"])
    target = _ask_int(f"set the {label} balance to", current)
    if target is None or target == current:
        print("  unchanged.")
        return
    if _backup(f"set-{column}") is None:
        return
    from contextlib import closing
    with store._lock, closing(store._connect()) as connection, connection:
        persona_id = int(store._identity(connection)["persona_id"])
        connection.execute(f"UPDATE clubs SET {column}=? WHERE persona_id=?", (target, persona_id))
        connection.execute(
            "INSERT INTO wallet_transactions (persona_id,created_at,currency,amount,balance_before,"
            "balance_after,reason,reference_type,reference_id,metadata_json) "
            "VALUES (?,?,?,?,?,?, 'MANUAL_ADJUST','settings-tool',?, '{}')",
            (persona_id, int(time.time()), currency, target - current, current, target,
             f"set-{column}-{int(time.time())}"),
        )
    print(f"  {label}s {current:,} -> {target:,}")


def clear_club() -> None:
    if not _require_idle() or not SAVE_PATH.is_file():
        return
    print("\n  This removes every card, squad, pack and cup in progress, and")
    print("  provisions the 23-player bronze starter squad again.")
    print("  Your coin balance is kept.")
    if input("  Type CLEAR to confirm: ").strip() != "CLEAR":
        print("  cancelled.")
        return
    if _backup("club-reset") is None:
        return
    store = _store()
    summary = store.reset_club_to_starter()
    # ownedItems includes the shipped kit/badge/stadium catalogue (about 1,800
    # rows on a real install), so report the players separately or the number
    # looks like the wipe did not happen.
    players = int(store.hub_data().get("clubPlayers", 0))
    cosmetics = max(0, int(summary.get("ownedItems", 0)) - players)
    print(f"  club reset: {players} players ({summary.get('squadPlayers')} in the starter squad), "
          f"{cosmetics:,} club cosmetics kept, {int(summary.get('coins', 0)):,} coins kept")


def reset_settings() -> dict:
    print("\n  This restores the built-in payouts, prizes and market stock.")
    print("  It does not touch your save.")
    if input("  Type RESET to confirm: ").strip() != "RESET":
        print("  cancelled.")
        return settings_module.load_settings()
    path = settings_module.write_settings({})
    print(f"  cleared {path}")
    return {}


MENU = """
========================================================
  FIFA 14 Local FUT - settings
========================================================
  1  Show current settings
  2  Match reward mode    (flat / dynamic)
  3  Match rewards        (win / draw / loss / dnf)
  4  Tournament payouts   (first clear / repeat)
  5  Transfer market      (rotation, consumable price)
  6  Set coin balance                        [save]
  7  Set FIFA Point balance                  [save]
  8  Clear club, keep the starter squad      [save]
  9  Reset all settings to defaults
  0  Exit
"""


def main() -> int:
    try:
        defaults = _defaults()
    except SystemExit as error:
        print(error)
        return 1
    saved = settings_module.load_settings()
    dirty = False
    while True:
        print(MENU)
        choice = input("  choose: ").strip()
        if choice == "0":
            break
        if choice == "1":
            show(defaults, saved)
        elif choice == "2":
            saved = edit_reward_mode(defaults, saved)
            dirty = True
        elif choice == "3":
            saved = edit_match_rewards(defaults, saved)
            dirty = True
        elif choice == "4":
            saved = edit_tournament_prizes(defaults, saved)
            dirty = True
        elif choice == "5":
            saved = edit_market(defaults, saved)
            dirty = True
        elif choice == "6":
            _set_balance("COINS")
        elif choice == "7":
            _set_balance("POINTS")
        elif choice == "8":
            clear_club()
        elif choice == "9":
            saved = reset_settings()
            dirty = False
        else:
            print("  no such option.")
            continue
        if dirty:
            path = settings_module.write_settings(saved)
            # Re-read through the loader so what is shown is what the server will
            # accept, not what was typed.
            saved = settings_module.load_settings()
            print(f"\n  saved to {path}")
            dirty = False
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
