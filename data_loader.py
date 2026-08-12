"""Fetches and caches NFL stats + schedule data from nflverse.

We pull directly from the nflverse-data GitHub releases rather than going
through nfl_data_py: that package pins an older, now-stale release tag
("player_stats") that nflverse stopped publishing per-year files for after
2024. "stats_player" (season + week granularity) and "schedules" are the
actively maintained sources, and already ship names/positions/opponents/
fantasy points baked in.
"""
import os
from pathlib import Path
from urllib.error import HTTPError

import certifi
import pandas as pd
import streamlit as st

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SEASON_STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{year}.parquet"
WEEK_STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.parquet"
POST_STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_post_{year}.parquet"
ROSTER_URL = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{year}.parquet"
SCHEDULE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet"
INJURIES_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{year}.parquet"
CONTRACTS_URL = "https://github.com/nflverse/nflverse-data/releases/download/contracts/historical_contracts.parquet"


@st.cache_data(show_spinner=False)
def find_latest_available_season(probe_from: int = 2027, floor: int = 2015) -> int:
    """Probes nflverse for the newest season that actually has a published stats file."""
    for year in range(probe_from, floor, -1):
        try:
            pd.read_parquet(SEASON_STATS_URL.format(year=year))
            return year
        except (HTTPError, FileNotFoundError, OSError):
            continue
    return floor


def default_years(n_years: int = 5) -> list[int]:
    latest = find_latest_available_season()
    return list(range(latest - n_years + 1, latest + 1))


def _fetch(url_template: str, cache_prefix: str, year: int) -> pd.DataFrame | None:
    path = DATA_DIR / f"{cache_prefix}_{year}.parquet"
    if path.exists():
        return pd.read_parquet(path)

    try:
        df = pd.read_parquet(url_template.format(year=year))
    except (HTTPError, FileNotFoundError, OSError):
        return None

    df.to_parquet(path)
    return df


def _rename_common(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={"player_display_name": "player_name", "player_name": "player_short_name"})


@st.cache_data(show_spinner=False)
def load_seasonal_stats(years: tuple[int, ...]) -> pd.DataFrame:
    """One row per player per season."""
    by_year = {y: _fetch(SEASON_STATS_URL, "stats_player_reg", y) for y in years}

    missing = [y for y in years if by_year[y] is None]
    if missing:
        st.warning(f"No published nflverse data yet for season(s): {missing}. Skipping them.")

    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    df = _rename_common(df).rename(columns={"recent_team": "team"})
    df["games"] = df["games"].replace(0, pd.NA)
    return df.dropna(subset=["player_name", "position"])


@st.cache_data(show_spinner=False)
def load_weekly_stats(years: tuple[int, ...]) -> pd.DataFrame:
    """One row per player per game, with opponent_team — needed for defense-vs-position."""
    by_year = {y: _fetch(WEEK_STATS_URL, "stats_player_week", y) for y in years}
    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    return _rename_common(df).dropna(subset=["player_name", "position", "opponent_team"])


@st.cache_data(show_spinner=False)
def load_schedule(season: int) -> pd.DataFrame:
    df = pd.read_parquet(SCHEDULE_URL)
    return df[df["season"] == season][["season", "week", "away_team", "home_team", "gameday"]]


@st.cache_data(show_spinner=False)
def load_playoff_stats(years: tuple[int, ...]) -> pd.DataFrame:
    by_year = {y: _fetch(POST_STATS_URL, "stats_player_post", y) for y in years}
    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    return _rename_common(df).dropna(subset=["player_name", "position"])


@st.cache_data(show_spinner=False)
def load_roster(years: tuple[int, ...]) -> pd.DataFrame:
    """One row per player per season: birth_date, experience, draft capital."""
    by_year = {y: _fetch(ROSTER_URL, "roster", y) for y in years}
    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    df = df[df["gsis_id"] != ""].drop_duplicates(subset=["gsis_id", "season"])

    df["birth_date"] = pd.to_datetime(df["birth_date"], errors="coerce")
    season_start = pd.to_datetime(df["season"].astype(str) + "-09-01")
    df["age"] = (season_start - df["birth_date"]).dt.days / 365.25

    return df.rename(columns={"gsis_id": "player_id"})[
        ["player_id", "season", "age", "years_exp", "draft_number"]
    ]


# The rosters release spells Arizona "AZ"; schedules/stats_player both use "ARI".
TEAM_ABBR_FIXUPS = {"AZ": "ARI"}


@st.cache_data(show_spinner=False)
def load_roster_status(season: int) -> pd.DataFrame:
    """Current roster standing for one season: ACT (active), RES (reserve/PUP/IR/suspended), RET (retired), CUT."""
    df = _fetch(ROSTER_URL, "roster", season)
    if df is None:
        return pd.DataFrame(columns=["player_id", "team", "status"])
    df = df[df["gsis_id"] != ""].drop_duplicates(subset=["gsis_id"], keep="last")
    df = df.rename(columns={"gsis_id": "player_id"})[["player_id", "team", "status"]]
    df["team"] = df["team"].replace(TEAM_ABBR_FIXUPS)
    return df


@st.cache_data(show_spinner=False)
def load_injuries(years: tuple[int, ...]) -> pd.DataFrame:
    """Weekly injury report entries (report_status: Questionable/Doubtful/Out)."""
    by_year = {y: _fetch(INJURIES_URL, "injuries", y) for y in years}
    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    return df.rename(columns={"gsis_id": "player_id"})[["player_id", "season", "week", "report_status"]]


def load_manual_overrides() -> pd.DataFrame:
    """Hand-maintained patch for very recent news (season-ending injuries, signings)
    that hasn't propagated into nflverse's roster snapshot yet. Edit
    data/manual_status_overrides.csv directly to add/remove entries — this is the
    one part of the pipeline that isn't automated, by necessity: there's no
    structured feed for "this happened three days ago."
    """
    path = DATA_DIR / "manual_status_overrides.csv"
    if not path.exists():
        return pd.DataFrame(columns=["player_name", "action", "team", "note"])
    return pd.read_csv(path)


@st.cache_data(show_spinner=False)
def load_contracts() -> pd.DataFrame:
    """Each player's most recent (is_active) contract, from OverTheCap via nflverse."""
    path = DATA_DIR / "historical_contracts.parquet"
    if path.exists():
        df = pd.read_parquet(path)
    else:
        df = pd.read_parquet(CONTRACTS_URL)
        df.to_parquet(path)

    df = df[df["is_active"] == True].dropna(subset=["gsis_id", "apy_cap_pct", "year_signed", "years"])
    df = df.rename(columns={"gsis_id": "player_id"})
    df["contract_end_year"] = df["year_signed"].astype(int) + df["years"].astype(int) - 1
    return df[["player_id", "position", "year_signed", "years", "apy_cap_pct", "contract_end_year"]]
