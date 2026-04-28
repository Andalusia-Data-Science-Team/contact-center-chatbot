#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Andalusia Booking Bot — Server Deployment Script
# Run this on your company server (Ubuntu/Debian)
# ═══════════════════════════════════════════════════════════════

set -e

echo "╔══════════════════════════════════════════════════╗"
echo "║  Andalusia Booking Bot — Server Setup            ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ──────────────────────────────────
echo "📦 Step 1: Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv curl unixodbc-dev

# ── 2. SQL Server ODBC Driver 17 ────────────────────────────
echo ""
echo "📦 Step 2: Installing SQL Server ODBC Driver 17..."
if ! odbcinst -q -d | grep -q "ODBC Driver 17"; then
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
    
    # Detect Ubuntu version
    UBUNTU_VERSION=$(lsb_release -rs)
    echo "   Detected Ubuntu $UBUNTU_VERSION"
    
    curl -fsSL "https://packages.microsoft.com/config/ubuntu/$UBUNTU_VERSION/prod.list" | sudo tee /etc/apt/sources.list.d/mssql-release.list > /dev/null
    sudo apt-get update -qq
    sudo ACCEPT_EULA=Y apt-get install -y msodbcsql17
    echo "   ✅ ODBC Driver 17 installed"
else
    echo "   ✅ ODBC Driver 17 already installed"
fi

# ── 3. Python virtual environment ───────────────────────────
echo ""
echo "🐍 Step 3: Setting up Python environment..."
cd "$(dirname "$0")"
BOT_DIR=$(pwd)

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "   ✅ Virtual environment created"
else
    echo "   ✅ Virtual environment already exists"
fi

source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "   ✅ Python packages installed"

# ── 4. Test DB connection ───────────────────────────────────
echo ""
echo "🔌 Step 4: Testing database connection..."
python3 -c "
from dotenv import load_dotenv; load_dotenv()
from config.settings import DB_SERVER, DB_DATABASE
print(f'   Connecting to {DB_SERVER}/{DB_DATABASE}...')
from db.database import get_connection
try:
    conn = get_connection()
    conn.close()
    print('   ✅ Database connection successful')
except Exception as e:
    print(f'   ❌ Database connection failed: {e}')
    print('   Check DB_SERVER, credentials in .env file')
"

# ── 5. Test LLM connection ──────────────────────────────────
echo ""
echo "🤖 Step 5: Testing LLM connection..."
python3 -c "
from dotenv import load_dotenv; load_dotenv()
from llm.client import call_llm
try:
    result = call_llm(
        messages=[{'role': 'user', 'content': 'say hello'}],
        max_tokens=10,
        label='test',
    )
    print(f'   ✅ LLM connection successful: {result[:50]}')
except Exception as e:
    print(f'   ❌ LLM connection failed: {e}')
    print('   Check OPENROUTER_API_KEY in .env file and internet access')
"

# ── 6. Create systemd service (runs on boot) ────────────────
echo ""
echo "⚙️  Step 6: Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/andalusia-bot.service"
sudo tee $SERVICE_FILE > /dev/null << SERVICEEOF
[Unit]
Description=Andalusia Booking Bot
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/streamlit run app.py --server.port 8502 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICEEOF

sudo systemctl daemon-reload
sudo systemctl enable andalusia-bot
sudo systemctl restart andalusia-bot
echo "   ✅ Service created and started"

# ── 7. Check status ─────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
sleep 2
if sudo systemctl is-active --quiet andalusia-bot; then
    SERVER_IP=$(hostname -I | awk '{print $1}')
    echo "✅ Bot is running!"
    echo ""
    echo "🌐 Share this URL with your team:"
    echo ""
    echo "   http://$SERVER_IP:8502"
    echo ""
    echo "═══════════════════════════════════════════════════"
    echo ""
    echo "📋 Useful commands:"
    echo "   View logs:     sudo journalctl -u andalusia-bot -f"
    echo "   Stop bot:      sudo systemctl stop andalusia-bot"
    echo "   Restart bot:   sudo systemctl restart andalusia-bot"
    echo "   Check status:  sudo systemctl status andalusia-bot"
    echo "   Update code:   (replace files) then: sudo systemctl restart andalusia-bot"
else
    echo "❌ Service failed to start. Check logs:"
    echo "   sudo journalctl -u andalusia-bot -n 50"
fi
