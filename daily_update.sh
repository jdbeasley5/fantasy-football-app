#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/Users/jbeasley/.nvm/versions/node/v20.20.1/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

LOGFILE="$(dirname "$0")/daily_update.log"
echo "=== $(date) ===" >> "$LOGFILE"

claude -p "$(cat <<'PROMPT'
You maintain a fantasy football draft-projection app (Python/Streamlit) in this repo. The app's projections are built from structured NFL data (nflverse), but that structured data lags real news by days -- e.g. a season-ending injury can still show a player as 'Active', and a very recent free-agent signing might not appear in any roster file yet.

Your job: research the last 24-48 hours of NFL news for anything that would materially change fantasy draft rankings for QB/RB/WR/TE players -- season-ending or multi-week injuries, retirements, and notable free-agent signings/trades (especially any that create or remove a starting opportunity) -- and keep data/manual_status_overrides.csv up to date with it.

The CSV has columns: player_name, action, team, note.
- action=exclude: player should be removed from the draft board entirely (e.g. ruled out for the season, retired). Leave team blank.
- action=include: player should be forced onto the board under a specific team (use this when a player signed/traded so recently that nflverse's roster snapshot doesn't reflect their new team yet).

Steps:
1. Read the current data/manual_status_overrides.csv.
2. Search current NFL news (last 1-2 days) for significant injuries, retirements, trades, and free-agent signings affecting QB/RB/WR/TE players.
3. For each newsworthy situation not already reflected in the CSV, add a new row. Quote any note field that contains a comma (the note field commonly does -- this matters, a past version of this file broke from an unquoted comma). Don't duplicate an existing row for the same player unless the situation changed (update the note instead).
4. If an existing row is clearly resolved or stale, you may remove it -- but when in doubt, leave it for a human to review rather than guessing.
5. If you made any changes, commit them to main with a specific commit message (name the players and what changed) and push. If there's nothing new, don't commit anything.

Do not modify any other file in the repo. There's no CI/tests to run here -- just the CSV update and a commit/push if there's something to add.
PROMPT
)" --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebSearch" >> "$LOGFILE" 2>&1

echo "=== done $(date) ===" >> "$LOGFILE"
echo "" >> "$LOGFILE"
