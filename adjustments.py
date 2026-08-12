"""Stage 2: weighted adjustments layered on top of the model's raw PPG.

Each factor is a multiplier centered on 1.0 (no effect). A `weight` in [0, 1]
controls how much that factor is allowed to pull the projection away from
1.0 — weight 0 ignores it entirely, weight 1 applies it at full strength.
"""
import pandas as pd

from scoring import SKILL_POSITIONS, add_league_points

GAMES_IN_SEASON = 17


def _clip(series, lo, hi):
    return series.clip(lower=lo, upper=hi)


def _blend(factor: pd.Series, weight: float) -> pd.Series:
    return 1 + weight * (factor - 1)


def team_dynamics_factor(seasonal_all: pd.DataFrame, latest_season: int) -> pd.Series:
    """Is this team's offense trending up or down vs. its own 3-year average?"""
    hist = seasonal_all[seasonal_all["position"].isin(SKILL_POSITIONS) & seasonal_all["season"].between(latest_season - 2, latest_season)]
    team_season_ppg = (hist.groupby(["team", "season"])["league_points"].sum() / GAMES_IN_SEASON).reset_index(name="ppg")

    factors = {}
    for team, g in team_season_ppg.groupby("team"):
        g = g.sort_values("season")
        if len(g) < 2 or g["ppg"].mean() == 0:
            factors[team] = 1.0
            continue
        factors[team] = g.iloc[-1]["ppg"] / g["ppg"].mean()
    return _clip(pd.Series(factors, name="team_dynamics_factor"), 0.85, 1.15)


def schedule_strength_factor(weekly_defense_season: pd.DataFrame, schedule_target: pd.DataFrame) -> pd.DataFrame:
    """Avg fantasy points a team's upcoming opponents allowed to each position, vs league average."""
    wk = weekly_defense_season[weekly_defense_season["position"].isin(SKILL_POSITIONS)]
    allowed = (wk.groupby(["opponent_team", "position"])["league_points"].sum() / GAMES_IN_SEASON).reset_index(name="allowed_ppg")
    league_avg = allowed.groupby("position")["allowed_ppg"].mean()
    allowed_lookup = allowed.set_index(["opponent_team", "position"])["allowed_ppg"]

    home = schedule_target[["home_team", "away_team"]].rename(columns={"home_team": "team", "away_team": "opp"})
    away = schedule_target[["home_team", "away_team"]].rename(columns={"away_team": "team", "home_team": "opp"})
    matchups = pd.concat([home, away], ignore_index=True)

    rows = []
    for team, g in matchups.groupby("team"):
        for position in SKILL_POSITIONS:
            vals = [allowed_lookup.get((opp, position)) for opp in g["opp"]]
            vals = [v for v in vals if v is not None]
            avg = league_avg.get(position)
            if not vals or not avg:
                continue
            rows.append({"team": team, "position": position, "schedule_factor": sum(vals) / len(vals) / avg})

    out = pd.DataFrame(rows)
    out["schedule_factor"] = _clip(out["schedule_factor"], 0.85, 1.15)
    return out


def durability(seasonal_all: pd.DataFrame, latest_season: int) -> pd.DataFrame:
    """Fraction of possible games played over the last up to 2 seasons — proxy for injury history."""
    hist = seasonal_all[seasonal_all["season"].between(latest_season - 1, latest_season)]
    rate = hist.groupby("player_id")["games"].mean() / GAMES_IN_SEASON
    return rate.clip(upper=1.0).rename("durability_rate").reset_index()


def playoff_performance_factor(seasonal_all: pd.DataFrame, playoff_all: pd.DataFrame, latest_season: int) -> pd.DataFrame:
    """Do they perform above or below their regular-season rate in the playoffs? Shrunk toward 1.0."""
    reg = seasonal_all[seasonal_all["season"].between(latest_season - 2, latest_season)][
        ["player_id", "season", "league_points_per_game"]
    ].rename(columns={"league_points_per_game": "reg_ppg"})

    post = playoff_all[playoff_all["position"].isin(SKILL_POSITIONS) & playoff_all["season"].between(latest_season - 2, latest_season)].copy()
    post = add_league_points(post)
    post["post_ppg"] = post["league_points"] / post["games"].replace(0, pd.NA)
    post = post[["player_id", "season", "post_ppg", "games"]].rename(columns={"games": "playoff_games"})

    merged = post.merge(reg, on=["player_id", "season"], how="left").dropna(subset=["reg_ppg", "post_ppg"])
    merged = merged[merged["reg_ppg"] > 0]
    merged["ratio"] = merged["post_ppg"] / merged["reg_ppg"]

    def _agg(g):
        weight = g["playoff_games"].sum()
        raw_ratio = (g["ratio"] * g["playoff_games"]).sum() / weight if weight else 1.0
        shrinkage = min(weight / 6, 1.0)  # fewer playoff games -> pull harder toward 1.0
        return 1 + shrinkage * 0.5 * (raw_ratio - 1)

    factor = merged.groupby("player_id").apply(_agg, include_groups=False).rename("playoff_factor")
    return _clip(factor, 0.9, 1.1).reset_index()


EXCLUDED_STATUSES = {"RET", "CUT"}


def apply_manual_overrides(df: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    """Hand-maintained patches for news too recent to be in the structured data yet."""
    df = df.copy()
    for _, row in overrides.iterrows():
        mask = df["player_name"].str.contains(row["player_name"], case=False, na=False)
        if row["action"] == "exclude":
            df.loc[mask, "exclude"] = True
        elif row["action"] == "include":
            df.loc[mask, "exclude"] = False
            if pd.notna(row.get("team")) and row.get("team"):
                df.loc[mask, "team"] = row["team"]
    return df


def current_roster_status(roster_status_target: pd.DataFrame) -> pd.DataFrame:
    """Current team + availability flag from the target season's live roster (ACT/RES/RET/CUT)."""
    df = roster_status_target[["player_id", "team", "status"]].copy()
    df["exclude"] = df["status"].isin(EXCLUDED_STATUSES)
    df["reserve_games_multiplier"] = (df["status"] == "RES").map({True: 0.5, False: 1.0})
    return df.rename(columns={"team": "current_team"})


def injury_report_risk_factor(injuries_prev_season: pd.DataFrame) -> pd.DataFrame:
    """How often were they on the weekly injury report last season (Questionable/Doubtful/Out)?

    A chronic-niggle signal on top of durability (games played): someone who
    gutted out every game but was flagged most weeks is a bigger risk than
    their games-played number alone suggests.
    """
    flagged = injuries_prev_season[injuries_prev_season["report_status"].isin(["Questionable", "Doubtful", "Out"])]
    weeks_flagged = flagged.groupby("player_id")["week"].nunique().rename("weeks_flagged")
    risk_multiplier = 1 - (weeks_flagged.clip(upper=17) / 17) * 0.15
    return pd.concat([weeks_flagged, risk_multiplier.rename("injury_report_risk_multiplier")], axis=1).reset_index()


# Empirically measured from 2012-2024 contract signings in this app (see chat):
# median % change in points/game from the year before signing to the year after,
# by position and whether the deal was "big" (top 25% of cap share at that position).
# Shrunk by 0.5x below since these are noisy historical medians, not causal estimates.
POST_SIGNING_PCT_CHANGE = {
    ("QB", True): -0.029, ("QB", False): -0.144,
    ("RB", True): -0.112, ("RB", False): -0.240,
    ("WR", True): -0.083, ("WR", False): -0.329,
    ("TE", True): 0.053, ("TE", False): -0.126,
}
POST_SIGNING_SHRINKAGE = 0.5


def contract_situation(contracts_df: pd.DataFrame, target_season: int) -> pd.DataFrame:
    """Flags players who just signed a new deal (with a data-grounded adjustment) or are
    entering a contract year (informational only — our own data showed no reliable
    performance boost for "playing for a new deal", so we don't fabricate one)."""
    df = contracts_df.copy()
    big_threshold = df.groupby("position")["apy_cap_pct"].quantile(0.75).rename("big_threshold")
    df = df.merge(big_threshold, on="position", how="left")
    df["is_big"] = df["apy_cap_pct"] >= df["big_threshold"]

    def _situation(row):
        if row["year_signed"] == target_season:
            return "Just signed (big deal)" if row["is_big"] else "Just signed (modest deal)"
        if row["contract_end_year"] == target_season:
            return "Contract year"
        return None

    df["contract_situation"] = df.apply(_situation, axis=1)

    def _factor(row):
        if row["year_signed"] != target_season:
            return 1.0
        pct_change = POST_SIGNING_PCT_CHANGE.get((row["position"], bool(row["is_big"])), 0.0)
        return 1 + POST_SIGNING_SHRINKAGE * pct_change

    df["contract_factor"] = df.apply(_factor, axis=1)
    return df[["player_id", "contract_situation", "contract_factor"]]


def breakout_potential_factor(seasonal_all: pd.DataFrame, roster_all: pd.DataFrame, latest_season: int) -> pd.DataFrame:
    """Young players whose workload is climbing get a modest boost."""
    cur = seasonal_all[seasonal_all["season"] == latest_season].copy()
    prev = seasonal_all[seasonal_all["season"] == latest_season - 1][["player_id", "targets", "carries", "attempts", "games"]]
    prev = prev.rename(columns={"targets": "targets_prev", "carries": "carries_prev", "attempts": "attempts_prev", "games": "games_prev"})
    cur = cur.merge(prev, on="player_id", how="left")

    for col, prev_col, games_col in [("targets", "targets_prev", "games"), ("carries", "carries_prev", "games"), ("attempts", "attempts_prev", "games")]:
        cur[f"{col}_pg"] = cur[col].fillna(0) / cur["games"]
    cur["usage_pg"] = cur["targets_pg"] + cur["carries_pg"] + cur["attempts_pg"]

    prev_usage = (cur["targets_prev"].fillna(0) + cur["carries_prev"].fillna(0) + cur["attempts_prev"].fillna(0)) / cur["games_prev"].replace(0, pd.NA)
    cur["usage_growth"] = (cur["usage_pg"] - prev_usage.fillna(0)).clip(lower=0)

    ros = roster_all[roster_all["season"] == latest_season][["player_id", "years_exp"]]
    cur = cur.merge(ros, on="player_id", how="left")
    cur["years_exp"] = cur["years_exp"].fillna(3)

    experience_bonus = ((4 - cur["years_exp"]) / 4).clip(lower=0, upper=1)
    usage_growth_bonus = (cur["usage_growth"] / 5).clip(lower=0, upper=1)
    cur["breakout_factor"] = 1 + 0.12 * experience_bonus * usage_growth_bonus

    return cur[["player_id", "breakout_factor"]]


def apply_adjustments(
    raw_df: pd.DataFrame,
    seasonal_all: pd.DataFrame,
    weekly_defense_season: pd.DataFrame,
    schedule_target: pd.DataFrame,
    playoff_all: pd.DataFrame,
    roster_all: pd.DataFrame,
    roster_status_target: pd.DataFrame,
    injuries_prev_season: pd.DataFrame,
    contracts_df: pd.DataFrame,
    manual_overrides: pd.DataFrame,
    latest_season: int,
    target_season: int,
    weights: dict,
) -> pd.DataFrame:
    seasonal_all = add_league_points(seasonal_all[seasonal_all["position"].isin(SKILL_POSITIONS)])
    weekly_defense_season = add_league_points(weekly_defense_season)

    df = raw_df.copy()

    # Drop anyone retired, cut, or not findable on a current 2026 roster at all —
    # a raw model trained on 2025 stats alone would otherwise still "project" them.
    # Also swap in their CURRENT team (a free agent signing/trade since 2025 would
    # otherwise leave team-dynamics and schedule lookups pointing at their old team).
    status = current_roster_status(roster_status_target)
    df = df.merge(status, on="player_id", how="left")
    df["exclude"] = df["exclude"].fillna(True).astype(bool)
    df["team"] = df["current_team"].fillna(df["team"])

    df = apply_manual_overrides(df, manual_overrides)

    df = df[~df["exclude"]].copy()
    df["reserve_games_multiplier"] = df["reserve_games_multiplier"].fillna(1.0)

    team_factor = team_dynamics_factor(seasonal_all, latest_season)
    df = df.merge(team_factor.rename("team_dynamics_factor"), left_on="team", right_index=True, how="left")
    df["team_dynamics_factor"] = df["team_dynamics_factor"].fillna(1.0)

    sched = schedule_strength_factor(weekly_defense_season, schedule_target)
    df = df.merge(sched, on=["team", "position"], how="left")
    df["schedule_factor"] = df["schedule_factor"].fillna(1.0)

    dur = durability(seasonal_all, latest_season)
    df = df.merge(dur, on="player_id", how="left")
    df["durability_rate"] = df["durability_rate"].fillna(0.85)

    injury_report = injury_report_risk_factor(injuries_prev_season)
    df = df.merge(injury_report, on="player_id", how="left")
    df["injury_report_risk_multiplier"] = df["injury_report_risk_multiplier"].fillna(1.0)
    df["weeks_flagged"] = df["weeks_flagged"].fillna(0)

    playoff = playoff_performance_factor(seasonal_all, playoff_all, latest_season)
    df = df.merge(playoff, on="player_id", how="left")
    df["playoff_factor"] = df["playoff_factor"].fillna(1.0)

    breakout = breakout_potential_factor(seasonal_all, roster_all, latest_season)
    df = df.merge(breakout, on="player_id", how="left")
    df["breakout_factor"] = df["breakout_factor"].fillna(1.0)

    contract = contract_situation(contracts_df, target_season)
    df = df.merge(contract, on="player_id", how="left")
    df["contract_factor"] = df["contract_factor"].fillna(1.0)
    df["contract_situation"] = df["contract_situation"].fillna("")

    df["team_dynamics_blend"] = _blend(df["team_dynamics_factor"], weights.get("team_dynamics", 1.0))
    df["schedule_blend"] = _blend(df["schedule_factor"], weights.get("schedule", 1.0))
    df["playoff_blend"] = _blend(df["playoff_factor"], weights.get("playoff", 1.0))
    df["breakout_blend"] = _blend(df["breakout_factor"], weights.get("breakout", 1.0))
    df["contract_blend"] = _blend(df["contract_factor"], weights.get("contract", 1.0))

    df["projected_ppg"] = (
        df["raw_projected_ppg"] * df["team_dynamics_blend"] * df["schedule_blend"]
        * df["playoff_blend"] * df["breakout_blend"] * df["contract_blend"]
    )

    injury_weight = weights.get("injury", 1.0)
    effective_durability = 1 - injury_weight * (1 - df["durability_rate"])
    effective_durability = effective_durability * _blend(df["injury_report_risk_multiplier"], injury_weight)
    effective_durability = effective_durability * _blend(df["reserve_games_multiplier"], injury_weight)
    df["projected_games"] = (GAMES_IN_SEASON * effective_durability).clip(lower=0, upper=GAMES_IN_SEASON)

    df["projected_total"] = df["projected_ppg"] * df["projected_games"]
    return df.sort_values("projected_total", ascending=False).reset_index(drop=True)
