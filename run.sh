#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ -n "$(git status --porcelain)" ]; then
    echo "Skipping git pull: you have local uncommitted changes. Run the app as-is."
else
    echo "Pulling latest changes (including any daily injury/signing updates)..."
    git pull --ff-only origin main || echo "Warning: git pull failed (offline, or diverged history) — continuing with local copy."
fi

./venv/bin/streamlit run app.py
