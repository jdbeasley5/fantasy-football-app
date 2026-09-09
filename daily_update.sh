#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/Users/jbeasley/.nvm/versions/node/v20.20.1/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

LOGFILE="$(dirname "$0")/daily_update.log"
echo "=== $(date) ===" >> "$LOGFILE"

claude -p "$(cat <<'PROMPT'
You maintain a fantasy football draft-projection app (Python/Streamlit) in this repo. The app projections are built from structured NFL data (nflverse), but that structured data lags real news by days -- e.g. a season-ending injury can still show a player as "Active", and a very recent free-agent signing might not appear in any roster file yet.

Your job: research the last 24-48 hours of NFL news for anything that would materially change fantasy draft rankings for QB/RB/WR/TE players -- season-ending or multi-week injuries, retirements, and notable free-agent signings/trades (especially any that create or remove a starting opportunity) -- and keep data/manual_status_overrides.csv up to date with it.

The CSV has columns: player_name, action, team, note.
- action=exclude: player should be removed from the draft board entirely (e.g. ruled out for the season, retired). Leave team blank.
- action=include: player should be forced onto the board under a specific team (use this when a player signed/traded so recently that nflverse roster snapshot does not reflect their new team yet).

IMPORTANT: never use an apostrophe character anywhere in this CSV -- not in a note, not in a player name variant, nothing. This script is invoked via a command substitution wrapping a heredoc, and an odd number of literal apostrophe characters anywhere in this whole prompt breaks bash parsing for the entire script (this has actually happened -- do not reintroduce it). Write around it: use the full "does not" / "is not" form instead of a contraction, and spell a name like Le Meke Brockington without the apostrophe.

Steps:
1. Read the current data/manual_status_overrides.csv.
2. Check https://www.espn.com/nfl/transactions first -- it is the canonical source for exactly the moves this CSV needs (trades, waivers/releases, practice-squad signings, IR/PUP/Exempt List moves). A player can get traded, then cut, then land on a practice squad within days of each other (this has actually happened this season) -- always fetch this page fresh rather than trusting a remembered status. Cross-reference it with general NFL news search (last 1-2 days) for injuries and signings that would not show up as a formal "transaction" yet (e.g. a season-ending injury reported by a beat writer before the official IR paperwork is filed).
3. For each newsworthy situation not already reflected in the CSV, add a new row. Quote any note field that contains a comma (the note field commonly does -- this matters, a past version of this file broke from an unquoted comma). Do not duplicate an existing row for the same player unless the situation changed (update the note instead).
4. For every EXISTING row already in the CSV, double check against the transactions page whether that player situation has moved again since the row was written (e.g. an "include" row for a player who has since been cut, or waived then re-signed to a practice squad -- practice-squad players are NOT active-roster and should be "exclude", not "include"). Update or remove rows that are now stale rather than just adding new ones.
5. If an existing row is clearly resolved or stale, you may remove it -- but when in doubt, leave it for a human to review rather than guessing.
6. If you made any changes, commit them to main with a specific commit message (name the players and what changed) and push. If there is nothing new, do not commit anything.

Do not modify any other file in the repo. There is no CI/tests to run here -- just the CSV update and a commit/push if there is something to add.
PROMPT
)" --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch" >> "$LOGFILE" 2>&1

echo "=== done $(date) ===" >> "$LOGFILE"
echo "" >> "$LOGFILE"
