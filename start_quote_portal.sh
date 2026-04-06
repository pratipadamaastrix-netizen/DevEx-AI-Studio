#!/bin/bash
set -e

echo ""
echo "============================================================"
echo " DevEx Studios FM Platform + Quote Portal QP1.0"
echo "============================================================"
echo ""

# Check for .env
if [ ! -f .env ]; then
    echo "[SETUP] .env not found — copying from .env.example"
    cp .env.example .env
    echo ""
    echo "  *** IMPORTANT ***"
    echo "  Edit .env and add your DEEPSEEK_API_KEY and GEMINI_API_KEY"
    echo "  before using AI features. The portal works without them but"
    echo "  will return mock/fallback AI responses."
    echo ""
fi

# Create venv if not present
if [ ! -d venv ]; then
    echo "[SETUP] Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install
source venv/bin/activate
echo "[SETUP] Installing dependencies..."
pip install -r requirements.txt --quiet

echo ""
echo "[START] Server starting at http://localhost:5000"
echo ""
echo "  Quote Wizard   >  http://localhost:5000/quote"
echo "  Ops Dashboard  >  http://localhost:5000/ops/quotes"
echo "  FM Console     >  http://localhost:5000/fm"
echo ""
echo "  Press Ctrl+C to stop"
echo ""

python app.py
