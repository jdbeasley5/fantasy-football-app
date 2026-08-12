#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
echo "Setup complete. Run with: ./venv/bin/streamlit run app.py"
