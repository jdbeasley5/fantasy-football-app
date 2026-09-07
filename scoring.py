"""League scoring: this league's actual custom rules, layered on top of
nflverse's `fantasy_points_ppr`.

Verified against real player rows that `fantasy_points_ppr` is exactly
standard PPR: 0.04/passing yard, 4/passing TD, -2/INT, 0.1/rush yard,
6/rush TD, 0.1/rec yard, 6/rec TD, +1/reception, -2/fumble lost, +2/2pt
conversion. This league differs from that baseline in a few places, so we
add the deltas on top rather than recomputing from scratch:
  - Passing TD is worth +5 here, not the standard +4 -> +1/passing TD.
  - An intercepted pass only costs -1 here, not the standard -2 -> +1/INT
    (a refund against the -2 already baked into fantasy_points_ppr).
  - First downs earn a bonus: +0.25 passing, +0.50 rushing, +0.50 receiving.
  - TEs get a full extra point per reception (2 pts/catch total with
    standard PPR), not just a half-point bonus.

NOT modeled here (this app has no access to play-by-play or per-game
splits, only season/week totals): the league's 40+/50+ yard big-play
bonuses, and its 100/200-yard single-game bonuses. Those add a real but
modest number of points/season for boom-type players -- a fast WR who
routinely breaks 40+ yard catches, a bell-cow back with several 100-yard
games -- that this model doesn't capture. Kicker and Team/Special Teams
Defense scoring aren't modeled at all; this app only ranks QB/RB/WR/TE.
"""

PASSING_TD_BONUS = 1.0  # league pays +5/passing TD vs. standard PPR's +4
INTERCEPTION_REFUND = 1.0  # league docks only -1/INT vs. standard PPR's -2

TE_PREMIUM_BONUS_PER_RECEPTION = 1.0  # on top of standard 1 pt/reception PPR

FIRST_DOWN_BONUS = {
    "passing_first_downs": 0.25,
    "rushing_first_downs": 0.50,
    "receiving_first_downs": 0.50,
}

SKILL_POSITIONS = ["QB", "RB", "WR", "TE"]


def add_league_points(df):
    """Adds `league_points` (PPR + this league's real bonuses/adjustments), and
    `league_points_per_game` if the data has a `games` column (season-level;
    weekly data is already per-game)."""
    df = df.copy()
    te_bonus = (df["position"] == "TE") * TE_PREMIUM_BONUS_PER_RECEPTION * df["receptions"].fillna(0)

    fd_bonus = 0
    for col, bonus in FIRST_DOWN_BONUS.items():
        if col in df.columns:
            fd_bonus = fd_bonus + df[col].fillna(0) * bonus

    td_bonus = df["passing_tds"].fillna(0) * PASSING_TD_BONUS if "passing_tds" in df.columns else 0
    int_refund = df["passing_interceptions"].fillna(0) * INTERCEPTION_REFUND if "passing_interceptions" in df.columns else 0

    df["league_points"] = df["fantasy_points_ppr"] + te_bonus + fd_bonus + td_bonus + int_refund
    if "games" in df.columns:
        df["league_points_per_game"] = df["league_points"] / df["games"]
    return df
