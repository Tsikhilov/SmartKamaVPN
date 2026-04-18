"""One-shot script: remove all 'velvet' references from the codebase."""
import re, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if '__file__' in dir() else os.getcwd()

# ---- replacements per file (order matters within each file) ----
# Pattern: (old_literal, new_literal)

# 1) messages.json  — key names only
MSG_JSON = os.path.join(ROOT, "UserBot", "Json", "messages.json")

# 2) markups.py — function names
MARKUPS = os.path.join(ROOT, "UserBot", "markups.py")

# 3) bot.py — the big one
BOT = os.path.join(ROOT, "UserBot", "bot.py")

# 4) reminder.py
REMINDER = os.path.join(ROOT, "Cronjob", "reminder.py")

# 5) scripts
CHECK_COV = os.path.join(ROOT, "scripts", "check_userbot_callback_coverage.py")
PROD_TOOLS = os.path.join(ROOT, "scripts", "prod_tools.ps1")
TEST_SUB = os.path.join(ROOT, "scripts", "_test_velvet_sub_page.py")
TEST_API = os.path.join(ROOT, "scripts", "_test_bot_api.py")

def replace_in_file(path, replacements, report=True):
    """replacements = list of (old, new) tuples."""
    if not os.path.exists(path):
        print(f"  SKIP (not found): {path}")
        return
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    original = content
    for old, new in replacements:
        content = content.replace(old, new)
    if content != original:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        if report:
            print(f"  UPDATED: {os.path.relpath(path, ROOT)}")
    else:
        if report:
            print(f"  NO CHANGE: {os.path.relpath(path, ROOT)}")


# ===== messages.json =====
print("\n=== messages.json ===")
replace_in_file(MSG_JSON, [
    ('"VELVET_', '"SK_'),
])

# ===== markups.py =====
print("\n=== markups.py ===")
replace_in_file(MARKUPS, [
    ('def velvet_vpn_subscriptions_markup', 'def sk_vpn_subscriptions_markup'),
    ('def velvet_renew_subscriptions_markup', 'def sk_renew_subscriptions_markup'),
    ('def velvet_subscription_actions_markup', 'def sk_subscription_actions_markup'),
    ('def velvet_setup_markup', 'def sk_setup_markup'),
    ('def velvet_devices_markup', 'def sk_devices_markup'),
    ('def velvet_lte_packages_markup', 'def sk_lte_packages_markup'),
    ('def velvet_referral_markup', 'def sk_referral_markup'),
    ('def velvet_params_markup', 'def sk_params_markup'),
    ('def velvet_help_markup', 'def sk_help_markup'),
    ('def velvet_about_markup', 'def sk_about_markup'),
])

# ===== bot.py =====
print("\n=== bot.py ===")
replace_in_file(BOT, [
    # Remove the compatibility remapping — replace the entire block
    ('    # Backward/forward compatibility for callback prefixes.\n'
     '    if key.startswith("smartkamavpn_"):\n'
     '        key = f"velvet_{key[len(\'smartkamavpn_\'):]}"',
     '    # Callback prefix (smartkamavpn_)'),

    # Function definitions
    ('def _prepare_velvet_devices_screen', 'def _prepare_sk_devices_screen'),
    ('def _send_velvet_main_menu', 'def _send_sk_main_menu'),
    ('def _send_velvet_vpn_menu', 'def _send_sk_vpn_menu'),

    # Function calls
    ('_prepare_velvet_devices_screen(', '_prepare_sk_devices_screen('),
    ('_send_velvet_main_menu(', '_send_sk_main_menu('),
    ('_send_velvet_vpn_menu(', '_send_sk_vpn_menu('),

    # Markup function calls (from markups.py)
    ('velvet_vpn_subscriptions_markup(', 'sk_vpn_subscriptions_markup('),
    ('velvet_renew_subscriptions_markup(', 'sk_renew_subscriptions_markup('),
    ('velvet_subscription_actions_markup(', 'sk_subscription_actions_markup('),
    ('velvet_setup_markup(', 'sk_setup_markup('),
    ('velvet_devices_markup(', 'sk_devices_markup('),
    ('velvet_lte_packages_markup(', 'sk_lte_packages_markup('),
    ('velvet_referral_markup(', 'sk_referral_markup('),
    ('velvet_params_markup(', 'sk_params_markup('),
    ('velvet_help_markup(', 'sk_help_markup('),
    ('velvet_about_markup(', 'sk_about_markup('),

    # Handler key comparisons: velvet_ → smartkamavpn_
    ('key == "velvet_title_menu"', 'key == "smartkamavpn_title_menu"'),
    ('key == "velvet_vpn_menu"', 'key == "smartkamavpn_vpn_menu"'),
    ('key == "velvet_renew_menu"', 'key == "smartkamavpn_renew_menu"'),
    ('key == "velvet_sub_open"', 'key == "smartkamavpn_sub_open"'),
    ('key == "velvet_setup"', 'key == "smartkamavpn_setup"'),
    ('key == "velvet_manual"', 'key == "smartkamavpn_manual"'),
    ('key == "velvet_support"', 'key == "smartkamavpn_support"'),
    ('key == "velvet_done"', 'key == "smartkamavpn_done"'),
    ('key == "velvet_sub_page"', 'key == "smartkamavpn_sub_page"'),
    ('key == "velvet_conf_happ"', 'key == "smartkamavpn_conf_happ"'),
    ('key == "velvet_params"', 'key == "smartkamavpn_params"'),
    ('key == "velvet_devices"', 'key == "smartkamavpn_devices"'),
    ('key in ("velvet_dev_block", "velvet_dev_del")', 'key in ("smartkamavpn_dev_block", "smartkamavpn_dev_del")'),
    ('key == "velvet_dev_block"', 'key == "smartkamavpn_dev_block"'),
    ('key == "velvet_lte"', 'key == "smartkamavpn_lte"'),
    ('key == "velvet_lte_buy"', 'key == "smartkamavpn_lte_buy"'),
    ('key == "velvet_buy_sub"', 'key == "smartkamavpn_buy_sub"'),
    ('key == "velvet_gift"', 'key == "smartkamavpn_gift"'),
    ('key == "velvet_gift_promo"', 'key == "smartkamavpn_gift_promo"'),
    ('key == "velvet_gift_subscription"', 'key == "smartkamavpn_gift_subscription"'),
    ('key == "velvet_gift_sub_pick"', 'key == "smartkamavpn_gift_sub_pick"'),
    ('key == "velvet_referral"', 'key == "smartkamavpn_referral"'),
    ('key == "velvet_copy_ref"', 'key == "smartkamavpn_copy_ref"'),
    ('key == "velvet_bought_gifts"', 'key == "smartkamavpn_bought_gifts"'),
    ('key == "velvet_info"', 'key == "smartkamavpn_info"'),

    # Message key references
    ("MESSAGES['VELVET_", "MESSAGES['SK_"),
    ('MESSAGES["VELVET_', 'MESSAGES["SK_'),
    ("MESSAGES.get('VELVET_", "MESSAGES.get('SK_"),

    # Comment
    ('# ----------------------------------- Velvet UI Area -----------------------------------',
     '# ----------------------------------- SmartKama UI Area -----------------------------------'),

    # Logging strings
    ('logging.warning("velvet_sub_page:', 'logging.warning("smartkamavpn_sub_page:'),
])

# ===== reminder.py =====
print("\n=== reminder.py ===")
replace_in_file(REMINDER, [
    ('callback_data=f"velvet_sub_open:{uuid}"', 'callback_data=f"smartkamavpn_sub_open:{uuid}"'),
])

# ===== scripts =====
print("\n=== scripts ===")
replace_in_file(CHECK_COV, [
    ('return "velvet_" + key[len("smartkamavpn_") :]',
     'return key  # already smartkamavpn_ prefix'),
])
replace_in_file(PROD_TOOLS, [
    ("'velvet_conf_happ'", "'smartkamavpn_conf_happ'"),
    ("'def velvet_params_markup'", "'def sk_params_markup'"),
    ('elif key == "velvet_conf_happ"', 'elif key == "smartkamavpn_conf_happ"'),
])
replace_in_file(TEST_SUB, [
    ('velvet_sub_page', 'smartkamavpn_sub_page'),
    ('Test velvet_sub_page', 'Test smartkamavpn_sub_page'),
])
replace_in_file(TEST_API, [
    ('velvet_sub_page', 'smartkamavpn_sub_page'),
])

# ===== Final check =====
print("\n=== Final grep for remaining 'velvet' ===")
import subprocess
result = subprocess.run(
    ['grep', '-rni', 'velvet', '--include=*.py', '--include=*.json', '--include=*.ps1', '--include=*.md', ROOT],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
if result.stdout.strip():
    lines = result.stdout.strip().split('\n')
    print(f"Found {len(lines)} remaining references:")
    for line in lines:
        print(f"  {line}")
else:
    print("No remaining 'velvet' references found!")

print("\nDone.")
