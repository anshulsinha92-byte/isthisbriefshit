#!/bin/bash
# Is This Brief Shit? — Local setup
set -e
echo "💩 Setting up Is This Brief Shit..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
if [ -z "$ANTHROPIC_API_KEY" ]; then
    read -p "Anthropic API key: " api_key
    export ANTHROPIC_API_KEY="$api_key"
fi
echo "→ Running at http://localhost:5000"
python app.py
