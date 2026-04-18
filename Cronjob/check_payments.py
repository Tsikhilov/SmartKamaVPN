"""Cron job: check pending YooKassa payments and credit wallets on success."""
import logging
import datetime
from config import CLIENT_TOKEN, YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
from Database.dbManager import USERS_DB

# Bot import needed only to send notifications
try:
    from UserBot.bot import bot, yookassa_client
except Exception:
    bot = None
    yookassa_client = None


def cron_check_yookassa_payments():
    """Check all pending YooKassa payments; credit wallet and notify user on success."""
    if not CLIENT_TOKEN:
        return
    if not yookassa_client:
        logging.info("YooKassa not configured — skip check_payments cron")
        return

    pending = USERS_DB.select_yookassa_payments()
    if not pending:
        return

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    processed = 0

    for record in pending:
        if record.get('status') in ('succeeded', 'canceled'):
            continue

        try:
            yk_data = yookassa_client.get_payment(record['yookassa_payment_id'])
            if not yk_data:
                continue

            new_status = yk_data.get('status', record['status'])
            if new_status == record['status']:
                continue

            USERS_DB.edit_yookassa_payment(
                payment_id=record['payment_id'],
                status=new_status,
                updated_at=now,
            )

            if new_status == 'succeeded':
                telegram_id = record['telegram_id']
                amount = record['amount']
                wallet = USERS_DB.find_wallet(telegram_id=telegram_id)
                if wallet:
                    new_balance = wallet[0]['balance'] + amount
                    USERS_DB.edit_wallet(telegram_id, balance=new_balance)
                else:
                    USERS_DB.add_wallet(telegram_id)
                    USERS_DB.edit_wallet(telegram_id, balance=amount)

                if bot:
                    try:
                        bot.send_message(
                            telegram_id,
                            f"✅ Платёж через ЮKassa подтверждён!\n"
                            f"💰 Ваш баланс пополнен на {amount:.0f} ₽.",
                        )
                    except Exception as e:
                        logging.warning("Failed to notify user %s: %s", telegram_id, e)

                processed += 1
                logging.info("YooKassa payment %s succeeded for user %s", record['payment_id'], telegram_id)

            elif new_status == 'canceled':
                telegram_id = record['telegram_id']
                if bot:
                    try:
                        bot.send_message(telegram_id, "❌ Платёж через ЮKassa был отменён.")
                    except Exception as e:
                        logging.warning("Failed to notify user %s: %s", telegram_id, e)
                logging.info("YooKassa payment %s canceled for user %s", record['payment_id'], telegram_id)

        except Exception as e:
            logging.error("Error processing payment %s: %s", record.get('payment_id'), e)

    logging.info("check_payments cron: %d payments processed", processed)
