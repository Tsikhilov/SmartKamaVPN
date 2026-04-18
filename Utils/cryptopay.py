# Crypto Pay API (@CryptoBot) Integration Module
import logging
import requests


class CryptoPayClient:
    """Client for Crypto Pay API (https://help.crypt.bot/crypto-pay-api)"""

    def __init__(self, api_token, testnet=False):
        self.api_token = api_token
        if testnet:
            self.base_url = "https://testnet-pay.crypt.bot/api"
        else:
            self.base_url = "https://pay.crypt.bot/api"
        self.headers = {
            "Crypto-Pay-API-Token": api_token,
            "Content-Type": "application/json",
        }

    def _request(self, method, params=None):
        try:
            resp = requests.get(
                f"{self.base_url}/{method}",
                headers=self.headers,
                params=params,
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
            logging.error(f"CryptoPay API error: {data}")
            return None
        except Exception as e:
            logging.error(f"CryptoPay API exception ({method}): {e}")
            return None

    def get_me(self):
        """Test connection and get bot info"""
        return self._request("getMe")

    def create_invoice(self, asset, amount, description=None, payload=None, expires_in=3600):
        """
        Create a payment invoice.

        Args:
            asset: Currency code (USDT, TON, BTC, ETH, etc.)
            amount: Payment amount as string
            description: Optional description (up to 1024 chars)
            payload: Optional internal payload (up to 4096 chars)
            expires_in: Seconds until invoice expires (default 1h)

        Returns:
            dict with invoice data including mini_app_invoice_url / bot_invoice_url
        """
        try:
            params = {
                "asset": asset,
                "amount": str(amount),
                "expires_in": expires_in,
            }
            if description:
                params["description"] = description[:1024]
            if payload:
                params["payload"] = str(payload)[:4096]

            resp = requests.get(
                f"{self.base_url}/createInvoice",
                headers=self.headers,
                params=params,
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
            logging.error(f"CryptoPay createInvoice error: {data}")
            return None
        except Exception as e:
            logging.error(f"CryptoPay createInvoice exception: {e}")
            return None

    def get_invoices(self, invoice_ids=None, status=None, asset=None, count=100, offset=0):
        """
        Get invoices list.

        Args:
            invoice_ids: Comma-separated list of invoice IDs
            status: Filter by status (active, paid, expired)
            asset: Filter by asset
            count: Number of invoices to return (max 1000)
            offset: Offset for pagination
        """
        params = {"count": count, "offset": offset}
        if invoice_ids:
            params["invoice_ids"] = invoice_ids
        if status:
            params["status"] = status
        if asset:
            params["asset"] = asset
        return self._request("getInvoices", params)

    def get_invoice(self, invoice_id):
        """Get a single invoice by ID"""
        result = self._request("getInvoices", {"invoice_ids": str(invoice_id)})
        if result and isinstance(result, dict):
            items = result.get("items", [])
            return items[0] if items else None
        return None

    def get_exchange_rates(self):
        """Get current exchange rates"""
        return self._request("getExchangeRates")

    def get_currencies(self):
        """Get list of supported currencies"""
        return self._request("getCurrencies")


def save_cryptopay_settings(db, api_token):
    """Save CryptoPay API token to database"""
    try:
        db.add_str_config("cryptopay_api_token", api_token)
        db.edit_str_config("cryptopay_api_token", value=api_token)
        return True
    except Exception as e:
        logging.error(f"Error saving CryptoPay settings: {e}")
        return False


def get_cryptopay_settings(db):
    """Get CryptoPay API token from database"""
    try:
        token = db.find_str_config(key="cryptopay_api_token")
        if token and token[0].get("value"):
            return {"api_token": token[0]["value"]}
        return None
    except Exception as e:
        logging.error(f"Error getting CryptoPay settings: {e}")
        return None
