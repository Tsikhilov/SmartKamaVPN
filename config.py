# Description: Configuration file for SmartKamaVPN Bot
# Panel: https://bot.smartkama.ru/XG2KXE1cOyMGJVEW/

import json
import logging
import os
import re
from urllib.parse import urlparse
import requests
from termcolor import colored

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from version import __version__

os.environ['no_proxy'] = '*'

VERSION = __version__

# Paths
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_DB_LOC = os.path.join(_BASE_DIR, "Database", "smartkamavpn.db")
LOG_DIR = os.path.join(_BASE_DIR, "Logs")
LOG_LOC = os.path.join(LOG_DIR, "smartkamavpn.log")
BACKUP_LOC = os.path.join(_BASE_DIR, "Backup")
RECEIPTIONS_LOC = os.path.join(_BASE_DIR, "UserBot", "Receiptions")
BOT_BACKUP_LOC = os.path.join(_BASE_DIR, "Backup", "Bot")

# Hiddify panel
API_PATH = "/api/v2"
SMARTKAMAVPN_BOT_ID = "@SmartKamaVPNbot"

# Welcome banner (Telegram file_id or URL). Set to None to disable.
WELCOME_BANNER = os.environ.get("WELCOME_BANNER", None)

# if directories not exists, create it
for _d in [LOG_DIR, BACKUP_LOC, BOT_BACKUP_LOC, RECEIPTIONS_LOC,
           os.path.join(_BASE_DIR, "Database")]:
    os.makedirs(_d, exist_ok=True)

# set logging  
logging.basicConfig(handlers=[logging.FileHandler(filename=LOG_LOC,
                                                  encoding='utf-8', mode='w')],
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)


def setup_users_db():
    # global USERS_DB
    try:
        if not os.path.exists(USERS_DB_LOC):
            logging.error(f"Database file not found in {USERS_DB_LOC} directory!")
            with open(USERS_DB_LOC, "w") as f:
                pass
        # USERS_DB = Database.dbManager.UserDBManager(USERS_DB_LOC)
    except Exception as e:
        logging.error(f"Error while connecting to database \n Error:{e}")
        raise Exception(f"Error while connecting to database \nBe in touch with {SMARTKAMAVPN_BOT_ID}")
    # return USERS_DB


setup_users_db()
from Database.dbManager import UserDBManager


def load_config(db):
    try:
        config = db.select_str_config()
        if not config:
            db.set_default_configs()
            config = db.select_str_config()
        configs = {}
        for conf in config:
            configs[conf['key']] = conf['value']

        return configs
    except Exception as e:
        logging.error(f"Error while loading config \n Error:{e}")
        raise Exception(f"Error while loading config \nBe in touch with {SMARTKAMAVPN_BOT_ID}")


def load_server_url(db):
    try:
        panel_url = db.select_servers()
        if not panel_url:
            return None
        return panel_url[0]['url']
    except Exception as e:
        logging.error(f"Error while loading panel_url \n Error:{e}")
        raise Exception(f"Error while loading panel_url \nBe in touch with {SMARTKAMAVPN_BOT_ID}")


ADMINS_ID, TELEGRAM_TOKEN, CLIENT_TOKEN, PANEL_URL, LANG, PANEL_ADMIN_ID = None, None, None, None, None, None
HIDDIFY_API_KEY = None  # deprecated, оставлен для совместимости
YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY = None, None
CRYPTOPAY_API_TOKEN = None

# Active panel provider for API adapter: 3xui (default) | marzban
PANEL_PROVIDER = os.getenv("SMARTKAMA_PANEL_PROVIDER", "3xui").strip().lower()

# 3x-ui panel settings (могут быть переопределены через .env или БД)
THREEXUI_USERNAME = os.getenv("THREEXUI_USERNAME", "admin")
THREEXUI_PASSWORD = os.getenv("THREEXUI_PASSWORD", "SmartKama2026!")
THREEXUI_PANEL_URL = os.getenv("THREEXUI_PANEL_URL", "https://sub.smartkama.ru:55445")
THREEXUI_WEB_BASE_PATH = os.getenv("THREEXUI_WEB_BASE_PATH", "/902184284ee0d060")
THREEXUI_INBOUND_ID = int(os.getenv("THREEXUI_INBOUND_ID", "1"))
THREEXUI_INBOUND_IDS = os.getenv("THREEXUI_INBOUND_IDS", "")
THREEXUI_REALITY_PUBLIC_KEY = os.getenv("THREEXUI_REALITY_PUBLIC_KEY", "")

# Marzban settings (phase migration support)
MARZBAN_PANEL_URL = os.getenv("MARZBAN_PANEL_URL", "")
MARZBAN_USERNAME = os.getenv("MARZBAN_USERNAME", "")
MARZBAN_PASSWORD = os.getenv("MARZBAN_PASSWORD", "")
MARZBAN_ACCESS_TOKEN = os.getenv("MARZBAN_ACCESS_TOKEN", "")
MARZBAN_TLS_VERIFY = os.getenv("MARZBAN_TLS_VERIFY", "false").strip().lower() in ("1", "true", "yes", "on")
MARZBAN_INBOUND_TAGS = os.getenv("MARZBAN_INBOUND_TAGS", "")

# MTProto proxy settings
MTPROTO_ENABLED = os.getenv("SMARTKAMA_MTPROTO_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
MTPROTO_SERVER = os.getenv("SMARTKAMA_MTPROTO_SERVER", "")
MTPROTO_PORT = int(os.getenv("SMARTKAMA_MTPROTO_PORT", "3128"))
MTPROTO_SECRET = os.getenv("SMARTKAMA_MTPROTO_SECRET", "")
MTPROTO_PROMOTE_TAG = os.getenv("SMARTKAMA_MTPROTO_PROMOTE_TAG", "")

# WhatsApp proxy settings
WHATSAPP_PROXY_ENABLED = os.getenv("SMARTKAMA_WHATSAPP_PROXY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
WHATSAPP_PROXY_SERVER = os.getenv("SMARTKAMA_WHATSAPP_PROXY_SERVER", "")

# Signal proxy settings
SIGNAL_PROXY_ENABLED = os.getenv("SMARTKAMA_SIGNAL_PROXY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
SIGNAL_PROXY_DOMAIN = os.getenv("SMARTKAMA_SIGNAL_PROXY_DOMAIN", "")


def set_config_variables(configs, server_url):
    env_admin_ids = os.getenv("SMARTKAMA_ADMIN_IDS")
    env_admin_token = os.getenv("SMARTKAMA_BOT_TOKEN_ADMIN")
    env_client_token = os.getenv("SMARTKAMA_BOT_TOKEN_CLIENT")
    env_lang = os.getenv("SMARTKAMA_LANG")
    env_panel_url = os.getenv("SMARTKAMA_PANEL_URL")

    raw_admin_ids = configs.get("bot_admin_id") or env_admin_ids
    telegram_token = configs.get("bot_token_admin") or env_admin_token
    client_token = configs.get("bot_token_client") or env_client_token
    panel_url = server_url or env_panel_url
    lang = configs.get("bot_lang") or env_lang or "RU"

    missing_required = not raw_admin_ids or not telegram_token or not panel_url
    if missing_required:
        print(colored("Config is not set! , Please run config.py first", "red"))
        raise Exception(f"Config is not set!\nBe in touch with {SMARTKAMAVPN_BOT_ID}")

    global ADMINS_ID, TELEGRAM_TOKEN, PANEL_URL, LANG, PANEL_ADMIN_ID, CLIENT_TOKEN, HIDDIFY_API_KEY
    global YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
    global CRYPTOPAY_API_TOKEN
    global PANEL_PROVIDER
    global THREEXUI_USERNAME, THREEXUI_PASSWORD, THREEXUI_PANEL_URL, THREEXUI_WEB_BASE_PATH, THREEXUI_INBOUND_ID, THREEXUI_INBOUND_IDS, THREEXUI_REALITY_PUBLIC_KEY
    global MARZBAN_PANEL_URL, MARZBAN_USERNAME, MARZBAN_PASSWORD, MARZBAN_ACCESS_TOKEN, MARZBAN_TLS_VERIFY, MARZBAN_INBOUND_TAGS
    global MTPROTO_ENABLED, MTPROTO_SERVER, MTPROTO_PORT, MTPROTO_SECRET, MTPROTO_PROMOTE_TAG
    global WHATSAPP_PROXY_ENABLED, WHATSAPP_PROXY_SERVER
    global SIGNAL_PROXY_ENABLED, SIGNAL_PROXY_DOMAIN

    if isinstance(raw_admin_ids, str) and raw_admin_ids.strip().startswith("["):
        ADMINS_ID = json.loads(raw_admin_ids)
    else:
        ADMINS_ID = [int(x.strip()) for x in str(raw_admin_ids).split(",") if x.strip()]

    TELEGRAM_TOKEN = telegram_token
    CLIENT_TOKEN = client_token

    if CLIENT_TOKEN:
        setup_users_db()
    PANEL_URL = panel_url
    LANG = lang
    # Извлечь UUID из Hiddify-URL (обратная совместимость, для 3x-ui будет None)
    path_parts = [p for p in urlparse(PANEL_URL).path.split('/') if p]
    uuid_like = [p for p in path_parts if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", p)]
    PANEL_ADMIN_ID = uuid_like[0] if uuid_like else None
    HIDDIFY_API_KEY = configs.get("hiddify_api_key") or os.getenv("SMARTKAMA_API_KEY") or PANEL_ADMIN_ID

    provider_raw = (configs.get("panel_provider") or os.getenv("SMARTKAMA_PANEL_PROVIDER") or PANEL_PROVIDER or "3xui").strip().lower()
    if provider_raw in ("3x-ui", "x-ui"):
        provider_raw = "3xui"
    if provider_raw not in ("3xui", "marzban"):
        provider_raw = "3xui"
    PANEL_PROVIDER = provider_raw

    # 3x-ui настройки (переопределяют значения по умолчанию если в БД/env заданы)
    THREEXUI_USERNAME = configs.get("threexui_username") or os.getenv("THREEXUI_USERNAME") or THREEXUI_USERNAME
    THREEXUI_PASSWORD = configs.get("threexui_password") or os.getenv("THREEXUI_PASSWORD") or THREEXUI_PASSWORD
    THREEXUI_PANEL_URL = configs.get("threexui_panel_url") or os.getenv("THREEXUI_PANEL_URL") or THREEXUI_PANEL_URL
    THREEXUI_WEB_BASE_PATH = configs.get("threexui_web_base_path") or os.getenv("THREEXUI_WEB_BASE_PATH") or THREEXUI_WEB_BASE_PATH
    THREEXUI_INBOUND_IDS = (configs.get("threexui_inbound_ids") or os.getenv("THREEXUI_INBOUND_IDS") or THREEXUI_INBOUND_IDS or "").strip()
    THREEXUI_REALITY_PUBLIC_KEY = (configs.get("threexui_reality_public_key") or os.getenv("THREEXUI_REALITY_PUBLIC_KEY") or THREEXUI_REALITY_PUBLIC_KEY or "").strip()
    _inbound_id = configs.get("threexui_inbound_id") or os.getenv("THREEXUI_INBOUND_ID")
    if _inbound_id:
        try:
            THREEXUI_INBOUND_ID = int(_inbound_id)
        except (ValueError, TypeError):
            logging.warning(f"Invalid THREEXUI_INBOUND_ID value: {_inbound_id!r}, using default 1")
            THREEXUI_INBOUND_ID = 1

    # Marzban settings (для поэтапной миграции)
    MARZBAN_PANEL_URL = (configs.get("marzban_panel_url") or os.getenv("MARZBAN_PANEL_URL") or MARZBAN_PANEL_URL or "").strip()
    MARZBAN_USERNAME = (configs.get("marzban_username") or os.getenv("MARZBAN_USERNAME") or MARZBAN_USERNAME or "").strip()
    MARZBAN_PASSWORD = (configs.get("marzban_password") or os.getenv("MARZBAN_PASSWORD") or MARZBAN_PASSWORD or "").strip()
    MARZBAN_ACCESS_TOKEN = (configs.get("marzban_access_token") or os.getenv("MARZBAN_ACCESS_TOKEN") or MARZBAN_ACCESS_TOKEN or "").strip()
    MARZBAN_INBOUND_TAGS = (configs.get("marzban_inbound_tags") or os.getenv("MARZBAN_INBOUND_TAGS") or MARZBAN_INBOUND_TAGS or "").strip()
    _marzban_tls_verify = (configs.get("marzban_tls_verify") or os.getenv("MARZBAN_TLS_VERIFY") or str(MARZBAN_TLS_VERIFY)).strip().lower()
    MARZBAN_TLS_VERIFY = _marzban_tls_verify in ("1", "true", "yes", "on")

    # MTProto proxy settings
    _mtproto_enabled = (configs.get("mtproto_enabled") or os.getenv("SMARTKAMA_MTPROTO_ENABLED") or str(MTPROTO_ENABLED)).strip().lower()
    MTPROTO_ENABLED = _mtproto_enabled in ("1", "true", "yes", "on")
    MTPROTO_SERVER = (configs.get("mtproto_server") or os.getenv("SMARTKAMA_MTPROTO_SERVER") or MTPROTO_SERVER or "").strip()
    _mtproto_port = configs.get("mtproto_port") or os.getenv("SMARTKAMA_MTPROTO_PORT")
    if _mtproto_port:
        try:
            MTPROTO_PORT = int(_mtproto_port)
        except (ValueError, TypeError):
            pass
    MTPROTO_SECRET = (configs.get("mtproto_secret") or os.getenv("SMARTKAMA_MTPROTO_SECRET") or MTPROTO_SECRET or "").strip()
    MTPROTO_PROMOTE_TAG = (configs.get("mtproto_promote_tag") or os.getenv("SMARTKAMA_MTPROTO_PROMOTE_TAG") or MTPROTO_PROMOTE_TAG or "").strip()

    # WhatsApp proxy settings
    _wa_proxy_enabled = (configs.get("whatsapp_proxy_enabled") or os.getenv("SMARTKAMA_WHATSAPP_PROXY_ENABLED") or str(WHATSAPP_PROXY_ENABLED)).strip().lower()
    WHATSAPP_PROXY_ENABLED = _wa_proxy_enabled in ("1", "true", "yes", "on")
    WHATSAPP_PROXY_SERVER = (configs.get("whatsapp_proxy_server") or os.getenv("SMARTKAMA_WHATSAPP_PROXY_SERVER") or WHATSAPP_PROXY_SERVER or "").strip()

    # Signal proxy settings
    _signal_proxy_enabled = (configs.get("signal_proxy_enabled") or os.getenv("SMARTKAMA_SIGNAL_PROXY_ENABLED") or str(SIGNAL_PROXY_ENABLED)).strip().lower()
    SIGNAL_PROXY_ENABLED = _signal_proxy_enabled in ("1", "true", "yes", "on")
    SIGNAL_PROXY_DOMAIN = (configs.get("signal_proxy_domain") or os.getenv("SMARTKAMA_SIGNAL_PROXY_DOMAIN") or SIGNAL_PROXY_DOMAIN or "").strip()

    # Load YooKassa settings
    YOOKASSA_SHOP_ID = configs.get("yookassa_shop_id")
    YOOKASSA_SECRET_KEY = configs.get("yookassa_secret_key")

    # Load CryptoPay settings
    CRYPTOPAY_API_TOKEN = configs.get("cryptopay_api_token")


def panel_url_validator(url):
    if not (url.startswith("https://") or url.startswith("http://")):
        print(colored("URL must start with http:// or https://", "red"))
        return False
    if url.endswith("/"):
        url = url[:-1]
    if url.endswith("admin"):
        url = url.replace("/admin", "")
    if url.endswith("admin/user"):
        url = url.replace("/admin/user", "")
    print(colored("Checking URL...", "yellow"))
    try:
        request = requests.get(f"{url}/admin/")
    except requests.exceptions.ConnectionError as e:
        print(colored("URL is not valid! Error in connection", "red"))
        print(colored(f"Error: {e}", "red"))
        return False

    if request.status_code != 200:
        print(colored("URL is not valid!", "red"))
        print(colored(f"Error: {request.status_code}", "red"))
        return False
    elif request.status_code == 200:
        print(colored("URL is valid!", "green"))
    return url


def bot_token_validator(token):
    print(colored("Checking Bot Token...", "yellow"))
    try:
        request = requests.get(f"https://api.telegram.org/bot{token}/getMe")
    except requests.exceptions.ConnectionError:
        print(colored("Bot Token is not valid! Error in connection", "red"))
        return False
    if request.status_code != 200:
        print(colored("Bot Token is not valid!", "red"))
        return False
    elif request.status_code == 200:
        print(colored("Bot Token is valid!", "green"))
        print(colored("Bot Username:", "green"), "@"+request.json()['result']['username'])
    return True


def set_by_user():
    print()
    print(
        colored("Example: 123456789\nIf you have more than one admin, split with comma(,)\n[get it from @userinfobot]",
                "yellow"))
    while True:
        admin_id = input("[+] Enter Telegram Admin Number IDs: ")
        admin_ids = admin_id.split(',')
        admin_ids = [admin_id.strip() for admin_id in admin_ids]
        if not all(admin_id.isdigit() for admin_id in admin_ids):
            print(colored("Admin IDs must be numbers separated by commas!", "red"))
            continue
        admin_ids = [int(admin_id) for admin_id in admin_ids]
        break
    print()
    print(colored("Example: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ\n[get it from @BotFather]", "yellow"))
    while True:
        token = input("[+] Enter your Admin bot token: ")
        if not token:
            print(colored("Token is required", "red"))
            continue
        if not bot_token_validator(token):
            continue
        break

    print()
    print(colored("You can use the bot as a userbot for your clients!", "yellow"))
    while True:
        userbot = input("Do you want a Bot for your users? (y/n): ").lower()
        if userbot not in ["y", "n"]:
            print(colored("Please enter y or n!", "red"))
            continue
        break
    if userbot == "y":
        print()
        print(colored("Example: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ\n[get it from @BotFather]", "yellow"))
        while True:
            client_token = input("[+] Enter your client (For Users) bot token: ")
            if not client_token:
                print(colored("Token is required!", "red"))
                continue
            if client_token == token:
                print(colored("Client token must be different from Admin token!", "red"))
                continue
            if not bot_token_validator(client_token):
                continue
            break
    else:
        client_token = None
    print()
    print(colored(
        "Example: https://panel.example.com/7frgemkvtE0/78854985-68dp-425c-989b-7ap0c6kr9bd4\n[exactly like this!]",
        "yellow"))
    while True:
        url = input("[+] Enter your panel URL:")
        if not url:
            print(colored("URL is required!", "red"))
            continue
        url = panel_url_validator(url)
        if not url:
            continue
        break
    print()
    print(colored("Example: RU (default: RU)\n[It is better that the language of the bot is the same as the panel]",
                  "yellow"))
    while True:
        lang = input("[+] Select your language (RU(Russian), EN(English)): ") or "RU"
        if lang not in ["RU", "EN"]:
            print(colored("Language must be RU or EN!", "red"))
            continue
        break

    return admin_ids, token, url, lang, client_token


def set_config_in_db(db, admin_ids, token, url, lang, client_token):
    try:
        # if str_config is not exists, create it
        if not db.select_str_config():
            db.add_str_config("bot_admin_id", value=json.dumps(admin_ids))
            db.add_str_config("bot_token_admin", value=token)
            db.add_str_config("bot_token_client", value=client_token)
            db.add_str_config("bot_lang", value=lang)
        else:
            db.edit_str_config("bot_admin_id", value=json.dumps(admin_ids))
            db.edit_str_config("bot_token_admin", value=token)
            db.edit_str_config("bot_token_client", value=client_token)
            db.edit_str_config("bot_lang", value=lang)
        # if servers is not exists, create it
        if not db.select_servers():
            db.add_server(url, 2000, title="Main Server", default_server=True)
        else:
            # find default server
            default_servers = db.find_server(default_server=True)
            if default_servers:
                default_server_id = default_servers[0]['id']
                default_server = default_servers[0]
                if default_server['url'] != url:
                    db.edit_server(default_server_id, url=url)
            else:
                db.add_server(url, 2000, title="Main Server", default_server=True)
    except Exception as e:
        logging.error(f"Error while inserting config to database \n Error:{e}")
        raise Exception(f"Error while inserting config to database \nBe in touch with {SMARTKAMAVPN_BOT_ID}")


def print_current_conf(conf, server_url):
    print()
    print(colored("Current configuration data:", "yellow"))
    print(f"[+] Admin IDs: {conf.get('bot_admin_id')}")
    print(f"[+] Admin Bot Token: {conf.get('bot_token_admin')}")
    print(f"[+] Client Bot Token: {conf.get('bot_token_client')}")
    print(f"[+] Panel URL: {server_url}")
    print(f"[+] Language: {conf.get('bot_lang')}")
    print()


if __name__ == '__main__':
    db = UserDBManager(USERS_DB_LOC)
    conf = load_config(db)
    server_url = load_server_url(db)
    if conf.get('bot_admin_id') and conf.get('bot_token_admin') and conf.get('bot_lang') and server_url:
        print("Config is already set!")
        print_current_conf(conf, server_url)
        print("Do you want to change config? (y/n): ")
        if input().lower() == "y":
            admin_ids, token, url, lang, client_token = set_by_user()
            set_config_in_db(db, admin_ids, token, url, lang, client_token)
            conf = load_config(db)
            server_url = load_server_url(db)
            set_config_variables(conf, server_url)
    else:
        admin_ids, token, url, lang, client_token = set_by_user()
        set_config_in_db(db, admin_ids, token, url, lang, client_token)
        conf = load_config(db)
        server_url = load_server_url(db)
    set_config_variables(conf, server_url)
    # close database connection
    db.close()

if os.getenv("SMARTKAMA_SKIP_CONFIG_AUTOLOAD") != "1":
    db = UserDBManager(USERS_DB_LOC)
    db.set_default_configs()
    conf = load_config(db)
    server_url = load_server_url(db)
    set_config_variables(conf, server_url)
    db.close()
