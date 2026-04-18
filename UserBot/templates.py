# Description: This file contains all the templates used in the bot.
from config import LANG
from UserBot.content import MESSAGES
from Utils.utils import rial_to_toman, toman_to_rial,all_configs_settings
from Database.dbManager import USERS_DB
# User Subscription Info Template
def user_info_template(sub_id, server, usr, header=""):
    settings = USERS_DB.find_bool_config(key='visible_hiddify_hyperlink')
    if settings:
        settings = settings[0]
        if settings['value']:
            user_name = f"<a href='{usr['link']}'> {usr['name']} </a>"
        else:
            user_name = usr['name']
    else:
        user_name = usr['name']
    # if usr['enable'] == 1:
    #     status = MESSAGES['ACTIVE_SUBSCRIPTION_STATUS']
    # else:
    #     status = MESSAGES['DEACTIVE_SUBSCRIPTION_STATUS']
    return f"""
{header}

{MESSAGES['USER_NAME']} {user_name}
{MESSAGES['SERVER']} {server['title']}
{MESSAGES['INFO_USAGE']} {usr['usage']['current_usage_GB']} {MESSAGES['OF']} {usr['usage']['usage_limit_GB']} {MESSAGES['GB']}
{MESSAGES['INFO_REMAINING_DAYS']} {usr['remaining_day']} {MESSAGES['DAY_EXPIRE']}
{MESSAGES['INFO_ID']} <code>{sub_id}</code>
"""
# {MESSAGES['SUBSCRIPTION_STATUS']} {status}

# Wallet Info Template
def wallet_info_template(balance):
    if balance == 0:
        return MESSAGES['ZERO_BALANCE']
    else:
        return f"""
         {MESSAGES['WALLET_INFO_PART_1']} {rial_to_toman(balance)} {MESSAGES['WALLET_INFO_PART_2']}
         """


# Plan Info Template
def plan_info_template(plan, header=""):
    msg = f"""
{header}
{MESSAGES['PLAN_INFO']}

{MESSAGES['PLAN_INFO_SIZE']} {plan['size_gb']} {MESSAGES['GB']}
{MESSAGES['PLAN_INFO_DAYS']} {plan['days']} {MESSAGES['DAY_EXPIRE']}
{MESSAGES['PLAN_INFO_PRICE']} {rial_to_toman(plan['price'])} {MESSAGES['TOMAN']}
"""
    if plan['description']:
        msg += f"""{MESSAGES['PLAN_INFO_DESC']} {plan['description']}"""
    return msg
    

# Owner Info Template (For Payment)
def owner_info_template(card_number, card_holder_name, price, header=""):
    card_number = card_number if card_number else "-"
    card_holder_name = card_holder_name if card_holder_name else "-"

    if LANG in ('RU', 'EN'):
        return (
            f"{header}\n\n"
            f"💰 Переведите ровно: <code>{rial_to_toman(price)}</code> ₽\n"
            f"💳 На карту: <code>{card_number}</code>\n"
            f"👤 Получатель: <b>{card_holder_name}</b>\n\n"
            f"❗️После оплаты отправьте скриншот чека."
        )
    elif LANG == 'FA':
        return f"""
{header}

💰لطفا دقیقا مبلغ: <code>{price}</code> {MESSAGES['RIAL']}
💴معادل: {rial_to_toman(price)} {MESSAGES['TOMAN']}
💳را به شماره کارت: <code>{card_number}</code>
👤به نام <b>{card_holder_name}</b> واریز کنید.

❗️بعد از واریز مبلغ، اسکرین شات از تراکنش را برای ما ارسال کنید.
"""
    return f"{header}\nPay {rial_to_toman(price)}₽ → card {card_number} ({card_holder_name})"


# Payment Received Template - Send to Admin
def payment_received_template(payment, user, header="", footer=""):
    username = f"@{user['username']}" if user['username'] else MESSAGES['NOT_SET']
    name = user['full_name'] if user['full_name'] else user['telegram_id']

    if LANG in ('RU', 'EN'):
        return (
            f"{header}\n\n"
            f"💳 Платёж #{payment['id']}\n"
            f"💰 Сумма: <b>{rial_to_toman(payment['payment_amount'])}</b> ₽\n"
            f"{MESSAGES['INFO_USER_NAME']} <b>{name}</b>\n"
            f"{MESSAGES['INFO_USER_USERNAME']} {username}\n"
            f"{MESSAGES['INFO_USER_NUM_ID']} {user['telegram_id']}\n"
            f"─────────────────\n"
            f"⬇️ Запрос на пополнение кошелька ⬇️\n\n"
            f"{footer}"
        )
    elif LANG == 'FA':
        return f"""
{header}

شناسه تراکنش: <code>{payment['id']}</code>
مبلغ تراکنش: <b>{rial_to_toman(payment['payment_amount'])}</b> {MESSAGES['TOMAN']}
{MESSAGES['INFO_USER_NAME']} <b>{name}</b>
{MESSAGES['INFO_USER_USERNAME']} {username}
{MESSAGES['INFO_USER_NUM_ID']} {user['telegram_id']}
---------------------
⬇️درخواست افزایش موجودی کیف پول⬇️

{footer}
"""
    return f"{header}\nPayment #{payment['id']} | {rial_to_toman(payment['payment_amount'])}₽ | {name}\n{footer}"


# Help Guide Template
def connection_help_template(header=""):
    if LANG == 'RU':
        return (
            f"{header}\n\n"
            "⭕️ Приложения для подключения к VPN\n\n"
            "📥 Android:\n"
            "<a href='https://play.google.com/store/apps/details?id=com.v2ray.ang'>V2RayNG</a>\n"
            "<a href='https://play.google.com/store/apps/details?id=ang.hiddify.com'>HiddifyNG</a>\n\n"
            "📥 iOS:\n"
            "<a href='https://apps.apple.com/us/app/streisand/id6450534064'>Streisand</a>\n"
            "<a href='https://apps.apple.com/us/app/foxray/id6448898396'>Foxray</a>\n"
            "<a href='https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690'>V2box</a>\n\n"
            "📥 Windows:\n"
            "<a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray</a>\n"
            "<a href='https://github.com/2dust/v2rayN/releases'>V2rayN</a>\n"
            "<a href='https://github.com/hiddify/HiddifyN/releases'>HiddifyN</a>\n\n"
            "📥 Mac и Linux:\n"
            "<a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray</a>"
        )
    elif LANG == 'FA':
        return f"""
{header}

⭕️ نرم افزار های مورد نیاز برای اتصال به کانفیگ
    
📥اندروید:
<a href='https://play.google.com/store/apps/details?id=com.v2ray.ang'>V2RayNG</a>
<a href='https://play.google.com/store/apps/details?id=ang.hiddify.com'>HiddifyNG</a>

📥آی او اس:
<a href='https://apps.apple.com/us/app/streisand/id6450534064'>Streisand</a>
<a href='https://apps.apple.com/us/app/foxray/id6448898396'>Foxray</a>
<a href='https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690'>V2box</a>

📥ویندوز:
<a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray</a>
<a href='https://github.com/2dust/v2rayN/releases'>V2rayN</a>
<a href='https://github.com/hiddify/HiddifyN/releases'>HiddifyN</a>

📥مک و لینوکس:
<a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray</a>
"""

    elif LANG == 'EN':
        return f"""
{header}

⭕️Required software for connecting to config

📥Android:
<a href='https://play.google.com/store/apps/details?id=com.v2ray.ang'>V2RayNG</a>
<a href='https://play.google.com/store/apps/details?id=ang.hiddify.com'>HiddifyNG</a>

📥iOS:
<a href='https://apps.apple.com/us/app/streisand/id6450534064'>Streisand</a>
<a href='https://apps.apple.com/us/app/foxray/id6448898396'>Foxray</a>
<a href='https://apps.apple.com/us/app/v2box-v2ray-client/id6446814690'>V2box</a>

📥Windows:
<a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray</a>
<a href='https://github.com/2dust/v2rayN/releases'>V2rayN</a>
<a href='https://github.com/hiddify/HiddifyN/releases'>HiddifyN</a>

📥Mac and Linux:
<a href='https://github.com/MatsuriDayo/nekoray/releases'>Nekoray</a>
"""


# Support Info Template
# def support_template(owner_info, header=""):
#     username = None
#     owner_info = all_configs_settings()
#     if owner_info:
#         username = owner_info['support_username'] if owner_info['support_username'] else "-"
#     else:
#         username = "-"

#     if LANG == 'FA':
#         return f"""
# {header}

# 📞پشتیبانی: {username}
# """

#     elif LANG == 'EN':
#         return f"""
# {header}

# 📞Supporter: {username}
# """


# Alert Package Days Template
def package_days_expire_soon_template(sub_id, remaining_days):
    if LANG == 'RU':
        return (
            f"⏰ Ваша подписка истекает через <b>{remaining_days} дн.</b>\n"
            f"Не забудьте продлить!\n"
            f"ID подписки: <code>{sub_id}</code>"
        )
    elif LANG == 'FA':
        return f"""
تنها {remaining_days} روز تا اتمام اعتبار پکیج شما باقی مانده است.
لطفا برای تمدید پکیج اقدام کنید.
شناسه پکیج شما: <code>{sub_id}</code>
"""
    else:
        return (
            f"Only {remaining_days} days left until your package expires.\n"
            f"Please purchase a new package.\n"
            f"Your package ID: <code>{sub_id}</code>"
        )


# Alert Package Size Template
def package_size_end_soon_template(sub_id, remaining_size):
    if LANG == 'RU':
        return (
            f"📶 Ваш трафик почти исчерпан: осталось <b>{remaining_size:.1f} ГБ</b>.\n"
            f"Пополните или продлите подписку!\n"
            f"ID подписки: <code>{sub_id}</code>"
        )
    elif LANG == 'FA':
        return f"""
تنها {remaining_size} گیگابایت تا اتمام اعتبار پکیج شما باقی مانده است.
لطفا برای تمدید پکیج اقدام کنید.

شناسه پکیج شما: <code>{sub_id}</code>
"""
    else:
        return (
            f"Only {remaining_size} GB left until your package expires.\n"
            f"Please renewal package.\n"
            f"Your package ID: <code>{sub_id}</code>"
        )

def renewal_unvalable_template(settings):
    if LANG == 'RU':
        return (
            f"🛑 Продление недоступно.\n"
            f"Для продления должно выполняться одно из условий:\n"
            f"1. Осталось менее {settings.get('advanced_renewal_days', '?')} дн. до окончания.\n"
            f"2. Осталось менее {settings.get('advanced_renewal_usage', '?')} ГБ трафика."
        )
    elif LANG == 'FA':
        return f"""
🛑در حال حاضر شما امکان تمدید اشتراک خود را ندارید.
جهت تمدید اشتراک باید یکی از شروط زیر برقرار باشد:
1- کمتر از {settings.get('advanced_renewal_days', '?')} روز تا اتمام اشتراک شما باقی مانده باشد.
2- حجم باقی مانده اشتراک شما کمتر از {settings.get('advanced_renewal_usage', '?')} گیگابایت باشد.
"""
    else:
        return (
            f"🛑You cannot renew your subscription at this time.\n"
            f"To renew, one of the following conditions must be met:\n"
            f"1- Less than {settings.get('advanced_renewal_days', '?')} days left.\n"
            f"2- Remaining traffic less than {settings.get('advanced_renewal_usage', '?')} GB."
        )
