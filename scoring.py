"""League scoring: PPR with a TE Premium bonus."""

TE_PREMIUM_BONUS_PER_RECEPTION = 0.5  # on top of standard 1 pt/reception PPR

SKILL_POSITIONS = ["QB", "RB", "WR", "TE"]


def add_league_points(df):
    """Adds `league_points` (PPR + TE premium), and `league_points_per_game` if
    the data has a `games` column (season-level; weekly data is already per-game)."""
    df = df.copy()
    te_bonus = (df["position"] == "TE") * TE_PREMIUM_BONUS_PER_RECEPTION * df["receptions"].fillna(0)
    df["league_points"] = df["fantasy_points_ppr"] + te_bonus
    if "games" in df.columns:
        df["league_points_per_game"] = df["league_points"] / df["games"]
    return df
