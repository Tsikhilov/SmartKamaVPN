#!/usr/bin/env python3
import sys, json, requests
sys.path.insert(0, "/opt/SmartKamaVPN")
from Utils import utils

BOT_TOKEN = "8512323955:AAFH-z7IzMON3uJLQxRd5Ooa9hRdW8Vnpbw"
CHAT_ID = 500661557

qr = utils.txt_to_qr("https://sub.smartkama.ru/kamil?app=1")
print(f"QR type={type(qr).__name__} len={len(qr)}")

markup = json.dumps({"inline_keyboard": [
    [{"text": "Open sub", "url": "https://sub.smartkama.ru/kamil"}],
    [{"text": "Back", "callback_data": "test_back"}],
]})

r = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
    data={"chat_id": CHAT_ID, "caption": "QR + HTTPS button test", "parse_mode": "HTML", "reply_markup": markup},
    files={"photo": ("qr.png", qr, "image/png")},
    timeout=10,
)
j = r.json()
print(f"ok={j.get('ok')} desc={j.get('description','')} msg_id={j.get('result',{}).get('message_id','N/A')}")
