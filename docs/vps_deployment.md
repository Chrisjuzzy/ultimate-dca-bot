# VPS Deployment Checklist

Deploy only after dashboard, backtesting, and 2-4 weeks of paper validation pass.

## Recommended VPS

- Oracle Cloud Free Tier
- Hetzner Cloud
- Contabo VPS

Minimum:

- Ubuntu 22.04
- 1-2 vCPU
- 2 GB RAM

## Server Setup

```bash
sudo apt update
sudo apt install python3 python3-venv git tmux supervisor -y
```

## App Setup

```bash
sudo mkdir -p /opt/ultimate_dca_bot
sudo chown "$USER":"$USER" /opt/ultimate_dca_bot
cd /opt/ultimate_dca_bot
git clone YOUR_REPO_URL .
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
mkdir -p logs data database
```

Create `.env` directly on the server.

Never commit `.env`.

## Supervisor Deployment

```bash
sudo cp deployment/supervisor_ultimate_dca_bot.conf /etc/supervisor/conf.d/ultimate_dca_bot.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start ultimate_dca_bot
sudo supervisorctl status
```

Logs:

```bash
tail -f logs/bot.log
tail -f logs/supervisor_stdout.log
tail -f logs/supervisor_stderr.log
```

## systemd Alternative

```bash
sudo cp deployment/ultimate-dca-bot.service /etc/systemd/system/ultimate-dca-bot.service
sudo systemctl daemon-reload
sudo systemctl enable ultimate-dca-bot
sudo systemctl start ultimate-dca-bot
sudo systemctl status ultimate-dca-bot
```

## Health Check

```bash
. .venv/bin/activate
python monitoring/health_monitor.py
```

## Deployment Rules

- Start in `TESTNET=true`.
- Validate Telegram alerts.
- Validate restart recovery.
- Validate paper mode before live mode.
- Rotate all exposed credentials before real money.
- Start real capital extremely small only after paper validation.
