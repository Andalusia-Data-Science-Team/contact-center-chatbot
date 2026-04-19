#!/bin/bash
# One-command deploy: push to GitHub + update server
# Usage: bash deploy_remote.sh

set -e

SERVER="ai@10.24.105.220"
KEY="$HOME/.ssh/deploy_key"
PROJECT="/home/ai/Workspace/Alia/contact-center-chatbot"

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
ssh -i "$KEY" "$SERVER" "cd $PROJECT && git pull origin main && if [ -f venv/bin/activate ]; then source venv/bin/activate && pip install -r requirements.txt -q; fi && sudo systemctl restart andalusia-bot && echo '✅ Bot restarted successfully'"

echo ""
echo "══════════════════════════════════════"
echo "✅ Deploy complete!"
echo "🌐 Bot: http://10.24.105.220:8502"
echo "📊 Logs: http://10.24.105.220:8502/logs"
echo "══════════════════════════════════════"
