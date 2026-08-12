import pandas as pd
import plotly.express as px
import streamlit as st

from data_loader import HISTORY_TAB_COLUMNS, default_years, find_latest_available_season, load_contracts, load_injuries, load_manual_overrides, load_playoff_stats, load_roster, load_roster_status, load_schedule, load_seasonal_stats, load_weekly_stats
from scoring import SKILL_POSITIONS, add_league_points

# predictions.py pulls in scikit-learn/scipy, which carry real import-time memory
# overhead. Deferring the import until the Draft Prep button is actually clicked
# means that cost is never paid at all for a session that doesn't use it — this
# matters on memory-constrained deployments (e.g. Streamlit Community Cloud's 1GB
# limit), where the crash traced back to this being imported unconditionally.

TRAIN_START_SEASON = 2012

st.set_page_config(page_title="Fantasy Football Analytics", layout="wide", page_icon="🏈")

CHART_FONT = dict(size=15)
COLOR_SEQUENCE = px.colors.qualitative.Bold

st.title("🏈 Fantasy Football Analytics")
st.caption("PPR + TE Premium scoring — 5-year performance history and 2026 draft projections, powered by nflverse data")

with st.sidebar:
    st.header("Settings")
    n_years = st.slider("Seasons to include (history tabs)", min_value=1, max_value=5, value=3)
    years = default_years(n_years)
    st.caption(f"Seasons: {years[0]}–{years[-1]}")

    min_games = st.slider("Minimum games played (filters out injured/backup noise)", 0, 17, 6)

    top_n_startable = st.slider(
        "Startable tier size per position (for position-strength ranking)",
        min_value=6, max_value=48, value=24, step=6,
    )

    st.divider()
    st.subheader("Draft board settings")
    num_teams = st.number_input("Number of teams in your league", min_value=4, max_value=20, value=12)

    st.caption("Stage-2 adjustment weights — how much each factor can move a player off the model's raw baseline.")
    weights = {
        "team_dynamics": st.slider("Team dynamics", 0.0, 1.0, 1.0, 0.1),
        "schedule": st.slider("Schedule strength", 0.0, 1.0, 1.0, 0.1),
        "injury": st.slider("Injury history", 0.0, 1.0, 1.0, 0.1),
        "playoff": st.slider("Playoff performance", 0.0, 1.0, 1.0, 0.1),
        "breakout": st.slider("Breakout potential", 0.0, 1.0, 1.0, 0.1),
        "contract": st.slider("Contract situation", 0.0, 1.0, 1.0, 0.1),
    }

with st.spinner("Loading NFL data (first run downloads and caches locally)..."):
    df = load_seasonal_stats(tuple(years), columns=HISTORY_TAB_COLUMNS)
    df = add_league_points(df)

df = df[df["position"].isin(SKILL_POSITIONS)]
df = df[df["games"].fillna(0) >= min_games]

tab_rank, tab_trends, tab_player, tab_predict = st.tabs(
    ["📊 Position & Player Rankings", "📈 5-Year Trends", "🔍 Player Deep Dive", "🔮 2026 Draft Prep"]
)

# ---------------------------------------------------------------------------
with tab_rank:
    st.subheader("Which position scores the most, right now?")
    st.caption(
        f"Average PPR (+ TE Premium) points/game for each position's top {top_n_startable} "
        "players — reflects the players who'd actually be starting in a fantasy lineup."
    )

    strength_rows = []
    for season in years:
        season_df = df[df["season"] == season]
        for pos in SKILL_POSITIONS:
            pos_df = season_df[season_df["position"] == pos].nlargest(top_n_startable, "league_points")
            if len(pos_df):
                strength_rows.append(
                    {"season": season, "position": pos, "avg_ppg": pos_df["league_points_per_game"].mean()}
                )
    strength_df = pd.DataFrame(strength_rows)

    fig = px.line(
        strength_df, x="season", y="avg_ppg", color="position", markers=True, text="avg_ppg",
        labels={"avg_ppg": "Avg pts/game", "season": "Season"},
        color_discrete_sequence=COLOR_SEQUENCE,
    )
    fig.update_traces(line=dict(width=3), marker=dict(size=9), texttemplate="%{text:.1f}", textposition="top center")
    fig.update_layout(height=450, font=CHART_FONT, legend_title_text="Position")
    st.plotly_chart(fig, use_container_width=True)

    latest_season = years[-1]
    latest_rank = (
        strength_df[strength_df["season"] == latest_season]
        .sort_values("avg_ppg", ascending=False)
        .reset_index(drop=True)
    )
    latest_rank.index += 1
    st.markdown(f"**Position ranking, {latest_season} season:**")
    st.dataframe(
        latest_rank.rename(columns={"avg_ppg": "Avg pts/game", "position": "Position", "season": "Season"}),
        use_container_width=True,
    )

    st.divider()
    st.subheader("Top players by position")
    col1, col2 = st.columns([1, 1])
    with col1:
        rank_pos = st.selectbox("Position", SKILL_POSITIONS)
    with col2:
        rank_season = st.selectbox("Season", list(reversed(years)), key="rank_season")

    top_players = (
        df[(df["position"] == rank_pos) & (df["season"] == rank_season)]
        .nlargest(25, "league_points")[["player_name", "team", "league_points", "league_points_per_game", "games"]]
        .reset_index(drop=True)
    )
    top_players.index += 1
    top_players.columns = ["Player", "Team", "Total pts", "Pts/game", "Games"]
    st.dataframe(top_players, use_container_width=True)

# ---------------------------------------------------------------------------
with tab_trends:
    st.subheader(f"Player trends across the last {n_years} seasons")
    st.caption("Pick a handful of players to compare — fewer lines stay readable.")

    pos_for_trend = st.selectbox("Position", SKILL_POSITIONS, key="trend_pos")
    pos_df = df[df["position"] == pos_for_trend]

    top_players_overall = (
        pos_df.groupby("player_name")["league_points"].sum().nlargest(6).index.tolist()
    )
    chosen_players = st.multiselect(
        "Players to chart (defaults to the top 6 by total points over the window)",
        sorted(pos_df["player_name"].unique()),
        default=top_players_overall,
        max_selections=8,
    )

    if chosen_players:
        trend_df = pos_df[pos_df["player_name"].isin(chosen_players)].sort_values("season")
        fig2 = px.line(
            trend_df, x="season", y="league_points_per_game", color="player_name",
            markers=True, labels={"league_points_per_game": "Pts/game", "season": "Season", "player_name": "Player"},
            color_discrete_sequence=COLOR_SEQUENCE,
        )
        fig2.update_traces(line=dict(width=3), marker=dict(size=9))
        fig2.update_layout(height=500, font=CHART_FONT, legend_title_text="Player")
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Pick at least one player to see their trend line.")

# ---------------------------------------------------------------------------
with tab_player:
    st.subheader("Player deep dive")
    all_players = sorted(df["player_name"].unique())
    player = st.selectbox("Search for a player", all_players)

    player_df = df[df["player_name"] == player].sort_values("season")
    if len(player_df):
        latest = player_df.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Position", latest["position"])
        c2.metric("Team", latest["team"])
        c3.metric(f"{latest['season']} total pts", f"{latest['league_points']:.1f}")
        c4.metric(f"{latest['season']} pts/game", f"{latest['league_points_per_game']:.1f}")

        fig3 = px.bar(
            player_df, x="season", y="league_points", text="league_points",
            labels={"league_points": "Total pts", "season": "Season"},
        )
        fig3.update_traces(texttemplate="%{text:.0f}", textposition="outside")
        fig3.update_layout(height=400, font=CHART_FONT)
        st.plotly_chart(fig3, use_container_width=True)

        display_cols = ["season", "team", "games", "league_points", "league_points_per_game", "targets", "receptions", "rushing_yards", "receiving_yards", "passing_yards"]
        display_cols = [c for c in display_cols if c in player_df.columns]
        st.dataframe(player_df[display_cols].reset_index(drop=True), use_container_width=True)

# ---------------------------------------------------------------------------
with tab_predict:
    latest_season = find_latest_available_season()
    target_season = latest_season + 1

    st.subheader(f"{target_season} Draft Board")
    st.caption(
        f"Two-stage projection: a trained model predicts each player's raw {target_season} pace from "
        f"their own history, then six weighted factors (sidebar) — including real injury reports, current "
        f"roster status, and contract data — adjust it into a final draft-value ranking for a {num_teams}-team league."
    )

    with st.expander("How this projection works (and what it can't see)"):
        st.markdown(
            f"""
            **Stage 1 — baseline model.** A Random Forest trained on every skill-position player-season
            back to {TRAIN_START_SEASON}, predicting next-season points/game from that player's own
            performance, usage, efficiency, age, experience, and draft capital. We also test Ridge and
            Lasso on the same time-based holdout (most recent cohort held out) — see the model comparison
            below. Random Forest ships to production even when a linear model tests close on MSE: trees
            handle non-linear effects (age curves, usage-role cliffs) more robustly.

            **Stage 2 — weighted adjustments**, each a multiplier centered on 1.0, tunable in the sidebar:
            - **Team dynamics** — is this player's team's offense trending up or down vs. its own 3-year average?
            - **Schedule strength** — how many fantasy points this player's {target_season} opponents allowed to
              their position in {target_season - 1}, vs. the league average.
            - **Injury history** — three real signals combined: % of possible games played over the last 2
              seasons, how many weeks they were on the official injury report as Questionable/Doubtful/Out in
              {target_season - 1}, and their live {target_season} roster status (a Reserve/PUP/IR designation
              right now cuts expected games in half). All three reduce expected games played, not rate.
            - **Playoff performance** — whether they historically over- or under-perform their regular-season
              rate in the playoffs, shrunk toward neutral for small sample sizes.
            - **Breakout potential** — a boost for players early in their career whose workload is climbing.
            - **Contract situation** — uses real contract data (OverTheCap via nflverse). Players who just
              signed a new deal get an adjustment sized from what we actually measured across 2012–2024
              signings: big deals (top 25% of cap share at their position) barely decline the next year,
              while modest deals decline noticeably more — the opposite of the "get paid, get worse" myth.
              Players in a contract year are labeled but NOT adjusted — our own data showed no reliable
              performance boost for "playing for a new deal", so we don't fabricate one.

            **Also filtered out:** anyone retired, released, or not found on a current {target_season} roster
            at all — a model trained only on {target_season - 1} stats would otherwise still project them.

            **Draft value** — projected total points minus a replacement-level player at that position
            (standard 1 QB / 2 RB / 2 WR / 1 TE / 1 FLEX roster math).

            **Caveats:** still can't see {target_season} trades that swap a player's role (only their team),
            coaching changes, holdouts, or brand-new injuries after this data was pulled. True rookies with
            zero prior NFL seasons can't be projected — they won't appear below.
            """
        )

    manual_overrides_preview = load_manual_overrides()
    with st.expander(f"⚠️ Recent injury/news watch — {latest_season + 1} training camp", expanded=True):
        st.caption(
            "Structured league data (rosters, injury reports) lags real news by days — a season-ending "
            "injury can still show 'Active' until the official transaction is filed, and a very recent "
            "signing may not appear on any roster file yet. These are hand-checked against current news."
        )
        if len(manual_overrides_preview):
            st.markdown("**Applied to the board below:**")
            for _, r in manual_overrides_preview.iterrows():
                icon = "🚫" if r["action"] == "exclude" else "✅"
                st.markdown(f"{icon} **{r['player_name']}** — {r['note']}")
        st.markdown(
            """
            **Being watched, not yet overridden** (day-to-day or too uncertain to hard-code a magnitude):
            - Zay Flowers (BAL) — quad contusion, day-to-day
            - Christian Kirk (SF) — calf strain, no return date yet
            - Luther Burden III (CHI) — groin injury, expected to miss some time
            - Tucker Kraft (GB) & Malik Nabers (NYG) — both recovering from 2025 ACL tears; the model
              already discounts their expected games for it, but both are reportedly trending toward a
              full Week 1 return, which the automated discount doesn't know yet
            """
        )
        st.caption(f"Edit `data/manual_status_overrides.csv` to add or remove hard overrides as news breaks.")

    # Streamlit executes every tab's code on every rerun regardless of which tab is
    # visible — without this gate, the app would train the model and download 13+
    # years of historical data on every single page load, even for someone who never
    # opens this tab. That's slow locally and can exceed free-tier resource limits
    # when deployed.
    if not st.session_state.get("draft_board_built", False):
        st.info("Building the draft board trains a model and pulls 13+ years of historical data — takes a little while.")
        if st.button("🔮 Build 2026 Draft Board", type="primary"):
            st.session_state.draft_board_built = True
            st.rerun()
    else:
        from predictions import add_draft_value, build_projections

        train_cohorts = tuple(range(TRAIN_START_SEASON, latest_season))
        with st.spinner("Training model and building projections (first run takes a bit longer)..."):
            seasonal_all = load_seasonal_stats(tuple(range(TRAIN_START_SEASON - 1, latest_season + 1)))
            roster_all = load_roster(tuple(range(TRAIN_START_SEASON, latest_season + 1)))
            weekly_defense = load_weekly_stats((latest_season,))
            schedule_target = load_schedule(target_season)
            playoff_all = load_playoff_stats(tuple(range(latest_season - 2, latest_season + 1)))
            roster_status_target = load_roster_status(target_season)
            injuries_prev_season = load_injuries((latest_season,))
            contracts_df = load_contracts()
            manual_overrides = load_manual_overrides()

            proj, trained = build_projections(
                seasonal_all, roster_all, weekly_defense, schedule_target, playoff_all,
                roster_status_target, injuries_prev_season, contracts_df, manual_overrides,
                latest_season, target_season, train_cohorts, weights,
            )

        if proj.empty:
            st.warning("Not enough historical data yet to build projections.")
        else:
            with st.expander("Model comparison (test-set MSE, lower is better) & top predictive features"):
                mse_df = pd.DataFrame(trained["mse_comparison"].items(), columns=["Model", "Test MSE"]).sort_values("Test MSE")
                mse_df["In production?"] = mse_df["Model"].map(lambda m: "✅ Random Forest" if m == "Random Forest" else "")
                c1, c2 = st.columns(2)
                with c1:
                    st.dataframe(mse_df.round(3), use_container_width=True, hide_index=True)
                    st.caption(f"Trained on {trained['n_training_rows']:,} player-seasons, cohorts {min(train_cohorts)}–{max(train_cohorts)}.")
                with c2:
                    importances = trained["feature_importances"].head(8).sort_values()
                    fig_imp = px.bar(importances, orientation="h", labels={"value": "Importance", "index": ""})
                    fig_imp.update_layout(height=300, font=CHART_FONT, showlegend=False)
                    st.plotly_chart(fig_imp, use_container_width=True)

            proj = add_draft_value(proj, num_teams=num_teams)

            positions_filter = st.multiselect("Positions", SKILL_POSITIONS, default=SKILL_POSITIONS)
            board = proj[proj["position"].isin(positions_filter)].head(100).reset_index(drop=True)
            board.index += 1

            display_cols = ["player_name", "position", "team", "raw_projected_ppg", "team_dynamics_factor", "schedule_factor", "durability_rate", "weeks_flagged", "playoff_factor", "breakout_factor", "contract_situation", "projected_total", "projected_ppg", "draft_value"]
            display = board[display_cols].copy()
            display.columns = ["Player", "Pos", "Team", "Raw pts/game", "Team trend", "Schedule", "Durability", "Wks on report", "Playoff", "Breakout", "Contract situation", "Proj. total", "Proj. pts/game", "Draft value"]
            factor_cols = ["Team trend", "Schedule", "Durability", "Playoff", "Breakout"]
            for col in display.columns:
                if col not in ("Player", "Pos", "Team", "Wks on report", "Contract situation"):
                    display[col] = display[col].round(3 if col in factor_cols else 2)

            st.dataframe(display, use_container_width=True, height=600)
            st.caption(
                "'Team trend' through 'Breakout' are the stage-2 multipliers (1.00 = no effect). "
                "'Wks on report' = weeks flagged Questionable/Doubtful/Out last season. "
                "'Contract situation' is blank for most players (only shown for those with a tracked contract event)."
            )

            st.divider()
            st.subheader("By position")
            st.caption("Same board, split out by position for position-specific draft planning.")
            pos_tabs = st.tabs(SKILL_POSITIONS)
            for pos, pos_tab in zip(SKILL_POSITIONS, pos_tabs):
                with pos_tab:
                    pos_board = proj[proj["position"] == pos].head(20).reset_index(drop=True)
                    pos_board.index += 1
                    pos_display = pos_board[["player_name", "team", "projected_total", "projected_ppg", "draft_value"]].copy()
                    pos_display.columns = ["Player", "Team", "Proj. total pts", "Proj. pts/game", "Draft value"]
                    st.dataframe(pos_display.round(2), use_container_width=True)

st.divider()
st.caption(
    f"Data source: nflverse. History tabs show seasons {years[0]}–{years[-1]}. "
    "Scoring: PPR with a 0.5 TE Premium bonus per reception."
)
