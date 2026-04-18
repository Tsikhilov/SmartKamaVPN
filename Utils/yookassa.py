# YooKassa Payment Integration Module
import json
import logging
import uuid
import requests
from datetime import datetime, timedelta

class YooKassaPayment:
    def __init__(self, shop_id, secret_key):
        self.shop_id = shop_id
        self.secret_key = secret_key
        self.base_url = "https://api.yookassa.ru/v3"
        self.auth = (shop_id, secret_key)

    def create_payment(self, amount, description, return_url, metadata=None):
        """
        Create a new payment in YooKassa

        Args:
            amount: Amount in rubles
            description: Payment description
            return_url: URL to redirect after payment
            metadata: Additional data to store with payment

        Returns:
            dict: Payment data including confirmation_url
        """
        try:
            idempotence_key = str(uuid.uuid4())

            payload = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "capture": True,
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url
                },
                "description": description,
                "metadata": metadata or {}
            }

            headers = {
                "Content-Type": "application/json",
                "Idempotence-Key": idempotence_key
            }

            response = requests.post(
                f"{self.base_url}/payments",
                auth=self.auth,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"YooKassa create payment error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logging.error(f"YooKassa create payment exception: {e}")
            return None

    def get_payment(self, payment_id):
        """
        Get payment status from YooKassa

        Args:
            payment_id: YooKassa payment ID

        Returns:
            dict: Payment data
        """
        try:
            response = requests.get(
                f"{self.base_url}/payments/{payment_id}",
                auth=self.auth,
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"YooKassa get payment error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logging.error(f"YooKassa get payment exception: {e}")
            return None

    def cancel_payment(self, payment_id):
        """
        Cancel a pending payment

        Args:
            payment_id: YooKassa payment ID

        Returns:
            dict: Cancelled payment data
        """
        try:
            idempotence_key = str(uuid.uuid4())

            headers = {
                "Content-Type": "application/json",
                "Idempotence-Key": idempotence_key
            }

            response = requests.post(
                f"{self.base_url}/payments/{payment_id}/cancel",
                auth=self.auth,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"YooKassa cancel payment error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logging.error(f"YooKassa cancel payment exception: {e}")
            return None


def save_yookassa_settings(db, shop_id, secret_key):
    """Save YooKassa settings to database"""
    try:
        db.add_str_config("yookassa_shop_id", shop_id)
        db.add_str_config("yookassa_secret_key", secret_key)
        return True
    except Exception as e:
        logging.error(f"Error saving YooKassa settings: {e}")
        return False


def get_yookassa_settings(db):
    """Get YooKassa settings from database"""
    try:
        shop_id = db.find_str_config(key="yookassa_shop_id")
        secret_key = db.find_str_config(key="yookassa_secret_key")

        if shop_id and secret_key:
            return {
                "shop_id": shop_id[0]["value"],
                "secret_key": secret_key[0]["value"]
            }
        return None
    except Exception as e:
        logging.error(f"Error getting YooKassa settings: {e}")
        return None
