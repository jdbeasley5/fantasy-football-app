"""Stage 1: a trained model that predicts next-season PPG from a player's
own historical performance, usage, and biographical data — no team or
schedule context here, that's layered on in stage 2 (see adjustments.py).

We compare Ridge, Lasso, and Random Forest on a time-based holdout (train on
earlier cohorts, test on the most recent one) and always ship Random Forest
in production, even when a linear model tests slightly lower on MSE — trees
handle the non-linear age curves and usage-role thresholds in this data
(e.g. a starter-vs-backup carries cliff) better than a linear fit, which
matters more for robustness than shaving a bit off average error.
"""
import numpy as np
import pandas as pd
import streamlit as st
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Lasso, Ridge
from sklearn.metrics import mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from scoring import SKILL_POSITIONS, add_league_points

COUNTING_STATS = [
    "attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions",
    "passing_air_yards", "passing_yards_after_catch", "passing_first_downs",
    "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "receiving_yards_after_catch", "receiving_first_downs",
]
RATE_STATS = ["passing_epa", "rushing_epa", "receiving_epa", "passing_cpoe", "target_share", "air_yards_share", "wopr"]
BIO_FEATURES = ["age", "years_exp", "draft_number"]
OWN_HISTORY_FEATURES = ["league_points_per_game", "league_points_per_game_prev", "games"]

FEATURE_COLUMNS = [f"{c}_pg" for c in COUNTING_STATS] + RATE_STATS + BIO_FEATURES + OWN_HISTORY_FEATURES


def build_features_for_cohort(seasonal_all: pd.DataFrame, roster_all: pd.DataFrame, cohort_season: int) -> pd.DataFrame:
    """Features describing each player as of `cohort_season`, used to predict cohort_season + 1."""
    cur = seasonal_all[seasonal_all["season"] == cohort_season].copy()

    prev = seasonal_all[seasonal_all["season"] == cohort_season - 1][["player_id", "league_points_per_game"]]
    prev = prev.rename(columns={"league_points_per_game": "league_points_per_game_prev"})
    cur = cur.merge(prev, on="player_id", how="left")
    cur["league_points_per_game_prev"] = cur["league_points_per_game_prev"].fillna(cur["league_points_per_game"])

    ros = roster_all[roster_all["season"] == cohort_season][["player_id", "age", "years_exp", "draft_number"]]
    cur = cur.merge(ros, on="player_id", how="left")
    cur["age"] = cur["age"].fillna(cur["age"].median())
    cur["years_exp"] = cur["years_exp"].fillna(0)
    cur["draft_number"] = cur["draft_number"].fillna(300)

    cur = cur.dropna(subset=["games"])
    for c in COUNTING_STATS:
        cur[f"{c}_pg"] = cur[c].fillna(0) / cur["games"]
    for c in RATE_STATS:
        cur[c] = cur[c].fillna(0)

    keep = ["player_id", "player_name", "position", "team", "season"] + FEATURE_COLUMNS
    out = cur[keep].copy()
    out["cohort_season"] = cohort_season
    return out


def _design_matrix(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURE_COLUMNS].fillna(0)
    dummies = pd.get_dummies(df["position"], prefix="pos")
    for pos in SKILL_POSITIONS:
        if f"pos_{pos}" not in dummies.columns:
            dummies[f"pos_{pos}"] = 0
    return pd.concat([X.reset_index(drop=True), dummies.reset_index(drop=True)], axis=1)


@st.cache_resource(show_spinner=False)
def train_model(seasonal_all: pd.DataFrame, roster_all: pd.DataFrame, train_cohort_seasons: tuple[int, ...]):
    seasonal_all = add_league_points(seasonal_all[seasonal_all["position"].isin(SKILL_POSITIONS)])

    rows = []
    for cohort in train_cohort_seasons:
        feats = build_features_for_cohort(seasonal_all, roster_all, cohort)
        label = seasonal_all[seasonal_all["season"] == cohort + 1][["player_id", "league_points_per_game"]]
        label = label.rename(columns={"league_points_per_game": "label_ppg"})
        rows.append(feats.merge(label, on="player_id", how="inner"))
    training_df = pd.concat(rows, ignore_index=True)
    training_df = training_df[training_df["label_ppg"].notna()]

    latest_cohort = max(train_cohort_seasons)
    train_df = training_df[training_df["cohort_season"] < latest_cohort]
    test_df = training_df[training_df["cohort_season"] == latest_cohort]

    X_train, y_train = _design_matrix(train_df), train_df["label_ppg"]
    X_test, y_test = _design_matrix(test_df), test_df["label_ppg"]

    candidates = {
        "Ridge": Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=5.0))]),
        "Lasso": Pipeline([("scale", StandardScaler()), ("model", Lasso(alpha=0.1, max_iter=5000))]),
        "Random Forest": RandomForestRegressor(n_estimators=300, max_depth=8, min_samples_leaf=5, random_state=42),
    }
    comparison = {}
    for name, mdl in candidates.items():
        mdl.fit(X_train, y_train)
        comparison[name] = mean_squared_error(y_test, mdl.predict(X_test))

    X_full, y_full = _design_matrix(training_df), training_df["label_ppg"]
    final_model = RandomForestRegressor(n_estimators=300, max_depth=8, min_samples_leaf=5, random_state=42)
    final_model.fit(X_full, y_full)

    importances = pd.Series(final_model.feature_importances_, index=X_full.columns).sort_values(ascending=False)

    return {
        "model": final_model,
        "mse_comparison": comparison,
        "feature_importances": importances,
        "design_columns": X_full.columns.tolist(),
        "n_training_rows": len(training_df),
        "train_cohorts": train_cohort_seasons,
    }


def predict_raw_ppg(trained, seasonal_all: pd.DataFrame, roster_all: pd.DataFrame, latest_season: int) -> pd.DataFrame:
    seasonal_all = add_league_points(seasonal_all[seasonal_all["position"].isin(SKILL_POSITIONS)])
    feats = build_features_for_cohort(seasonal_all, roster_all, latest_season)

    X = _design_matrix(feats)
    X = X.reindex(columns=trained["design_columns"], fill_value=0)

    feats = feats.copy()
    feats["raw_projected_ppg"] = np.clip(trained["model"].predict(X), 0, None)
    return feats
