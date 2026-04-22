#!/bin/bash
# One-command deploy: push to GitHub + update server
# Usage: bash deploy_remote.sh

set -e

SERVER="ai@10.24.105.220"
KEY="$HOME/.ssh/deploy_key"
PROJECT="/home/ai/Workspace/Alia/booking_bot_latest"

echo "══════════════════════════════════════"
echo "  Deploying Andalusia Booking Bot"
echo "══════════════════════════════════════"

# Step 1: Push to GitHub
echo ""
echo "📤 Pushing to GitHub..."
git push origin main

# Step 2: Pull on server + restart
echo ""
echo "🖥️  Updating server..."
ssh -i "$KEY" "$SERVER" "cd $PROJECT && git pull origin main && if [ -f venv/bin/activate ]; then source venv/bin/activate && pip install -r requirements.txt -q; fi"

# Try to restart the service (may need sudo password)
echo ""
echo "🔄 Restarting bot service..."
ssh -i "$KEY" "$SERVER" "sudo systemctl restart andalusia-bot 2>/dev/null && echo '✅ Bot restarted' || echo '⚠️  Could not restart automatically. Run on server: sudo systemctl restart andalusia-bot'"

echo ""
echo "══════════════════════════════════════"
echo "✅ Code deployed!"
echo "🌐 Bot: http://10.24.105.220:8502"
echo "📊 Logs: http://10.24.105.220:8502/logs"
echo "══════════════════════════════════════"
