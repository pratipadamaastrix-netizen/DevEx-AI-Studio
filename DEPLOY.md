# DevEx FM Platform — Deployment Guide (v0.2.5)

## Quick start (local / dev)

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python app.py
```

---

## Production deployment (Ubuntu + Nginx + Gunicorn)

### 1. Clone and set up

```bash
git clone <your-repo> /var/www/devex-fm
cd /var/www/devex-fm
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
nano .env   # fill in all keys
chmod 600 .env
```

### 3. Install and start the service

```bash
sudo cp devex-fm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable devex-fm
sudo systemctl start devex-fm
sudo systemctl status devex-fm
```

### 4. Configure Nginx

```bash
sudo cp nginx.conf /etc/nginx/sites-available/devex-fm
sudo ln -s /etc/nginx/sites-available/devex-fm /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 5. SSL (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 6. Configure Twilio webhook

In Twilio Console → Messaging → your WhatsApp sender:

- **Webhook URL:** `https://your-domain.com/wa/inbound`
- **Method:** HTTP POST

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask secret — use a long random string |
| `DEEPSEEK_API_KEY` | Yes* | AI ticket classification |
| `GEMINI_API_KEY` | Yes* | WhatsApp image analysis |
| `TWILIO_ACCOUNT_SID` | Yes* | Twilio account |
| `TWILIO_AUTH_TOKEN` | Yes* | Twilio auth |
| `TWILIO_WA_FROM` | Yes* | Your WhatsApp sender number |

\* Required for WhatsApp bridge. App runs without them but WA features fall back to keyword-only mode.

---

## Checking logs

```bash
sudo journalctl -u devex-fm -f          # live logs
sudo journalctl -u devex-fm --since today
```
