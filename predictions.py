"""Orchestrates the two-stage projection: a trained Random Forest baseline
(model.py) plus weighted stage-2 adjustments (adjustments.py).
"""
import pandas as pd

from adjustments import apply_adjustments
from model import predict_raw_ppg, train_model
from scoring import SKILL_POSITIONS

DEFAULT_WEIGHTS = {"team_dynamics": 1.0, "schedule": 1.0, "injury": 1.0, "playoff": 1.0, "breakout": 1.0, "contract": 1.0}


def build_projections(
    seasonal_all: pd.DataFrame,
    roster_all: pd.DataFrame,
    weekly_defense_season: pd.DataFrame,
    schedule_target: pd.DataFrame,
    playoff_all: pd.DataFrame,
    roster_status_target: pd.DataFrame,
    injuries_prev_season: pd.DataFrame,
    contracts_df: pd.DataFrame,
    manual_overrides: pd.DataFrame,
    latest_season: int,
    target_season: int,
    train_cohort_seasons: tuple[int, ...],
    weights: dict = DEFAULT_WEIGHTS,
):
    trained = train_model(seasonal_all, roster_all, train_cohort_seasons)
    raw = predict_raw_ppg(trained, seasonal_all, roster_all, latest_season)

    proj = apply_adjustments(
        raw_df=raw,
        seasonal_all=seasonal_all,
        weekly_defense_season=weekly_defense_season,
        schedule_target=schedule_target,
        playoff_all=playoff_all,
        roster_all=roster_all,
        roster_status_target=roster_status_target,
        injuries_prev_season=injuries_prev_season,
        contracts_df=contracts_df,
        manual_overrides=manual_overrides,
        latest_season=latest_season,
        target_season=target_season,
        weights=weights,
    )
    return proj, trained


# Rough flex-eligibility split for a standard 1QB/2RB/2WR/1TE/1FLEX lineup —
# most flex starts are RBs or WRs; TEs get a small bump here since this app
# assumes TE Premium scoring, which pushes more TEs into flex consideration.
FLEX_SHARE = {"QB": 0.0, "RB": 0.5, "WR": 0.4, "TE": 0.1}
BASE_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}


def add_draft_value(proj: pd.DataFrame, num_teams: int = 12) -> pd.DataFrame:
    proj = proj.copy()
    proj["replacement_points"] = 0.0
    for position in SKILL_POSITIONS:
        pos_df = proj[proj["position"] == position].sort_values("projected_total", ascending=False)
        rank = round(num_teams * (BASE_STARTERS[position] + FLEX_SHARE[position]))
        rank = min(rank, len(pos_df)) or 1
        replacement_points = pos_df.iloc[rank - 1]["projected_total"] if len(pos_df) else 0.0
        proj.loc[proj["position"] == position, "replacement_points"] = replacement_points

    proj["draft_value"] = proj["projected_total"] - proj["replacement_points"]
    return proj.sort_values("draft_value", ascending=False).reset_index(drop=True)
