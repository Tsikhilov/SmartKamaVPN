import datetime
import json
import logging
import os
import sqlite3
import re
from sqlite3 import Error
from version import is_version_less

_SAFE_COL_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

_KNOWN_TABLES = frozenset({
    'users', 'plans', 'orders', 'order_subscriptions', 'non_order_subscriptions',
    'str_config', 'int_config', 'bool_config', 'wallet', 'payments',
    'yookassa_payments', 'crypto_payments', 'gift_promo_codes', 'servers', 'device_connections', 'referrals',
})

def _validate_column(name):
    if not _SAFE_COL_RE.match(name):
        raise ValueError(f"Invalid column name: {name!r}")

def _validate_table(name):
    if name not in _KNOWN_TABLES:
        raise ValueError(f"Invalid table name: {name!r}")
#from urllib.parse import urlparse

#from Utils import api
#from config import PANEL_URL, API_PATH, USERS_DB_LOC




class UserDBManager:
    def __init__(self, db_file):
        self.conn = self.create_connection(db_file)
        self.create_user_table()
        self._migrate_device_types()
        #self.set_default_configs()

    def _migrate_device_types(self):
        """One-time migration: normalize legacy device_type values in device_connections
        to the current 3-bucket scheme (phone / computer / tv).
        Old values: tablet, unknown, other, desktop, pc  →  computer.
        Safe to call on every startup; idempotent.
        """
        try:
            self.conn.execute(
                "UPDATE device_connections SET device_type = 'computer' "
                "WHERE device_type IN ('tablet', 'unknown', 'other', 'desktop', 'pc')"
            )
            self.conn.commit()
        except Exception as _e:
            logging.warning(f"device_type migration skipped: {_e}")

    #close connection
    def __del__(self):
        self.conn.close()
    
    def close(self):
        self.conn.close()
    

    def create_connection(self, db_file):
        """ Create a database connection to a SQLite database """
        try:
            conn = sqlite3.connect(db_file, check_same_thread=False)
            return conn
        except Error as e:
            logging.error(f"Error while connecting to database \n Error:{e}")
            return None

    def create_user_table(self):
        cur = self.conn.cursor()
        try:
            # All CREATE TABLE statements in a single transaction for fast startup
            cur.execute("CREATE TABLE IF NOT EXISTS users ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "telegram_id INTEGER NOT NULL UNIQUE,"
                        "full_name TEXT NULL,"
                        "username TEXT NULL,"
                        "test_subscription BOOLEAN NOT NULL DEFAULT 0,"
                        "banned BOOLEAN NOT NULL DEFAULT 0,"
                        "created_at TEXT NOT NULL)")

            cur.execute("CREATE TABLE IF NOT EXISTS plans ("
                        "id INTEGER PRIMARY KEY,"
                        "size_gb INTEGER NOT NULL,"
                        "days INTEGER NOT NULL,"
                        "price INTEGER NOT NULL,"
                        "server_id INTEGER NOT NULL,"
                        "description TEXT NULL,"
                        "status BOOLEAN NOT NULL,"
                        "FOREIGN KEY (server_id) REFERENCES server (id))")

            cur.execute("CREATE TABLE IF NOT EXISTS orders ("
                        "id INTEGER PRIMARY KEY,"
                        "telegram_id INTEGER NOT NULL,"
                        "plan_id INTEGER NOT NULL,"
                        "user_name TEXT NOT NULL,"
                        "created_at TEXT NOT NULL,"
                        "FOREIGN KEY (telegram_id) REFERENCES user (telegram_id),"
                        "FOREIGN KEY (plan_id) REFERENCES plans (id))")

            cur.execute("CREATE TABLE IF NOT EXISTS order_subscriptions ("
                        "id INTEGER PRIMARY KEY,"
                        "order_id INTEGER NOT NULL,"
                        "uuid TEXT NOT NULL,"
                        "server_id INTEGER NOT NULL,"
                        "FOREIGN KEY (server_id) REFERENCES server (id),"
                        "FOREIGN KEY (order_id) REFERENCES orders (id))")

            cur.execute("CREATE TABLE IF NOT EXISTS non_order_subscriptions ("
                        "id INTEGER PRIMARY KEY,"
                        "telegram_id INTEGER NOT NULL,"
                        "uuid TEXT NOT NULL UNIQUE,"
                        "server_id INTEGER NOT NULL,"
                        "FOREIGN KEY (server_id) REFERENCES server (id),"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")

            cur.execute("CREATE TABLE IF NOT EXISTS str_config ("
                        "key TEXT NOT NULL UNIQUE,"
                        "value TEXT NULL)")

            cur.execute("CREATE TABLE IF NOT EXISTS int_config ("
                        "key TEXT NOT NULL UNIQUE,"
                        "value INTEGER NOT NULL)")

            cur.execute("CREATE TABLE IF NOT EXISTS bool_config ("
                        "key TEXT NOT NULL UNIQUE,"
                        "value BOOLEAN NOT NULL)")

            cur.execute("CREATE TABLE IF NOT EXISTS wallet ("
                        "telegram_id INTEGER NOT NULL UNIQUE,"
                        "balance INTEGER NOT NULL DEFAULT 0,"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")

            cur.execute("CREATE TABLE IF NOT EXISTS payments ("
                        "id INTEGER PRIMARY KEY,"
                        "telegram_id INTEGER NOT NULL,"
                        "payment_amount INTEGER NOT NULL,"
                        "payment_method TEXT NOT NULL,"
                        "payment_image TEXT NOT NULL,"
                        "approved BOOLEAN NULL,"
                        "created_at TEXT NOT NULL,"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")

            cur.execute("CREATE TABLE IF NOT EXISTS yookassa_payments ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "payment_id TEXT UNIQUE NOT NULL,"
                        "telegram_id INTEGER NOT NULL,"
                        "amount INTEGER NOT NULL,"
                        "status TEXT NOT NULL DEFAULT 'pending',"
                        "yookassa_payment_id TEXT,"
                        "confirmation_url TEXT,"
                        "created_at TEXT NOT NULL,"
                        "updated_at TEXT,"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")

            cur.execute("CREATE TABLE IF NOT EXISTS crypto_payments ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "payment_id TEXT UNIQUE NOT NULL,"
                        "telegram_id INTEGER NOT NULL,"
                        "invoice_id TEXT,"
                        "asset TEXT NOT NULL,"
                        "amount_crypto TEXT NOT NULL,"
                        "amount_rub INTEGER NOT NULL,"
                        "status TEXT NOT NULL DEFAULT 'active',"
                        "pay_url TEXT,"
                        "created_at TEXT NOT NULL,"
                        "updated_at TEXT,"
                        "FOREIGN KEY (telegram_id) REFERENCES users (telegram_id))")

            cur.execute("CREATE TABLE IF NOT EXISTS gift_promo_codes ("
                        "code TEXT PRIMARY KEY,"
                        "creator_telegram_id INTEGER NOT NULL,"
                        "amount INTEGER NOT NULL,"
                        "status TEXT NOT NULL DEFAULT 'new',"
                        "created_at TEXT NOT NULL,"
                        "redeemed_by INTEGER,"
                        "redeemed_at TEXT)")

            cur.execute("CREATE TABLE IF NOT EXISTS servers ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "url TEXT NOT NULL,"
                        "title TEXT, description TEXT,"
                        "user_limit INTEGER NOT NULL,"
                        "status BOOLEAN NOT NULL,"
                        "default_server BOOLEAN NOT NULL DEFAULT 0)")

            # Device tracking table — tracks which devices connect to each subscription
            cur.execute("CREATE TABLE IF NOT EXISTS device_connections ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "sub_uuid TEXT NOT NULL,"
                        "user_agent TEXT NOT NULL,"
                        "device_type TEXT NOT NULL DEFAULT 'unknown',"
                        "device_name TEXT,"
                        "client_app TEXT,"
                        "client_ip TEXT,"
                        "first_seen TEXT NOT NULL,"
                        "last_seen TEXT NOT NULL,"
                        "UNIQUE(sub_uuid, user_agent))")

            # Referral program — tracks who invited whom and bonuses
            cur.execute("CREATE TABLE IF NOT EXISTS referrals ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "referrer_id INTEGER NOT NULL,"
                        "referee_id INTEGER NOT NULL UNIQUE,"
                        "bonus_given INTEGER NOT NULL DEFAULT 0,"
                        "total_bonus INTEGER NOT NULL DEFAULT 0,"
                        "created_at TEXT NOT NULL,"
                        "FOREIGN KEY (referrer_id) REFERENCES users (telegram_id),"
                        "FOREIGN KEY (referee_id) REFERENCES users (telegram_id))")

            self.conn.commit()
            logging.info("All tables created successfully!")


        except Error as e:
            logging.error(f"Error while creating user table \n Error:{e}")
            return False
        finally:
            cur.close()
        return True

    def select_users(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM users")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all users \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_user(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find user!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM users WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"User {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding user {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def delete_user(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete user!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"DELETE FROM users WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"User {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting user {kwargs} \n Error:{e}")
            return False

        finally:
            cur.close()
    def edit_user(self, telegram_id, **kwargs):
        cur = self.conn.cursor()
        try:

            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE users SET {key}=? WHERE telegram_id=?", (value, telegram_id))
                    self.conn.commit()
                    logging.info(f"User [{telegram_id}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating user [{telegram_id}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True

        finally:
            cur.close()
    def add_user(self, telegram_id, full_name,username, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO users(telegram_id, full_name,username, created_at) VALUES(?,?,?,?)",
                        (telegram_id, full_name,username, created_at))
            self.conn.commit()
            logging.info(f"User [{telegram_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding user [{telegram_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def add_plan(self, plan_id, size_gb, days, price, server_id, description=None, status=True):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO plans(id,size_gb, days, price, server_id, description, status) VALUES(?,?,?,?,?,?,?)",
                        (plan_id, size_gb, days, price, server_id, description, status))
            self.conn.commit()
            logging.info(f"Plan [{size_gb}GB] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding plan [{size_gb}GB] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_plans(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM plans ORDER BY price ASC")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all plans \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_plan(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find plan!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM plans WHERE {key}=? ORDER BY price ASC", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Plan {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding plan {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def delete_plan(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete plan!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"DELETE FROM plans WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"Plan {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting plan {kwargs} \n Error:{e}")
            return False

        finally:
            cur.close()
    def edit_plan(self, plan_id, **kwargs):
        cur = self.conn.cursor()
        try:

            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE plans SET {key}=? WHERE id=?", (value, plan_id))
                    self.conn.commit()
                    logging.info(f"Plan [{plan_id}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating plan [{plan_id}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True
    
        finally:
            cur.close()
    def add_user_plans(self, telegram_id, plan_id):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO user_plans(telegram_id, plan_id) VALUES(?,?)",
                        (telegram_id, plan_id))
            self.conn.commit()
            logging.info(f"Plan [{plan_id}] Reserved for [{telegram_id}] successfully!")
            return True

        except Error as e:
            logging.error(f"Error while Reserving plan [{plan_id}] for [{telegram_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_user_plans(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM user_plans")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all user_plans \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_user_plans(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find user_plan!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM user_plans WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Plan {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding user_plans {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def delete_user_plans(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete user_plan!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"DELETE FROM user_plans WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"Plan {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting user_plans {kwargs} \n Error:{e}")
            return False

        finally:
            cur.close()
    def edit_user_plans(self, user_plans_id, **kwargs):
        cur = self.conn.cursor()
        try:

            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE user_plans SET {key}=? WHERE id=?", (value, user_plans_id))
                    self.conn.commit()
                    logging.info(f"user_plans [{user_plans_id}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating user_plans [{user_plans_id}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True
    
        finally:
            cur.close()
    def add_order(self, order_id, telegram_id,user_name, plan_id, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO orders(id,telegram_id, plan_id,user_name,created_at) VALUES(?,?,?,?,?)",
                (order_id, telegram_id, plan_id,user_name, created_at))
            self.conn.commit()
            logging.info(f"Order [{order_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding order [{order_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_orders(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM orders")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all orders \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_order(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find order!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM orders WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Order {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding order {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def edit_order(self, order_id, **kwargs):
        cur = self.conn.cursor()
        try:

            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE orders SET {key}=? WHERE id=?", (value, order_id))
                    self.conn.commit()
                    logging.info(f"Order [{order_id}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating order [{order_id}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True

        finally:
            cur.close()
    def add_order_subscription(self, sub_id, order_id, uuid, server_id):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO order_subscriptions(id,order_id,uuid,server_id) VALUES(?,?,?,?)",
                (sub_id, order_id, uuid, server_id))
            self.conn.commit()
            logging.info(f"Order [{order_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding order [{order_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_order_subscription(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM order_subscriptions")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all orders \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_order_subscription(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find order!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM order_subscriptions WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Order {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding order {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def edit_order_subscriptions(self, order_id, **kwargs):
        cur = self.conn.cursor()
        try:

            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE order_subscriptions SET {key}=? WHERE order_id=?", (value, order_id))
                    self.conn.commit()
                    logging.info(f"Order [{order_id}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating order [{order_id}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True

        finally:
            cur.close()
    def delete_order_subscription(self, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"DELETE FROM order_subscriptions WHERE {key}=?", (value,))
                self.conn.commit()
                logging.info(f"Order [{value}] deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting order [{kwargs}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def get_order_name_by_uuid(self, uuid):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT o.user_name FROM orders o "
                "JOIN order_subscriptions os ON o.id = os.order_id "
                "WHERE os.uuid=? LIMIT 1",
                (uuid,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        except Error as e:
            logging.error(f"Error getting order name by uuid {uuid}: {e}")
            return None

        finally:
            cur.close()
    def add_non_order_subscription(self, non_sub_id, telegram_id, uuid, server_id):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO non_order_subscriptions(id,telegram_id,uuid,server_id) VALUES(?,?,?,?)",
                (non_sub_id, telegram_id, uuid, server_id))
            self.conn.commit()
            logging.info(f"Order [{telegram_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding order [{telegram_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_non_order_subscriptions(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM non_order_subscriptions")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all orders \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_non_order_subscription(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find order!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM non_order_subscriptions WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Order {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding order {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def delete_non_order_subscription(self, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"DELETE FROM non_order_subscriptions WHERE {key}=?", (value,))
                self.conn.commit()
                logging.info(f"Order [{value}] deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting order [{kwargs}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def edit_bool_config(self, key_row, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE bool_config SET {key}=? WHERE key=?", (value, key_row))
                    self.conn.commit()
                    logging.info(f"Settings [{key}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating settings [{key}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True

        finally:
            cur.close()
    def find_bool_config(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find settings!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM bool_config WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Settings {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding settings {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def add_bool_config(self, key, value):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT or IGNORE INTO bool_config(key,value) VALUES(?,?)",
                (key, value))
            self.conn.commit()
            logging.info(f"Settings [{key}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding settings [{key}] \n Error: {e}")
            return False
        finally:
            cur.close()
            

    def select_bool_config(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM bool_config")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all settings \n Error:{e}")
            return None

        finally:
            cur.close()
    def select_str_config(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM str_config")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all settings \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_str_config(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find settings!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM str_config WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Settings {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding settings {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def edit_str_config(self, key_row, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE str_config SET {key}=? WHERE key=?", (value, key_row))
                    self.conn.commit()
                    logging.info(f"Settings [{key}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating settings [{key}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True

        finally:
            cur.close()
    def add_str_config(self, key, value):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT or IGNORE INTO str_config(key,value) VALUES(?,?)",
                (key, value))
            self.conn.commit()
            logging.info(f"Settings [{key}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding settings [{key}] \n Error: {e}")
            return False
        finally:
            cur.close()

    def select_int_config(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM int_config")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all settings \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_int_config(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find settings!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM int_config WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Settings {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding settings {kwargs} \n Error:{e}")
            return None
        finally:
            cur.close()
    def edit_int_config(self, key_row, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():            
                _validate_column(key)
                try:
                    cur.execute(f"UPDATE int_config SET {key}=? WHERE key=?", (value, key_row))
                    self.conn.commit()
                    logging.info(f"Settings [{key}] successfully update [{key}] to [{value}]")
                except Error as e:
                    logging.error(f"Error while updating settings [{key}] [{key}] to [{value}] \n Error: {e}")
                    return False

            return True

        finally:
            cur.close()
    def add_int_config(self, key, value):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT or IGNORE INTO int_config(key,value) VALUES(?,?)",
                (key, value))
            self.conn.commit()
            logging.info(f"Settings [{key}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding settings [{key}] \n Error: {e}")
            return False
        finally:
            cur.close()

    def set_default_configs(self):
        
        self.add_bool_config("visible_hiddify_hyperlink", True)
        self.add_bool_config("three_random_num_price", False)
        self.add_bool_config("force_join_channel", False)
        self.add_bool_config("panel_auto_backup", True)
        self.add_bool_config("bot_auto_backup", True)
        self.add_bool_config("test_subscription", True)
        self.add_bool_config("reminder_notification", True)
        
        self.add_bool_config("renewal_subscription_status", True)
        self.add_bool_config("buy_subscription_status", True)
        self.add_bool_config("payment_method_card_enabled", True)
        self.add_bool_config("payment_method_yookassa_enabled", True)
        self.add_bool_config("payment_method_pally_enabled", False)
        self.add_bool_config("payment_method_crypto_enabled", False)


        self.add_bool_config("visible_conf_dir", False)
        self.add_bool_config("visible_conf_sub_auto", True)
        self.add_bool_config("visible_conf_sub_url", False)
        self.add_bool_config("visible_conf_sub_url_b64", False)
        self.add_bool_config("visible_conf_clash", False)
        self.add_bool_config("visible_conf_hiddify", False)
        self.add_bool_config("visible_conf_sub_sing_box", False)
        self.add_bool_config("visible_conf_sub_full_sing_box", False)

        self.add_str_config("bot_admin_id", None)
        self.add_str_config("bot_token_admin", None)
        self.add_str_config("bot_token_client", None)
        self.add_str_config("bot_lang", None)

        self.add_str_config("card_number", None)
        self.add_str_config("card_holder", None)
        self.add_str_config("support_username", None)
        self.add_str_config("channel_id", None)
        self.add_str_config("msg_user_start", None)

        self.add_str_config("msg_manual_android", None)
        self.add_str_config("msg_manual_ios", None)
        self.add_str_config("msg_manual_windows", None)
        self.add_str_config("msg_manual_mac", None)
        self.add_str_config("msg_manual_linux", None)

        self.add_str_config("msg_faq", None)
        self.add_str_config("pally_payment_url", None)

        self.add_int_config("min_deposit_amount", 10000)

        self.add_int_config("reminder_notification_days", 3)
        self.add_int_config("reminder_notification_usage", 3)

        self.add_int_config("test_sub_days", 1)
        self.add_int_config("test_sub_size_gb", 1)
        
        self.add_int_config("advanced_renewal_days", 3)
        self.add_int_config("advanced_renewal_usage", 3)
        
        self.add_int_config("renewal_method", 1)



    def add_wallet(self, telegram_id):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO wallet(telegram_id) VALUES(?)",
                (telegram_id,))
            self.conn.commit()
            logging.info(f"Balance [{telegram_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding balance [{telegram_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_wallet(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM wallet")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all balance \n Error:{e}")
            return None

        finally:
            cur.close()
    def find_wallet(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find balance!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM wallet WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Balance {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding balance {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def edit_wallet(self, telegram_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"UPDATE wallet SET {key}=? WHERE telegram_id=?", (value, telegram_id,))
                self.conn.commit()
                logging.info(f"balance successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating balance [{key}] to [{value}] \n Error: {e}")
            return False

        finally:
            cur.close()

    def atomic_deduct_wallet(self, telegram_id, amount):
        """Atomically deduct amount from wallet. Returns True if balance was sufficient."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "UPDATE wallet SET balance = balance - ? WHERE telegram_id = ? AND balance >= ?",
                (int(amount), telegram_id, int(amount)))
            self.conn.commit()
            if cur.rowcount > 0:
                logging.info(f"Wallet atomic deduct {amount} for {telegram_id} OK")
                return True
            logging.warning(f"Wallet atomic deduct {amount} for {telegram_id}: insufficient balance")
            return False
        except Error as e:
            logging.error(f"Error in atomic_deduct_wallet: {e}")
            return False
        finally:
            cur.close()

    def atomic_credit_wallet(self, telegram_id, amount):
        """Atomically credit amount to wallet. Returns True on success."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "UPDATE wallet SET balance = balance + ? WHERE telegram_id = ?",
                (int(amount), telegram_id))
            self.conn.commit()
            if cur.rowcount > 0:
                logging.info(f"Wallet atomic credit {amount} for {telegram_id} OK")
                return True
            logging.warning(f"Wallet atomic credit for {telegram_id}: wallet not found")
            return False
        except Error as e:
            logging.error(f"Error in atomic_credit_wallet: {e}")
            return False
        finally:
            cur.close()

    def add_payment(self, payment_id, telegram_id, payment_amount, payment_method, payment_image, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO payments(id,telegram_id, payment_amount,payment_method,payment_image,created_at) VALUES(?,?,?,?,?,?)",
                (payment_id, telegram_id, payment_amount, payment_method, payment_image, created_at))
            self.conn.commit()
            logging.info(f"Payment [{payment_id}] added successfully!")
            return True

        except Error as e:
            logging.error(f"Error while adding payment [{payment_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def edit_payment(self, payment_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"UPDATE payments SET {key}=? WHERE id=?", (value, payment_id))
                self.conn.commit()
                logging.info(f"payment successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating payment [{key}] to [{value}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def find_payment(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find payment!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM payments WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Payment {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding payment {kwargs} \n Error:{e}")
            return None
        
        finally:
            cur.close()
    def select_payments(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM payments")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all payments \n Error:{e}")
            return None
    
        finally:
            cur.close()
    def select_servers(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM servers")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all servers \n Error:{e}")
            return None
        
        finally:
            cur.close()
    def add_server(self, url, user_limit, title=None, description=None, status=True, default_server=False):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO servers(url,title,description,user_limit,status,default_server) VALUES(?,?,?,?,?,?)",
                (url, title, description, user_limit, status, default_server))
            self.conn.commit()
            logging.info(f"Server [{url}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding server [{url}] \n Error: {e}")
            return False
    
        finally:
            cur.close()
    def edit_server(self, server_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"UPDATE servers SET {key}=? WHERE id=?", (value, server_id))
                self.conn.commit()
                logging.info(f"Server [{server_id}] successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating server [{server_id}] [{key}] to [{value}] \n Error: {e}")
            return False
    
        finally:
            cur.close()
    def find_server(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find server!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM servers WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Server {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding server {kwargs} \n Error:{e}")
            return None
        
        finally:
            cur.close()
    def delete_server(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to delete server!")
            return False
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"DELETE FROM servers WHERE {key}=?", (value,))
                self.conn.commit()
            logging.info(f"server {kwargs} deleted successfully!")
            return True
        except Error as e:
            logging.error(f"Error while deleting server {kwargs} \n Error:{e}")
            return False
        
    

        finally:
            cur.close()
    # YooKassa Payment Methods
    def add_yookassa_payment(self, payment_id, telegram_id, amount, yookassa_payment_id, confirmation_url, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO yookassa_payments(payment_id, telegram_id, amount, yookassa_payment_id, confirmation_url, status, created_at) VALUES(?,?,?,?,?,?,?)",
                (payment_id, telegram_id, amount, yookassa_payment_id, confirmation_url, 'pending', created_at))
            self.conn.commit()
            logging.info(f"YooKassa Payment [{payment_id}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding YooKassa payment [{payment_id}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def find_yookassa_payment(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find YooKassa payment!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM yookassa_payments WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"YooKassa Payment {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding YooKassa payment {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def edit_yookassa_payment(self, payment_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"UPDATE yookassa_payments SET {key}=? WHERE payment_id=?", (value, payment_id))
                self.conn.commit()
                logging.info(f"YooKassa payment [{payment_id}] successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating YooKassa payment [{payment_id}] [{key}] to [{value}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_yookassa_payments(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM yookassa_payments ORDER BY created_at DESC")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all YooKassa payments \n Error:{e}")
            return None

        finally:
            cur.close()
    # Crypto Payment Methods
    def add_crypto_payment(self, payment_id, telegram_id, invoice_id, asset, amount_crypto, amount_rub, pay_url, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO crypto_payments(payment_id, telegram_id, invoice_id, asset, amount_crypto, amount_rub, pay_url, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (payment_id, telegram_id, invoice_id, asset, amount_crypto, amount_rub, pay_url, 'active', created_at))
            self.conn.commit()
            logging.info(f"Crypto Payment [{payment_id}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding crypto payment [{payment_id}] \n Error: {e}")
            return False
        finally:
            cur.close()

    def find_crypto_payment(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find crypto payment!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM crypto_payments WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Crypto Payment {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding crypto payment {kwargs} \n Error:{e}")
            return None
        finally:
            cur.close()

    def edit_crypto_payment(self, payment_id, **kwargs):
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"UPDATE crypto_payments SET {key}=? WHERE payment_id=?", (value, payment_id))
                self.conn.commit()
                logging.info(f"Crypto payment [{payment_id}] successfully update [{key}] to [{value}]")
            return True
        except Error as e:
            logging.error(f"Error while updating crypto payment [{payment_id}] [{key}] to [{value}] \n Error: {e}")
            return False
        finally:
            cur.close()

    def select_crypto_payments(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM crypto_payments ORDER BY created_at DESC")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all crypto payments \n Error:{e}")
            return None
        finally:
            cur.close()

    # Gift Promo Code Methods
    def add_gift_promo_code(self, code, creator_telegram_id, amount, created_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO gift_promo_codes(code, creator_telegram_id, amount, status, created_at) VALUES(?,?,?,?,?)",
                (code, creator_telegram_id, amount, 'new', created_at))
            self.conn.commit()
            logging.info(f"Gift promo code [{code}] added successfully!")
            return True
        except Error as e:
            logging.error(f"Error while adding gift promo code [{code}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def find_gift_promo_code(self, **kwargs):
        if len(kwargs) != 1:
            logging.warning("You can only use one key to find gift promo code!")
            return None
        rows = []
        cur = self.conn.cursor()
        try:
            for key, value in kwargs.items():
                _validate_column(key)
                cur.execute(f"SELECT * FROM gift_promo_codes WHERE {key}=?", (value,))
                rows = cur.fetchall()
            if len(rows) == 0:
                logging.debug(f"Gift promo code {kwargs} not found!")
                return None
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while finding gift promo code {kwargs} \n Error:{e}")
            return None

        finally:
            cur.close()
    def redeem_gift_promo_code(self, code, redeemed_by, redeemed_at):
        cur = self.conn.cursor()
        try:
            cur.execute(
                "UPDATE gift_promo_codes SET status=?, redeemed_by=?, redeemed_at=? WHERE code=?",
                ('redeemed', redeemed_by, redeemed_at, code),
            )
            self.conn.commit()
            logging.info(f"Gift promo code [{code}] redeemed successfully!")
            return True
        except Error as e:
            logging.error(f"Error while redeeming gift promo code [{code}] \n Error: {e}")
            return False

        finally:
            cur.close()
    def select_gift_promo_codes(self):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM gift_promo_codes ORDER BY created_at DESC")
            rows = cur.fetchall()
            rows = [dict(zip([key[0] for key in cur.description], row)) for row in rows]
            return rows
        except Error as e:
            logging.error(f"Error while selecting all gift promo codes \n Error:{e}")
            return None

        finally:
            cur.close()
    def backup_to_json(self, backup_dir):
        try:

            backup_data = {}  # Store backup data in a dictionary

            # List of tables to backup
            tables = ['users', 'plans', 'orders', 'order_subscriptions', 'non_order_subscriptions',
                      'str_config', 'int_config', 'bool_config', 'wallet', 'payments', 'servers', 'yookassa_payments',
                      'gift_promo_codes']

            for table in tables:
                cur = self.conn.cursor()
                try:
                    cur.execute(f"SELECT * FROM {table}")
                    rows = cur.fetchall()

                    # Convert rows to list of dictionaries
                    table_data = []
                    for row in rows:
                        columns = [column[0] for column in cur.description]
                        table_data.append(dict(zip(columns, row)))

                    backup_data[table] = table_data
                finally:
                    cur.close()
            return backup_data

        except sqlite3.Error as e:
            logging.error('SQLite error:', str(e))
            return False
    def restore_from_json(self, backup_file):
        logging.info(f"Restoring database from {backup_file}...")
        try:
            cur = self.conn.cursor()
            try:

                with open(backup_file, 'r') as json_file:
                    backup_data = json.load(json_file)
                
                if not isinstance(backup_data, dict):
                    logging.error('Backup data should be a dictionary.')
                    print('Backup data should be a dictionary.')
                    return
                # print(backup_data.get('version'), VERSION)
                # if backup_data.get('version') != VERSION:
                #     if backup_data.get('version') is None:
                #         logging.error('Backup data version is not found.')
                #         print('Backup data version is not found.')
                #         return
                #     if VERSION.find('-pre'):
                #         VERSION = VERSION.split('-pre')[0]
                #     if is_version_less(backup_data.get('version'),VERSION ):
                #         logging.error('Backup data version is less than current version.')
                #         print('Backup data version is less than current version.')
                #         if is_version_less(backup_data.get('version'), '5.5.0'):
                #             logging.error('Backup data version is less than 5.5.0.')
                #             print('Backup data version is less than 5.5.0.')
                #             return 

                self.conn.execute('BEGIN TRANSACTION')

                for table, data in backup_data.items():
                    if table == 'version':
                        continue
                    _validate_table(table)
                    logging.info(f"Restoring table {table}...")
                    for entry in data:
                        if not isinstance(entry, dict):
                            logging.error('Invalid entry format. Expected a dictionary.')
                            print('Invalid entry format. Expected a dictionary.')
                            continue

                        for col in entry.keys():
                            _validate_column(col)
                        keys = ', '.join(entry.keys())
                        placeholders = ', '.join(['?' for _ in entry.values()])
                        values = tuple(entry.values())
                        query = f"INSERT OR REPLACE INTO {table} ({keys}) VALUES ({placeholders})"
                        logging.info(f"Query: {query}")
                    
                        try:
                            cur.execute(query, values)
                        except sqlite3.Error as e:
                            logging.error('SQLite error:', str(e))
                            logging.error('Entry:', entry)
                            print('SQLite error:', str(e))
                            print('Entry:', entry)

                self.conn.commit()
                logging.info('Database restored successfully.')
                return True

            finally:
                cur.close()
        except sqlite3.Error as e:
            logging.error('SQLite error:', str(e))
            return False

    # -------------------- Device Tracking --------------------
    def upsert_device_connection(self, sub_uuid, user_agent, device_type, device_name, client_app, client_ip=None):
        """Insert or update a device connection record for a subscription."""
        cur = self.conn.cursor()
        try:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                cur.execute(
                    "INSERT INTO device_connections (sub_uuid, user_agent, device_type, device_name, client_app, client_ip, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(sub_uuid, user_agent) DO UPDATE SET "
                    "last_seen=?, client_ip=?, device_type=?, device_name=?, client_app=?",
                    (sub_uuid, user_agent, device_type, device_name, client_app, client_ip, now, now,
                     now, client_ip, device_type, device_name, client_app)
                )
                self.conn.commit()
                return True
            except Error as e:
                logging.error(f"Error upserting device connection: {e}")
                return False

        finally:
            cur.close()
    def get_devices_by_sub(self, sub_uuid):
        """Get all devices connected to a subscription UUID."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT * FROM device_connections WHERE sub_uuid=? ORDER BY last_seen DESC",
                (sub_uuid,)
            )
            rows = cur.fetchall()
            return [dict(zip([k[0] for k in cur.description], r)) for r in rows]
        except Error as e:
            logging.error(f"Error getting devices: {e}")
            return []

        finally:
            cur.close()
    def get_all_devices(self):
        """Get all device connections grouped by subscription."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT * FROM device_connections ORDER BY last_seen DESC")
            rows = cur.fetchall()
            return [dict(zip([k[0] for k in cur.description], r)) for r in rows]
        except Error as e:
            logging.error(f"Error getting all devices: {e}")
            return []

        finally:
            cur.close()
    def count_devices_by_sub(self, sub_uuid):
        """Count devices connected to a subscription UUID."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM device_connections WHERE sub_uuid=?", (sub_uuid,))
            row = cur.fetchone()
            return row[0] if row else 0
        except Error as e:
            logging.error(f"Error counting devices: {e}")
            return 0

        finally:
            cur.close()
    def delete_device_connection(self, device_id):
        """Delete a device connection by its id."""
        cur = self.conn.cursor()
        try:
            cur.execute("DELETE FROM device_connections WHERE id=?", (int(device_id),))
            self.conn.commit()
            return cur.rowcount > 0
        except Error as e:
            logging.error(f"Error deleting device connection: {e}")
            return False

        finally:
            cur.close()
    def get_device_stats(self):
        """Get device type statistics."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT device_type, COUNT(DISTINCT sub_uuid || user_agent) as count "
                "FROM device_connections GROUP BY device_type ORDER BY count DESC"
            )
            rows = cur.fetchall()
            stats = {}
            for raw_type, count in rows:
                normalized = str(raw_type or '').strip().lower()
                if normalized in ('tv', 'android tv', 'android_tv', 'smart tv', 'apple tv'):
                    key = 'tv'
                elif normalized in ('phone', 'android', 'ios', 'iphone'):
                    key = 'phone'
                else:
                    key = 'computer'
                stats[key] = stats.get(key, 0) + count
            return stats
        except Error as e:
            logging.error(f"Error getting device stats: {e}")
            return {}

        finally:
            cur.close()
    # ── Referral methods ──

    def add_referral(self, referrer_id, referee_id, created_at):
        """Record a referral relationship."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT OR IGNORE INTO referrals (referrer_id, referee_id, created_at) "
                "VALUES (?, ?, ?)",
                (referrer_id, referee_id, created_at)
            )
            self.conn.commit()
            return cur.rowcount > 0
        except Error as e:
            logging.error(f"Error adding referral: {e}")
            return False

        finally:
            cur.close()
    def find_referrer(self, referee_id):
        """Find who referred a user."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT referrer_id FROM referrals WHERE referee_id = ?", (referee_id,))
            row = cur.fetchone()
            return row[0] if row else None
        except Error as e:
            logging.error(f"Error finding referrer: {e}")
            return None

        finally:
            cur.close()
    def get_referrals(self, referrer_id):
        """Get all users referred by someone."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT r.referee_id, r.total_bonus, r.created_at, u.full_name "
                "FROM referrals r LEFT JOIN users u ON r.referee_id = u.telegram_id "
                "WHERE r.referrer_id = ? ORDER BY r.created_at DESC",
                (referrer_id,)
            )
            rows = cur.fetchall()
            return [{"referee_id": r[0], "total_bonus": r[1], "created_at": r[2], "name": r[3]} for r in rows]
        except Error as e:
            logging.error(f"Error getting referrals: {e}")
            return []

        finally:
            cur.close()
    def add_referral_bonus(self, referrer_id, referee_id, amount):
        """Add bonus to referrer for referee's purchase."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "UPDATE referrals SET total_bonus = total_bonus + ?, bonus_given = 1 "
                "WHERE referrer_id = ? AND referee_id = ?",
                (amount, referrer_id, referee_id)
            )
            self.conn.commit()
            return cur.rowcount > 0
        except Error as e:
            logging.error(f"Error adding referral bonus: {e}")
            return False

        finally:
            cur.close()
    def get_referral_stats(self, referrer_id):
        """Get referral statistics for a user."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) as total, COALESCE(SUM(total_bonus), 0) as earned "
                "FROM referrals WHERE referrer_id = ?",
                (referrer_id,)
            )
            row = cur.fetchone()
            return {"invited": row[0], "earned": row[1]} if row else {"invited": 0, "earned": 0}
        except Error as e:
            logging.error(f"Error getting referral stats: {e}")
            return {"invited": 0, "earned": 0}
    

        finally:
            cur.close()
try:
    from config import USERS_DB_LOC
except ImportError:
    USERS_DB_LOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smartkamavpn.db")
USERS_DB = UserDBManager(USERS_DB_LOC)
