"""League scoring: PPR with a TE Premium bonus and a Premium First Downs (PFD) bonus."""

TE_PREMIUM_BONUS_PER_RECEPTION = 0.5  # on top of standard 1 pt/reception PPR
FIRST_DOWN_BONUS = 0.5  # per first down gained, rushing + receiving + passing alike

FIRST_DOWN_COLUMNS = ["passing_first_downs", "rushing_first_downs", "receiving_first_downs"]

SKILL_POSITIONS = ["QB", "RB", "WR", "TE"]


def add_league_points(df):
    """Adds `league_points` (PPR + TE premium + first-down premium), and
    `league_points_per_game` if the data has a `games` column (season-level;
    weekly data is already per-game)."""
    df = df.copy()
    te_bonus = (df["position"] == "TE") * TE_PREMIUM_BONUS_PER_RECEPTION * df["receptions"].fillna(0)

    fd_cols = [c for c in FIRST_DOWN_COLUMNS if c in df.columns]
    fd_bonus = df[fd_cols].fillna(0).sum(axis=1) * FIRST_DOWN_BONUS if fd_cols else 0

    df["league_points"] = df["fantasy_points_ppr"] + te_bonus + fd_bonus
    if "games" in df.columns:
        df["league_points_per_game"] = df["league_points"] / df["games"]
    return df
