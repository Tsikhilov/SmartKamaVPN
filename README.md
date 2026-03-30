<h1 align="center">SmartKamaVPN Bot</h1>

<p align="center">
Telegram bot for managing your Hiddify VPN panel directly from Telegram.<br>
Panel: <a href="https://bot.smartkama.ru">bot.smartkama.ru</a>
</p>

## Features
- [x] Multi panel support
- [x] Sell VPN configs / subscriptions
- [x] Add / Remove / Edit users
- [x] View users list with full info (traffic, date, UUID)
- [x] Search users (by name, config, UUID)
- [x] Subscription links & QR codes
- [x] Auto backup panel + send to Telegram
- [x] View server status (RAM, CPU, disk)
- [x] YooKassa payment integration
- [x] Client (User) bot with wallet, purchase, tickets
- [x] Multi language (Russian, English)
- [x] Cron jobs: reminders, backups
- [x] Reset user traffic / days
- [x] And more...

## Installation

Run the following command on your Linux server:

```bash
sudo bash -c "$(curl -Lfo- https://raw.githubusercontent.com/Tsikhilov/SmartKamaVPN/main/install.sh)"
```

Before installing, prepare:

1. **Admin Telegram ID** — get from [@userinfobot](https://t.me/userinfobot) (e.g. `123456789`)
2. **Admin Bot Token** — get from [@BotFather](https://t.me/BotFather)
3. **Client Bot Token** (optional) — separate bot for users
4. **Hiddify Panel URL** — e.g. `https://bot.smartkama.ru/XG2KXE1cOyMGJVEW`
5. **Language** — `RU` (default) or `EN`

## Commands

### Start / Restart bot
```bash
cd /opt/SmartKamaVPN && chmod +x restart.sh && ./restart.sh
```

### Update bot
```bash
cd /opt/SmartKamaVPN && git pull && pip install -r requirements.txt && ./restart.sh
```

### Edit config
```bash
cd /opt/SmartKamaVPN && python3 config.py && ./restart.sh
```

### View logs
```bash
cat /opt/SmartKamaVPN/Logs/smartkamavpn.log
```

### Stop bot
```bash
pkill -9 -f smartkamavpnTelegramBot.py
```

### Uninstall
```bash
cd /opt/SmartKamaVPN && chmod +x uninstall.sh && ./uninstall.sh
```

### Reinstall
```bash
cd /opt/ && rm -rf /opt/SmartKamaVPN && sudo bash -c "$(curl -Lfo- https://raw.githubusercontent.com/Tsikhilov/SmartKamaVPN/main/install.sh)"
```
