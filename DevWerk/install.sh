#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "DevWerk installed. Copy config/llm.example.json to config/llm.json and set credentials before starting."
