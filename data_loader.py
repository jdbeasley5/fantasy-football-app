"""Fetches and caches NFL stats + schedule data from nflverse.

We pull directly from the nflverse-data GitHub releases rather than going
through nfl_data_py: that package pins an older, now-stale release tag
("player_stats") that nflverse stopped publishing per-year files for after
2024. "stats_player" (season + week granularity) and "schedules" are the
actively maintained sources, and already ship names/positions/opponents/
fantasy points baked in.
"""
import io
import os
import time
from pathlib import Path
from urllib.error import HTTPError

import certifi
import pandas as pd
import requests
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


def _url_exists(url: str, retries: int = 3, timeout: int = 10) -> bool | None:
    """True/False for a definitive answer (200/206 vs 404), or None if every retry
    hit something else (timeout, 5xx, rate limit) -- an inconclusive network issue,
    not evidence the file is missing.

    Uses a 1-byte ranged GET rather than HEAD: GitHub's release CDN was
    intermittently flaky specifically on HEAD requests through its redirect
    chain, while plain GETs (confirmed against the same URLs via curl) were
    consistently reliable. A ranged GET keeps this cheap without downloading
    the whole file just to check existence.
    """
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout, allow_redirects=True, headers={"Range": "bytes=0-0", "Connection": "close"})
            if resp.status_code in (200, 206):
                return True
            if resp.status_code == 404:
                return False
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(0.5 * (attempt + 1))
    return None


@st.cache_data(show_spinner=False)
def find_latest_available_season(probe_from: int = 2027, floor: int = 2015) -> int:
    """Probes nflverse for the newest season that actually has a published stats file.

    Uses a lightweight HEAD request rather than downloading the file, and
    distinguishes a real 404 (season not published yet -> try the previous year)
    from a transient network error. A prior version treated any exception as
    "doesn't exist" and silently fell back to `floor` -- which then got cached by
    st.cache_data and stuck for the rest of the process's life, poisoning every
    downstream fetch with the wrong year range. Raising here instead means a
    transient failure surfaces as a visible, retryable error rather than a wrong
    answer that looks like it succeeded.
    """
    for year in range(probe_from, floor, -1):
        exists = _url_exists(SEASON_STATS_URL.format(year=year))
        if exists:
            return year
        if exists is False:
            continue
        raise RuntimeError(f"Could not tell whether the {year} season is published (network issue after retries) — try again.")
    return floor


def default_years(n_years: int = 5) -> list[int]:
    latest = find_latest_available_season()
    return list(range(latest - n_years + 1, latest + 1))


def _read_remote_parquet_with_retry(url: str, retries: int = 3) -> pd.DataFrame | None:
    """Fetches a parquet file over HTTP, retrying transient failures.

    Downloads the whole file as plain bytes via `requests` first, then parses
    it from memory -- rather than letting `pd.read_parquet(url)` manage the
    remote read itself. That path goes through pyarrow's HTTP filesystem,
    which issues several range requests per file (footer, then columns);
    GitHub's release CDN was intermittently returning 503s / dropped
    connections for that multi-request pattern while a single plain `curl`
    download of the same URL succeeded every time. One simple GET matches the
    access pattern that's actually reliable against this CDN.

    A 404 means the file genuinely doesn't exist (e.g. a season not published
    yet) -- no point retrying that. Anything else (503, 500, 429, timeouts,
    connection resets) is treated as transient and retried with backoff.
    """
    last_error = None
    for attempt in range(retries):
        try:
            # Connection: close -- this app makes many sequential requests to the
            # same host in one run (one per season/year across several loaders).
            # requests/urllib3 pools and reuses connections by default, and GitHub's
            # CDN was intermittently dropping reused connections partway through a
            # long sequential chain ("remote end closed connection without
            # response") on requests that landed later in the sequence, even though
            # the same URLs succeeded reliably in isolation. Forcing a fresh
            # connection per request avoids reusing one the server may have decided
            # to recycle.
            resp = requests.get(url, timeout=30, headers={"Connection": "close"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return pd.read_parquet(io.BytesIO(resp.content))
        except requests.HTTPError as e:
            last_error = e
        except (requests.RequestException, OSError) as e:
            last_error = e
        if attempt < retries - 1:
            time.sleep(0.5 * (attempt + 1))
    st.warning(f"Network error fetching {url} after {retries} attempts: {last_error}")
    return None


def _fetch(url_template: str, cache_prefix: str, year: int, retries: int = 3) -> pd.DataFrame | None:
    path = DATA_DIR / f"{cache_prefix}_{year}.parquet"
    if path.exists():
        return pd.read_parquet(path)

    df = _read_remote_parquet_with_retry(url_template.format(year=year), retries=retries)
    if df is not None:
        df.to_parquet(path)
    return df


def _rename_common(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={"player_display_name": "player_name", "player_name": "player_short_name"})


# The history tabs (rankings/trends/deep-dive) only ever display these columns —
# selecting down to them right after load keeps the *cached* copy small. The
# draft-prediction path (model.py) needs the full ~140-column file and requests it
# by passing columns=None.
HISTORY_TAB_COLUMNS = (
    "player_id", "player_name", "position", "team", "season", "games",
    "fantasy_points_ppr", "receptions", "targets",
    "rushing_yards", "receiving_yards", "passing_yards",
)


@st.cache_data(show_spinner=False)
def load_seasonal_stats(years: tuple[int, ...], columns: tuple[str, ...] | None = None) -> pd.DataFrame:
    """One row per player per season. Pass `columns` (post-rename names) to cache
    a trimmed-down copy instead of all ~140 columns — see HISTORY_TAB_COLUMNS."""
    by_year = {y: _fetch(SEASON_STATS_URL, "stats_player_reg", y) for y in years}

    missing = [y for y in years if by_year[y] is None]
    if missing:
        st.warning(f"No published nflverse data yet for season(s): {missing}. Skipping them.")

    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    df = _rename_common(df).rename(columns={"recent_team": "team"})
    df["games"] = df["games"].replace(0, pd.NA)
    df = df.dropna(subset=["player_name", "position"])

    if columns is not None:
        df = df[[c for c in columns if c in df.columns]].copy()
    return df


@st.cache_data(show_spinner=False)
def load_weekly_stats(years: tuple[int, ...]) -> pd.DataFrame:
    """One row per player per game, with opponent_team — needed for defense-vs-position."""
    by_year = {y: _fetch(WEEK_STATS_URL, "stats_player_week", y) for y in years}
    df = pd.concat([v for v in by_year.values() if v is not None], ignore_index=True)
    return _rename_common(df).dropna(subset=["player_name", "position", "opponent_team"])


@st.cache_data(show_spinner=False)
def load_schedule(season: int) -> pd.DataFrame:
    df = _read_remote_parquet_with_retry(SCHEDULE_URL)
    if df is None:
        raise RuntimeError("Could not fetch the schedule file after retries (network issue) — try again.")
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
        df = _read_remote_parquet_with_retry(CONTRACTS_URL)
        if df is None:
            raise RuntimeError("Could not fetch contracts data after retries (network issue) — try again.")
        df.to_parquet(path)

    df = df[df["is_active"] == True].dropna(subset=["gsis_id", "apy_cap_pct", "year_signed", "years"])
    df = df.rename(columns={"gsis_id": "player_id"})
    df["contract_end_year"] = df["year_signed"].astype(int) + df["years"].astype(int) - 1
    return df[["player_id", "position", "year_signed", "years", "apy_cap_pct", "contract_end_year"]]
