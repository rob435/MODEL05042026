# Soak Run

Use this workflow for the first production-like validation on the VPS.
Do not deploy by copying a dirty local working tree full of SQLite files and caches.

## 1. Prepare host

```bash
sudo mkdir -p /opt
cd /opt
git clone https://github.com/rob435/MODEL05042026.git
cd /opt/MODEL05042026
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
mkdir -p data
```

## 2. Prepare environment

Start from [production.env.example](production.env.example):

```bash
cp deploy/production.env.example .env
```

Replace:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Use a rotated Telegram bot token, not the one previously pasted into chat.

If you tune `.env`, restart the service after the change. `systemd` will not magically reload environment files.

## 3. Preflight checks

```bash
. .venv/bin/activate
python universe_validator.py
python smoke.py --cycles 4 --db /opt/MODEL05042026/data/smoke.sqlite3 --strict-universe
python benchmark.py --tickers 100 --cycles 10
```

If any of these fail, do not start the soak.

## 4. Bounded soak run

Run the real service loop for 24 hours with Telegram enabled:

```bash
. .venv/bin/activate
set -a
source .env
set +a
python main.py --run-seconds 86400
```

For a shorter first pass:

```bash
python main.py --run-seconds 1800
```

For a no-alert dry run:

```bash
python main.py --run-seconds 1800 --disable-telegram
```

## 5. Inspect results

```bash
python report.py --db /opt/MODEL05042026/data/signals.sqlite3 --top 20
```

What you want:

- non-zero `processed_cycles`
- zero `websocket_failures` or at least clean recovery
- zero `queue_drops`
- reasonable top tickers, not one symbol dominating forever
- no Telegram delivery errors in logs

## 6. Promote to systemd

Copy [model050426.service](model050426.service) into `/etc/systemd/system/` and reload:

```bash
sudo cp deploy/model050426.service /etc/systemd/system/model050426.service
sudo systemctl daemon-reload
sudo systemctl enable model050426
sudo systemctl start model050426
sudo systemctl status model050426
```

Then monitor:

```bash
journalctl -u model050426 -f
```

Basic control commands:

```bash
sudo systemctl restart model050426
sudo systemctl status model050426 --no-pager
journalctl -u model050426 -n 100 --no-pager
```

## 7. Stop conditions

Stop the soak and inspect immediately if you see:

- repeated WebSocket reconnect loops
- missing-candle recovery thrashing
- queue drops
- Telegram send failures
- obviously nonsensical repeated signals
