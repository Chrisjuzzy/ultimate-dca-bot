#!/bin/bash

# Update and install dependencies
sudo apt update && sudo apt install -y python3-pip git

# Clone the repository (replace YOUR_REPO_URL with the actual URL)
git clone YOUR_REPO_URL ultimate_dca_bot
cd ultimate_dca_bot

# Install Python dependencies
pip3 install -r requirements.txt

# Start the bot process in the background
nohup python3 bot.py &

# Start the dashboard process in the background
nohup streamlit run dashboard.py --server.address 0.0.0.0 --server.port 8504 &

# Schedule health monitor to run every 5 minutes
(crontab -l 2>/dev/null; echo "*/5 * * * * cd $(pwd) && python3 monitoring/health_monitor.py") | crontab -

# Output VPS access details
IP=$(curl -s ifconfig.me)
echo "Deployment complete! Access the dashboard at http://$IP:8504"