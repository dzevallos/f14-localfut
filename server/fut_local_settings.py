"""User-editable tuning for the local FUT server.

Match payouts, cup prizes and market stock used to be Python constants, so
changing them meant editing server code. They are still defined as constants --
that is what the verifiers assert against -- but this module lets a settings
file override them at import time, which is what tools/fut_settings.py writes.

The file lives beside the save (``%LOCALAPPDATA%\\FIFA14LocalFUTBeta``) so it
survives extracting a new build over the tree.

Two rules matter more than anything else here:

* **Never raise.** This is imported while the server starts, and the launcher
  throws away its own output on failure (see the handoff's operating rules), so
  an exception here surfaces as a window that closes with no explanation. A
  malformed or hostile file is ignored with a diagnostic instead.
* **Validate, do not trust.** Every value is bounded. A settings file cannot put
  the server into a state the verifiers would reject, because the launcher runs
  those verifiers before startup and would refuse to boot.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "local-fut-settings.json"
SETTINGS_VERSION = 1

# name -> (minimum, maximum). Bounds are deliberately generous: this is a local
# single-player server and the point is to let people tune it. They exist to
# keep a typo from producing a negative wallet or a market that never restocks.
_COIN_BOUNDS = (0, 2_000_000_000)
_MATCH_RESULT_KEYS = ("WIN", "DRAW", "LOSS", "DNF")


def settings_path() -> Path:
    """Where the settings file lives, next to the persistent save."""
    override = os.environ.get("FIFA14_LOCAL_SETTINGS")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local_app_data) / "FIFA14LocalFUTBeta" / SETTINGS_FILENAME


def _diagnostic(message: str) -> None:
    # stderr, never stdout: stdout is the probe's JSON log.
    print(f"[fut-settings] {message}", file=sys.stderr, flush=True)


def _bounded(value: Any, low: int, high: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


_cache: tuple[str, dict[str, Any]] | None = None


def load_settings(*, refresh: bool = False) -> dict[str, Any]:
    """Read and validate the settings file. Returns {} when there is nothing usable.

    Cached per path: both store modules ask for this at import, and reading the
    file twice would also report any complaint about it twice.
    """
    global _cache
    path = settings_path()
    if not refresh and _cache is not None and _cache[0] == str(path):
        return dict(_cache[1])
    settings = _load_settings_uncached(path)
    _cache = (str(path), settings)
    return dict(settings)


def _load_settings_uncached(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        _diagnostic(f"ignoring unreadable settings file {path}: {error}")
        return {}
    if not isinstance(raw, dict):
        _diagnostic(f"ignoring settings file {path}: top level must be an object")
        return {}

    settings: dict[str, Any] = {}

    rewards = raw.get("matchRewards")
    if isinstance(rewards, dict):
        cleaned = {}
        for key in _MATCH_RESULT_KEYS:
            if key in rewards:
                value = _bounded(rewards[key], *_COIN_BOUNDS)
                if value is None:
                    _diagnostic(f"matchRewards.{key} is not a number; keeping the built-in value")
                else:
                    cleaned[key] = value
        if cleaned:
            settings["matchRewards"] = cleaned

    mode = raw.get("matchRewardMode")
    if isinstance(mode, str):
        cleaned_mode = mode.strip().lower()
        if cleaned_mode in {"flat", "dynamic"}:
            settings["matchRewardMode"] = cleaned_mode
        else:
            _diagnostic(f"matchRewardMode must be 'flat' or 'dynamic', not {mode!r}; keeping flat")

    prizes = raw.get("tournamentPrizes")
    if isinstance(prizes, dict):
        cleaned_prizes: dict[int, dict[str, int]] = {}
        for key, value in prizes.items():
            tournament_id = _bounded(key, 1, 99)
            if tournament_id is None or not isinstance(value, dict):
                continue
            entry = {}
            for field in ("prize", "repeatPrize"):
                if field in value:
                    amount = _bounded(value[field], 1, _COIN_BOUNDS[1])
                    if amount is None:
                        _diagnostic(f"tournamentPrizes.{key}.{field} is not a number; ignoring it")
                    else:
                        entry[field] = amount
            if entry:
                cleaned_prizes[tournament_id] = entry
        if cleaned_prizes:
            settings["tournamentPrizes"] = cleaned_prizes

    # Diagnostics a tester has to be able to turn on without a terminal. These
    # were env-var-only, which does not work for the way the game is actually
    # started: RUN_FIFA14_LOCAL_BETA.cmd relaunches itself elevated through
    # ShellExecute, and that boundary drops any variable set in the user's shell.
    # The settings file is next to the save and is read after elevation, so it
    # reaches the server either way. An env var still wins when one is set.
    diagnostics = raw.get("diagnostics")
    if isinstance(diagnostics, dict):
        cleaned_diagnostics: dict[str, Any] = {}
        if "playerStatProbe" in diagnostics:
            cleaned_diagnostics["playerStatProbe"] = bool(diagnostics.get("playerStatProbe"))
        season_save = diagnostics.get("seasonSaveMode")
        if isinstance(season_save, str):
            candidate = season_save.strip().lower()
            if candidate in {"blob", "round"}:
                cleaned_diagnostics["seasonSaveMode"] = candidate
            else:
                _diagnostic(f"diagnostics.seasonSaveMode must be 'blob' or 'round', not {season_save!r}")
        if cleaned_diagnostics:
            settings["diagnostics"] = cleaned_diagnostics

    club = raw.get("club")
    if isinstance(club, dict):
        cleaned_club: dict[str, Any] = {}
        # The venue every club plays in. The id is what the client renders and
        # what the tracer forces onto the native offline stadium provider; the
        # name is only the label this server reports. Bounds are the shipped
        # stadium id range, so a typo cannot ask the client for a venue that
        # does not exist -- an unrenderable stadium is a crash, not a blank.
        if "stadiumId" in club:
            stadium_id = _bounded(club.get("stadiumId"), 1, 300)
            if stadium_id is None:
                _diagnostic("club.stadiumId is not a number; keeping the built-in stadium")
            else:
                cleaned_club["stadiumId"] = stadium_id
        name = club.get("stadiumName")
        if isinstance(name, str) and name.strip():
            cleaned_club["stadiumName"] = name.strip()[:96]
        elif "stadiumName" in club:
            _diagnostic("club.stadiumName must be a non-empty string; keeping the built-in name")
        if cleaned_club:
            settings["club"] = cleaned_club

    market = raw.get("market")
    if isinstance(market, dict):
        cleaned_market = {}
        # 1 lists the whole catalogue; the upper bound stops a fraction so large
        # that a rotation would be empty.
        fraction = _bounded(market.get("rotationFraction"), 1, 64) if "rotationFraction" in market else None
        if fraction is not None:
            cleaned_market["rotationFraction"] = fraction
        minutes = _bounded(market.get("rotationMinutes"), 1, 24 * 60) if "rotationMinutes" in market else None
        if minutes is not None:
            cleaned_market["rotationMinutes"] = minutes
        price = _bounded(market.get("consumablePrice"), 0, _COIN_BOUNDS[1]) if "consumablePrice" in market else None
        if price is not None:
            cleaned_market["consumablePrice"] = price
        if cleaned_market:
            settings["market"] = cleaned_market

    return settings


def write_settings(settings: dict[str, Any]) -> Path:
    """Persist settings, creating the folder if the save has not been made yet."""
    global _cache
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": SETTINGS_VERSION, **settings}
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _cache = None
    return path
