# Description: API for connecting to the panel
import json
import logging
import os
import random
import subprocess
import string
from io import BytesIO
import re
from datetime import datetime
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from Database.dbManager import USERS_DB
import psutil
import qrcode
import requests
from config import PANEL_URL, BACKUP_LOC, CLIENT_TOKEN, USERS_DB_LOC,RECEIPTIONS_LOC,BOT_BACKUP_LOC, API_PATH,LOG_DIR, THREEXUI_REALITY_PUBLIC_KEY
import AdminBot.templates
from Utils import api
from version import __version__
import zipfile
import shutil
# Global variables
# Make Session for requests
session = requests.session()
# Base panel URL - example: https://panel.example.com
BASE_URL = urlparse(PANEL_URL).scheme + "://" + urlparse(PANEL_URL).netloc
_CLIENT_PROXY_PATH_CACHE = None


def _discover_client_proxy_path_from_hiddify():
    app_cfg = "/opt/hiddify-manager/hiddify-panel/app.cfg"
    if not os.path.exists(app_cfg):
        return None

    try:
        with open(app_cfg, "r", encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"SQLALCHEMY_DATABASE_URI\s*=\s*'([^']+)'", text)
        if not m:
            return None

        uri = m.group(1)
        m2 = re.search(r"mysql\+mysqldb://([^:]+):([^@]+)@([^/]+)/([^?]+)", uri)
        if not m2:
            return None

        db_user, db_pass, db_host, db_name = m2.groups()
        cmd = [
            "mysql",
            "-N",
            f"-u{db_user}",
            f"-p{db_pass}",
            "-h",
            db_host,
            "-D",
            db_name,
            "-e",
            "SELECT value FROM str_config WHERE `key`='proxy_path_client' LIMIT 1;",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            return None
        value = (proc.stdout or "").strip()
        return value.strip("/") if value else None
    except Exception:
        return None


def _resolve_client_proxy_path(default_path):
    global _CLIENT_PROXY_PATH_CACHE
    if _CLIENT_PROXY_PATH_CACHE:
        return _CLIENT_PROXY_PATH_CACHE

    try:
        cfg = USERS_DB.find_str_config(key='hiddify_client_proxy_path')
        if cfg and cfg[0].get('value'):
            _CLIENT_PROXY_PATH_CACHE = str(cfg[0]['value']).strip("/")
            return _CLIENT_PROXY_PATH_CACHE
    except Exception:
        pass

    env_path = os.getenv("HIDDIFY_CLIENT_PROXY_PATH")
    if env_path:
        _CLIENT_PROXY_PATH_CACHE = env_path.strip("/")
        return _CLIENT_PROXY_PATH_CACHE

    discovered = _discover_client_proxy_path_from_hiddify()
    if discovered:
        _CLIENT_PROXY_PATH_CACHE = discovered
        try:
            if USERS_DB.find_str_config(key='hiddify_client_proxy_path'):
                USERS_DB.edit_str_config('hiddify_client_proxy_path', value=discovered)
            else:
                USERS_DB.add_str_config('hiddify_client_proxy_path', discovered)
        except Exception:
            pass
        return _CLIENT_PROXY_PATH_CACHE

    _CLIENT_PROXY_PATH_CACHE = default_path.strip("/") if default_path else default_path
    return _CLIENT_PROXY_PATH_CACHE


# Users directory in panel
# USERS_DIR = "/admin/user/"


# Get request - return request object
def get_request(url):
    logging.info(f"GET Request to {privacy_friendly_logging_request(url)}")
    global session
    try:
        req = session.get(url)
        logging.info(f"GET Request to {privacy_friendly_logging_request(url)} - Status Code: {req.status_code}")
        return req
    except requests.exceptions.ConnectionError as e:
        logging.exception(f"Connection Exception: {e}")
        return False
    except requests.exceptions.Timeout as e:
        logging.exception(f"Timeout Exception: {e}")
        return False
    except requests.exceptions.RequestException as e:
        logging.exception(f"General Connection Exception: {e}")
        return False
    except Exception as e:
        logging.exception(f"General Exception: {e}")
        return False


# Post request - return request object
def post_request(url, data):
    logging.info(f"POST Request to {privacy_friendly_logging_request(url)} - Data: {data}")
    global session
    try:
        req = session.post(url, data=data)
        return req
    except requests.exceptions.ConnectionError as e:
        logging.exception(f"Connection Exception: {e}")
        return False
    except requests.exceptions.Timeout as e:
        logging.exception(f"Timeout Exception: {e}")
        return False
    except requests.exceptions.RequestException as e:
        logging.exception(f"General Connection Exception: {e}")
        return False
    except Exception as e:
        logging.exception(f"General Exception: {e}")
        return False


def users_to_dict(users_dict):
    if not users_dict:
        return False
    users_array = []
    for user in users_dict:
        users_array.append({'uuid': user['uuid'], 'name': user['name'], 'last_online': user['last_online'],
                            'expiry_time': None,
                            'usage_limit_GB': user['usage_limit_GB'], 'package_days': user['package_days'],
                            'mode': user['mode'],
                            'monthly': None, 'start_date': user['start_date'],
                            'current_usage_GB': user['current_usage_GB'],
                            'last_reset_time': user['last_reset_time'], 'comment': user['comment'],
                            'telegram_id': user['telegram_id'],
                            'added_by': user.get('added_by_uuid', user.get('added_by')), 'max_ips': None, 'enable': None})
    return users_array


# Change telegram user data format
def Telegram_users_to_dict(Tel_users_dict):
    if not Tel_users_dict:
        return False
    users_array = []
    for user in Tel_users_dict:
        users_array.append({'id': user[0], 'telegram_id': user[1], 'created_at': user[3], })
    return users_array


# Calculate remaining days
def calculate_remaining_days(start_date, package_days):
    import datetime
    import pytz

    datetime_iran = datetime.datetime.now(pytz.timezone('Asia/Tehran'))
    datetime_iran = datetime_iran.replace(tzinfo=None)
    if start_date is None:
        return package_days
    start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    # remaining_days = package_days - (datetime.datetime.now() - start_date).days
    remaining_days = package_days - (datetime_iran - start_date).days + 1
    if remaining_days < 0:
        return 0
    return remaining_days


# Calculate remaining usage
def calculate_remaining_usage(usage_limit_GB, current_usage_GB):
    remaining_usage = usage_limit_GB - current_usage_GB
    return round(remaining_usage, 2)


# Calculate last online time
def calculate_remaining_last_online(last_online_date_time):
    import datetime
    if last_online_date_time == "0001-01-01 00:00:00.000000" or last_online_date_time == "1-01-01 00:00:00":
        return AdminBot.content.MESSAGES['NEVER']
    # last_online_date_time = datetime.datetime.strptime(last_online_date_time, "%Y-%m-%d %H:%M:%S.%f")
    last_online_date_time = datetime.datetime.strptime(last_online_date_time, "%Y-%m-%d %H:%M:%S")
    last_online_time = (datetime.datetime.now() - last_online_date_time)
    last_online = AdminBot.templates.last_online_time_template(last_online_time)
    return last_online


# Process users data - return list of users
def dict_process(url, users_dict, sub_id=None, server_id=None):
    BASE_URL = urlparse(url,).scheme + "://" + urlparse(url,).netloc
    panel_parts = [p for p in urlparse(url).path.split('/') if p]
    panel_path = panel_parts[0] if panel_parts else None
    sub_domain = os.getenv("SMARTKAMA_SUB_DOMAIN", "sub.smartkama.ru").strip() or "sub.smartkama.ru"
    sub_port = int(os.getenv("SMARTKAMA_SUB_PORT", "443"))
    logging.info(f"Parse users page")
    if not users_dict:
        return False
    users_list = []
    for user in users_dict:
        user_sub_id = str(user.get('sub_id') or '')[:8]
        if not user_sub_id:
            user_sub_id = str(user['uuid'])[:8]

        if panel_path:
            panel_user_link = f"{BASE_URL}/{panel_path}/{user['uuid']}/"
        else:
            panel_user_link = f"{BASE_URL}/{user['uuid']}/"

        # User-facing click-through should open subscription URL, not panel API endpoint.
        _sub_port_sfx = f":{sub_port}" if sub_port not in (443, 0) else ""
        user_link = f"https://{sub_domain}{_sub_port_sfx}/{user_sub_id}"

        users_list.append({
            "name": user['name'],
            "usage": {
                'usage_limit_GB': round(user['usage_limit_GB'], 2),
                'current_usage_GB': round(user['current_usage_GB'], 2),
                'remaining_usage_GB': calculate_remaining_usage(user['usage_limit_GB'], user['current_usage_GB'])
            },
            "remaining_day": calculate_remaining_days(user['start_date'], user['package_days']),
            "comment": user['comment'],
            "last_connection": calculate_remaining_last_online(user['last_online']) if user['last_online'] else None,
            "uuid": user['uuid'],
            "link": user_link,
            "panel_link": panel_user_link,
            "mode": user['mode'],
            "enable": user['enable'],
            "sub_id": sub_id,
            "server_id": server_id
        })

    return users_list


# Get single user info - return dict of user info
def user_info(url, uuid):
    logging.info(f"Get info of user single user - {uuid}")
    lu = api.select(url)
    if not lu:
        return False
    for user in lu:
        if user['uuid'] == uuid:
            return user
    return False


# Get sub links - return dict of sub links (3x-ui format)
def sub_links(uuid, url=None):
    from config import THREEXUI_PANEL_URL
    SUB_DOMAIN = os.getenv("SMARTKAMA_SUB_DOMAIN", "sub.smartkama.ru").strip() or "sub.smartkama.ru"
    SUB_PORT = int(os.getenv("SMARTKAMA_SUB_PORT", "443"))
    _port_sfx = f":{SUB_PORT}" if SUB_PORT not in (443, 0) else ""

    # Попробуем получить subId клиента через API
    try:
        client_data = api.find(url=THREEXUI_PANEL_URL, uuid=uuid)
        sub_id = (client_data.get("sub_id") or "") if client_data else ""
    except Exception:
        sub_id = ""

    # Если subId не получен — используем первые 8 символов UUID (наш шаблон создания)
    if not sub_id:
        sub_id = str(uuid)[:8]

    # Raw endpoint used for import/parsing in bot internals.
    sub_base_raw = f"https://{SUB_DOMAIN}{_port_sfx}/sub/{sub_id}"
    # Public pretty link used in user-facing messages.
    public_sub_link = f"https://{SUB_DOMAIN}{_port_sfx}/{sub_id}"

    sub = {
        'sub_link':         sub_base_raw,
        'sub_link_b64':     sub_base_raw,
        'sub_link_auto':    sub_base_raw,
        'sub_link_raw':     sub_base_raw,
        'sub_link_auto_raw': sub_base_raw,
        'public_sub_link':  public_sub_link,
        'clash_configs':    f"{sub_base_raw}?config=clash",
        'hiddify_configs':  sub_base_raw,
        'sing_box':         sub_base_raw,
        'sing_box_full':    sub_base_raw,
        'home_link':        sub_base_raw,
        'public_home_link': public_sub_link,
    }
    return sub


# Parse sub links
def sub_parse(sub):
    logging.info(f"Parse sub links")
    res = get_request(sub)
    if not res or res.status_code != 200:
        return False

    body = (res.text or "").strip()
    if "vless://" not in body and "vmess://" not in body and "trojan://" not in body:
        try:
            import base64
            padded = body + "=" * ((4 - len(body) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if decoded:
                body = decoded
        except Exception:
            pass

    urls = re.findall(r'(vless:\/\/[^\n]+)|(vmess:\/\/[^\n]+)|(trojan:\/\/[^\n]+)', body)

    def _normalize_vless_url(vless_url):
        if not vless_url or not vless_url.startswith("vless://"):
            return vless_url

        base = vless_url
        fragment = ""
        if "#" in vless_url:
            base, fragment = vless_url.split("#", 1)

        parsed = urlsplit(base)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if "encryption" not in params:
            params["encryption"] = "none"

        # Keep browser fingerprint explicit for all VLESS transports.
        params["fp"] = "chrome"

        if (params.get("security") or "").lower() == "reality":
            if "pbk" not in params and THREEXUI_REALITY_PUBLIC_KEY:
                params["pbk"] = THREEXUI_REALITY_PUBLIC_KEY

        normalized = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(params, doseq=True),
            ""
        ))
        if fragment:
            normalized = f"{normalized}#{fragment}"
        return normalized

    def _normalize_trojan_url(trojan_url):
        if not trojan_url or not trojan_url.startswith("trojan://"):
            return trojan_url

        base = trojan_url
        fragment = ""
        if "#" in trojan_url:
            base, fragment = trojan_url.split("#", 1)

        parsed = urlsplit(base)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params["fp"] = "chrome"

        normalized = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(params, doseq=True),
            ""
        ))
        if fragment:
            normalized = f"{normalized}#{fragment}"
        return normalized

    def _normalize_vmess_url(vmess_url):
        if not vmess_url or not vmess_url.startswith("vmess://"):
            return vmess_url
        try:
            payload = vmess_url.replace("vmess://", "", 1)
            cfg = base64decoder(payload)
            if not cfg:
                return vmess_url
            cfg["fp"] = "chrome"
            encoded = base64.b64encode(
                json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("utf-8")
            return f"vmess://{encoded}"
        except Exception:
            return vmess_url

    config_links = {
        'vless': [],
        'vmess': [],
        'trojan': []
    }
    for url in urls:
        if url[0]:
            normalized_vless = _normalize_vless_url(url[0])
            match = re.search(r'#(.+)$', normalized_vless)
            if match:
                vless_title = match.group(1).replace("%20", " ")
                config_links['vless'].append([normalized_vless, vless_title])
        elif url[1]:
            normalized_vmess = _normalize_vmess_url(url[1])
            config = normalized_vmess.replace("vmess://", "")
            config_parsed = base64decoder(config)
            if config_parsed:
                vmess_title = config_parsed['ps'].replace("%20", " ")
                config_links['vmess'].append([normalized_vmess, vmess_title])
        elif url[2]:
            normalized_trojan = _normalize_trojan_url(url[2])
            match = re.search(r'#(.+)$', normalized_trojan)
            if match:
                trojan_title = match.group(1).replace("%20", " ")
                trojan_sni = re.search(r'sni=([^&]+)', normalized_trojan)
                if trojan_sni:
                    if trojan_sni.group(1) == "fake_ip_for_sub_link":
                        continue
                config_links['trojan'].append([normalized_trojan, match.group(1)])

    config_links['vless'].sort(
        key=lambda item: (
            0 if ("type=ws" in item[0] and "security=tls" in item[0]) else
            1 if "security=reality" in item[0] else
            2
        )
    )
        
    return config_links


# Backup panel
def backup_panel(url):
    logging.info(f"Backup panel")
    BASE_URL = urlparse(url,).scheme + "://" + urlparse(url,).netloc
    dir_panel = urlparse(url).path.split('/')
    backup_url = f"{BASE_URL}/{dir_panel[1]}/{dir_panel[2]}/admin/backup/backupfile/"

    backup_req = get_request(backup_url)
    if not backup_req or backup_req.status_code != 200:
        return False

    now = datetime.now()
    dt_string = now.strftime("%d-%m-%Y_%H-%M-%S")

    file_name = f"Backup_{urlparse(url,).netloc}_{dt_string}.json"

    file_name = os.path.join(BACKUP_LOC, file_name)
    if not os.path.exists(BACKUP_LOC):
        os.makedirs(BACKUP_LOC)
    with open(file_name, 'w+') as f:
        f.write(backup_req.text)
    return file_name

# zip an array of files
def zip_files(files, zip_file_name,path=None):
    if path:
        zip_file_name = os.path.join(path, zip_file_name)
    with zipfile.ZipFile(zip_file_name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            # Get the base name of the file (i.e. the file name without the parent folders)
            base_name = os.path.basename(file)
            # Write the file to the zip archive with the base name as the arcname
            zip_file.write(file, arcname=base_name)
    return zip_file_name

# full backup
def full_backup():
    files = []
    servers = USERS_DB.select_servers()
    for server in servers:
        file_name = backup_panel(server['url'])
        if file_name:
            files.append(file_name)
    backup_bot = backup_json_bot()
    if backup_bot:
        files.append(backup_bot)
    if files:
        now = datetime.now()
        dt_string = now.strftime("%d-%m-%Y_%H-%M-%S")
        zip_title = f"Backup_{dt_string}.zip"
        zip_file_name = zip_files(files, zip_title,path=BACKUP_LOC)
        if zip_file_name:
            return zip_file_name
    return False

# Extract UUID from config
def extract_uuid_from_config(config):
    logging.info(f"Extract UUID from config")
    uuid_pattern = r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})"
    match = re.search(uuid_pattern, config)

    if match:
        uuid = match.group(1)
        return uuid
    else:
        return None


# System status
def system_status():
    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent
    return {
        'cpu': cpu_usage,
        'ram': ram_usage,
        'disk': disk_usage
    }


# Search user by name
def search_user_by_name(url, name):
    # users = dict_process(users_to_dict(ADMIN_DB.select_users()))
    users = api.select(url)
    if not users:
        return False
    res = []
    for user in users:
        if name.lower() in user['name'].lower():
            res.append(user)
    if res:
        return res
    return False


# Search user by uuid
def search_user_by_uuid(url, uuid):
    # users = dict_process(users_to_dict(ADMIN_DB.select_users()))
    users = api.select(url)
    if not users:
        return False
    for user in users:
        if user['uuid'] == uuid:
            return user
    return False


# Base64 decoder
def base64decoder(s):
    import base64
    try:
        conf = base64.b64decode(s).decode("utf-8")
        conf = json.loads(conf)
    except Exception as e:
        conf = False

    return conf


# Search user by config
def search_user_by_config(url, config):
    if config.startswith("vmess://"):
        config = config.replace("vmess://", "")
        config = base64decoder(config)
        if config:
            uuid = config['id']
            user = search_user_by_uuid(url, uuid)
            if user:
                return user
    uuid = extract_uuid_from_config(config)
    if uuid:
        user = search_user_by_uuid(url, uuid)
        if user:
            return user
    return False


# Check text is it config or sub
def is_it_config_or_sub(config):
    if config.startswith("vmess://"):
        config = config.replace("vmess://", "")
        config = base64decoder(config)
        if config:
            return config['id']
    uuid = extract_uuid_from_config(config)
    if uuid:
        return uuid


# Users bot add plan
def users_bot_add_plan(size, days, price, server_id,description=None):
    if not CLIENT_TOKEN:
        return False
    # randon 4 digit number
    plan_id = random.randint(10000, 99999)
    plan_status = USERS_DB.add_plan(plan_id, size, days, price, server_id,description=description)
    if not plan_status:
        return False
    return True

#--------------------------Server area ----------------------------
# add server
def add_server(url, user_limit, title=None, description=None, status=True, default_server=False):
    # randon 5 digit number
    #server_id = random.randint(10000, 99999)
    server_status = USERS_DB.add_server(url, user_limit, title, description, status, default_server)
    if not server_status:
        return False
    return True


# Check user is expired
def is_user_expired(user):
    if user['remaining_day'] == 0:
        return True
    return False


# Expired users list
def expired_users_list(users):
    expired_users = []
    for user in users:
        if is_user_expired(user):
            expired_users.append(user)
    return expired_users


# Text to QR code
def txt_to_qr(txt):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=1,
    )
    qr.add_data(txt)
    qr.make(fit=True, )
    img_qr = qr.make_image(fill_color="black", back_color="white")
    stream = BytesIO()
    img_qr.save(stream)
    img = stream.getvalue()

    return img


# List of users who not ordered from bot (Link Subscription)
def non_order_user_info(telegram_id):
    users_list = []
    non_ordered_subscriptions = USERS_DB.find_non_order_subscription(telegram_id=telegram_id)
    if non_ordered_subscriptions:
        for subscription in non_ordered_subscriptions:
            server_id = subscription['server_id']
            server = USERS_DB.find_server(id=server_id)
            if server:
                server = server[0]
                #if server['status']:
                URL = server['url'] + API_PATH
                non_order_user = api.find(URL, subscription['uuid'])
                if non_order_user:
                    non_order_user = users_to_dict([non_order_user])
                    non_order_user = dict_process(URL, non_order_user, subscription['id'],server_id)
                    if non_order_user:
                        non_order_user = non_order_user[0]
                        users_list.append(non_order_user)
    return users_list


# List of users who ordered from bot and made payment
def order_user_info(telegram_id):
    users_list = []
    orders = USERS_DB.find_order(telegram_id=telegram_id)
    if orders:
        for order in orders:
            ordered_subscriptions = USERS_DB.find_order_subscription(order_id=order['id'])
            if ordered_subscriptions:
                for subscription in ordered_subscriptions:
                    server_id = subscription['server_id']
                    server = USERS_DB.find_server(id=server_id)
                    if server:
                        server = server[0]
                        #if server['status']:
                        URL = server['url'] + API_PATH
                        order_user = api.find(URL, subscription['uuid'])
                        if order_user:
                            order_user = users_to_dict([order_user])
                            order_user = dict_process(URL, order_user, subscription['id'], server_id)
                            if order_user:
                                order_user = order_user[0]
                                users_list.append(order_user)
    return users_list



# Replace last three characters of a string with random numbers (For Payment)
def replace_last_three_with_random(input_string):
    if len(input_string) <= 3:
        return input_string  # Not enough characters to replace
    input_string = int(input_string)
    input_string += random.randint(1000, 9999)
    return str(input_string)
    # random_numbers = ''.join(random.choice(string.digits) for _ in range(3))
    # modified_string = input_string[:-3] + random_numbers
    # return modified_string


# Privacy-friendly logging - replace your panel url with panel.private.com
def privacy_friendly_logging_request(url):
    url = urlparse(url)
    url = url.scheme + "://" + "panel.private.com" + url.path
    return url


def all_configs_settings():
    bool_configs = USERS_DB.select_bool_config()
    int_configs = USERS_DB.select_int_config()
    str_configs = USERS_DB.select_str_config()

    # all configs to one dict
    all_configs = {}
    for config in bool_configs:
        all_configs[config['key']] = config['value']
    for config in int_configs:
        all_configs[config['key']] = config['value']
    for config in str_configs:
        all_configs[config['key']] = config['value']

    # Add YooKassa settings if available
    yookassa_shop = USERS_DB.find_str_config(key="yookassa_shop_id")
    yookassa_secret = USERS_DB.find_str_config(key="yookassa_secret_key")
    if yookassa_shop:
        all_configs['yookassa_shop_id'] = yookassa_shop[0]['value']
    if yookassa_secret:
        all_configs['yookassa_secret_key'] = yookassa_secret[0]['value']

    # Safe defaults for optional payment settings on legacy databases.
    all_configs.setdefault('payment_method_card_enabled', 1)
    all_configs.setdefault('payment_method_yookassa_enabled', 1)
    all_configs.setdefault('payment_method_pally_enabled', 0)
    all_configs.setdefault('pally_payment_url', None)

    return all_configs


def find_order_subscription_by_uuid(uuid):
    order_subscription = USERS_DB.find_order_subscription(uuid=uuid)
    non_order_subscription = USERS_DB.find_non_order_subscription(uuid=uuid)
    if order_subscription:
        return order_subscription[0]
    elif non_order_subscription:
        return non_order_subscription[0]
    else:
        return False
    
def is_it_subscription_by_uuid_and_telegram_id(uuid, telegram_id):
    subs = []
    flag = False
    orders = USERS_DB.find_order(telegram_id=telegram_id)
    if orders:
        for order in orders:
            ordered_subscriptions = USERS_DB.find_order_subscription(order_id=order['id'])
            if ordered_subscriptions:
                for subscription in ordered_subscriptions:
                    if subscription['uuid'] == uuid:
                        flag = True
                        subs.append(subscription)
                        break
            if flag == True:
                break 
    
    non_order_subscriptions = USERS_DB.find_non_order_subscription(telegram_id=telegram_id)
    if non_order_subscriptions:
        for subscription in non_order_subscriptions:
            if subscription['uuid'] == uuid:
                subs.append(subscription)
                break
    if subs:
        return True
    else:
        return False


def toman_to_rial(toman):
    return int(toman) * 10


def rial_to_toman(rial):
    return "{:,.0f}".format(int(int(rial) / 10))


def backup_json_bot():
    back_dir = BOT_BACKUP_LOC
    if not os.path.exists(back_dir):
        os.makedirs(back_dir)
    bk_json_data = USERS_DB.backup_to_json(back_dir)
    if not bk_json_data:
        return False
    bk_json_data['version'] = __version__
    now = datetime.now()
    dt_string = now.strftime("%d-%m-%Y_%H-%M-%S")
    bk_json_file = os.path.join(back_dir, f"Backup_Bot_{dt_string}.json")
    with open(bk_json_file, 'w+') as f:
        json.dump(bk_json_data, f, indent=4)
    zip_file = os.path.join(back_dir, f"Backup_Bot_{dt_string}.zip")
    with zipfile.ZipFile(zip_file, 'w') as zip:
        zip.write(bk_json_file,os.path.basename(bk_json_file))
        zip.write(USERS_DB_LOC,os.path.basename(USERS_DB_LOC))
        for file in os.listdir(RECEIPTIONS_LOC):
            zip.write(os.path.join(RECEIPTIONS_LOC, file),os.path.join(os.path.basename(RECEIPTIONS_LOC),file))
    os.remove(bk_json_file)
    return zip_file


def restore_json_bot(file):
    extract_path = os.path.join(BOT_BACKUP_LOC, "tmp", os.path.basename(file).replace(".zip", ""))
    if not os.path.exists(file):
        return False
    if not file.endswith(".zip"):
        return False
    try:
        if not os.path.exists(extract_path):
            os.makedirs(extract_path)
    except Exception as e:
        logging.exception(f"Exception: {e}")
        return False
    try:
        with zipfile.ZipFile(file, 'r') as outer_zip:
            # Iterate through all entries in the outer zip
            for entry in outer_zip.namelist():
                # Check if the entry is a zip file
                if entry.lower().endswith('.zip'):
                    nested_zip_filename = entry
                    # Extract the nested zip file
                    with outer_zip.open(nested_zip_filename) as nested_zip_file:
                        # Save the nested zip file to a temporary location
                        nested_zip_path = os.path.join(extract_path, nested_zip_filename)
                        with open(nested_zip_path, 'wb') as f:
                            f.write(nested_zip_file.read())

                        # Extract contents of the nested zip file
                        with zipfile.ZipFile(nested_zip_path, 'r') as nested_zip:
                            # Check if the JSON file exists
                            # select json file
                            json_filename = None
                            for file in nested_zip.namelist():
                                if file.endswith('.json'):
                                    json_filename = file
                                    break
                            if json_filename in nested_zip.namelist():
                                nested_zip.extractall(extract_path)
                else:
                            json_filename = None
                            for file in outer_zip.namelist():
                                if file.endswith('.json'):
                                    json_filename = file
                                    break
                            if json_filename in outer_zip.namelist():
                                outer_zip.extractall(extract_path)
    except Exception as e:
        logging.exception(f"Exception: {e}")
        return False
                
            
    bk_json_file = os.path.join(extract_path, os.path.basename(json_filename))
    # with open(bk_json_file, 'r') as f:
    #     bk_json_data = json.load(f)
    status_db = USERS_DB.restore_from_json(bk_json_file)
    if not status_db:
        return False
    if not os.path.exists(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC))):
        os.mkdir(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC)))
    # move reception files
    for file in os.listdir(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC))):
        try:
            os.rename(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC), file),
                    os.path.join(RECEIPTIONS_LOC, file))
        except Exception as e:
            logging.exception(f"Exception: {e}")
    try:
        # # remove tmp folder
        # os.remove(bk_json_file)
        # # remove RECEIPTIONS all files
        # for file in os.listdir(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC))):
        #     os.remove(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC), file))
        # os.rmdir(os.path.join(extract_path, os.path.basename(RECEIPTIONS_LOC)))
        # # romove hidyBot.db
        # os.remove(os.path.join(extract_path, os.path.basename(USERS_DB_LOC)))
        shutil.rmtree(extract_path)
    except Exception as e:
        logging.exception(f"Exception: {e}")
        return False
    return True

# Debug Data 
def debug_data():
    bk_json_data = USERS_DB.backup_to_json(BOT_BACKUP_LOC)
    if not bk_json_data:
        return False
    
    bk_json_data['version'] = __version__
    
    new_servers = []
    for server in bk_json_data['servers']:
        url = privacy_friendly_logging_request(server['url'])
        server['url'] = url
        new_servers.append(server)
    bk_json_data['servers'] = new_servers
    
    bk_json_data['str_config'] = [x for x in bk_json_data['str_config'] if x['key'] not in ['bot_token_admin','bot_token_client']]
    
    now = datetime.now()
    dt_string = now.strftime("%d-%m-%Y_%H-%M-%S")
    bk_json_file = os.path.join(BOT_BACKUP_LOC, f"DB_Data_{dt_string}.json")
    with open(bk_json_file, 'w+') as f:
        json.dump(bk_json_data, f, indent=4)
    zip_file = os.path.join(BOT_BACKUP_LOC, f"Debug_Data_{dt_string}.zip")
    with zipfile.ZipFile(zip_file, 'w') as zip:
        zip.write(bk_json_file,os.path.basename(bk_json_file))
        if os.path.exists(os.path.join(os.getcwd(),"bot.log")):
            # only send last 1000 lines of log
            with open(os.path.join(os.getcwd(),"bot.log"), 'r') as f:
                lines = f.readlines()
                lines = lines[-1000:]
                with open(os.path.join(os.getcwd(),"bot.log"), 'w') as f:
                    f.writelines(lines)
            zip.write("bot.log",os.path.basename("bot.log"))
        if os.path.exists(LOG_DIR):
            for file in os.listdir(LOG_DIR):
                zip.write(os.path.join(LOG_DIR, file),file)

    os.remove(bk_json_file)
    return zip_file
    