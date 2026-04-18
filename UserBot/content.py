import os
import json
from config import LANG
from Utils.utils import all_configs_settings

FOLDER = "Json"
MSG_FILE = "messages.json"
BTN_FILE = "buttons.json"
CMD_FILE = "commands.json"

settings = all_configs_settings()

# Load messages with fallback to RU
with open(os.path.join(os.path.dirname(__file__), FOLDER, MSG_FILE), encoding='utf-8') as f:
    MESSAGES_ALL = json.load(f)
# Use RU as default, fallback to EN if RU not available
MESSAGES = MESSAGES_ALL.get(LANG, MESSAGES_ALL.get('RU', MESSAGES_ALL.get('EN', {})))
if settings.get('msg_user_start'):
    MESSAGES['WELCOME'] = settings.get('msg_user_start')

# Load buttons with fallback to RU
with open(os.path.join(os.path.dirname(__file__), FOLDER, BTN_FILE), encoding='utf-8') as f:
    KEY_MARKUP_ALL = json.load(f)
KEY_MARKUP = KEY_MARKUP_ALL.get(LANG, KEY_MARKUP_ALL.get('RU', KEY_MARKUP_ALL.get('EN', {})))

# Load commands with fallback to RU
with open(os.path.join(os.path.dirname(__file__), FOLDER, CMD_FILE), encoding='utf-8') as f:
    BOT_COMMANDS_ALL = json.load(f)
BOT_COMMANDS = BOT_COMMANDS_ALL.get(LANG, BOT_COMMANDS_ALL.get('RU', BOT_COMMANDS_ALL.get('EN', {})))
