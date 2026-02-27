"""Database service helpers for blind-box plugin."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def init_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_wallet (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                balance INTEGER NOT NULL,
                registered_at INTEGER NOT NULL,
                PRIMARY KEY (group_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS category_state (
                category_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                remaining_items TEXT NOT NULL,
                remaining_slots TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_kv (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_listing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                price INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                seller_user_id TEXT NOT NULL,
                is_system INTEGER NOT NULL,
                day_key TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def db_get_user(db_path: Path, group_id: str, user_id: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT group_id,user_id,balance,registered_at FROM user_wallet WHERE group_id=? AND user_id=?",
            (group_id, user_id),
        )
        return cur.fetchone()
    finally:
        conn.close()


def db_get_balance(db_path: Path, group_id: str, user_id: str) -> Optional[int]:
    row = db_get_user(db_path, group_id, user_id)
    return int(row[2]) if row else None


def db_register_user(db_path: Path, group_id: str, user_id: str, balance: int):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_wallet(group_id,user_id,balance,registered_at) VALUES (?,?,?,?)",
            (group_id, user_id, int(balance), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def db_update_balance(db_path: Path, group_id: str, user_id: str, balance: int):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE user_wallet SET balance=? WHERE group_id=? AND user_id=?", (int(balance), group_id, user_id))
        conn.commit()
    finally:
        conn.close()


def db_ensure_category_state(db_path: Path, category_id: str, category: dict):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT signature FROM category_state WHERE category_id=?", (category_id,))
        row = cur.fetchone()
        if row is None or row[0] != category["signature"]:
            conn.execute(
                "INSERT OR REPLACE INTO category_state(category_id,signature,remaining_items,remaining_slots,updated_at) VALUES (?,?,?,?,?)",
                (
                    category_id,
                    category["signature"],
                    json.dumps(list(category["items"].keys()), ensure_ascii=False),
                    json.dumps(list(category["slots"]), ensure_ascii=False),
                    int(time.time()),
                ),
            )
            conn.commit()
    finally:
        conn.close()


def db_get_category_state(db_path: Path, category_id: str) -> Tuple[List[str], List[int]]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT remaining_items, remaining_slots FROM category_state WHERE category_id=?", (category_id,))
        row = cur.fetchone()
        if not row:
            return [], []
        items = json.loads(row[0]) if row[0] else []
        slots = json.loads(row[1]) if row[1] else []
        return list(items), sorted(int(v) for v in slots)
    finally:
        conn.close()


def db_set_category_state(db_path: Path, category_id: str, signature: str, items: List[str], slots: List[int]):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE category_state SET signature=?, remaining_items=?, remaining_slots=?, updated_at=? WHERE category_id=?",
            (signature, json.dumps(items, ensure_ascii=False), json.dumps(slots, ensure_ascii=False), int(time.time()), category_id),
        )
        conn.commit()
    finally:
        conn.close()


def db_get_kv(db_path: Path, key: str) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT v FROM system_kv WHERE k=?", (key,))
        row = cur.fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def db_set_kv(db_path: Path, key: str, value: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO system_kv(k,v) VALUES (?,?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def db_grant_daily_gift(db_path: Path, amount: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("UPDATE user_wallet SET balance = balance + ?", (int(amount),))
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def db_add_market_listing(
    db_path: Path,
    group_id: str,
    category_id: str,
    item_id: str,
    item_name: str,
    price: int,
    quantity: int,
    seller_user_id: str,
    is_system: int,
    day_key: str,
):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO market_listing(
                group_id,category_id,item_id,item_name,price,quantity,seller_user_id,is_system,day_key,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                group_id,
                category_id,
                item_id,
                item_name,
                int(price),
                int(quantity),
                seller_user_id,
                int(is_system),
                day_key,
                int(time.time()),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def db_list_market_listings(db_path: Path, group_id: str, category_id: str = "") -> List[dict]:
    conn = sqlite3.connect(db_path)
    try:
        if category_id:
            cur = conn.execute(
                """
                SELECT id,group_id,category_id,item_id,item_name,price,quantity,seller_user_id,is_system,day_key
                FROM market_listing WHERE group_id=? AND category_id=? AND quantity>0
                ORDER BY is_system DESC, price ASC, id ASC
                """,
                (group_id, category_id),
            )
        else:
            cur = conn.execute(
                """
                SELECT id,group_id,category_id,item_id,item_name,price,quantity,seller_user_id,is_system,day_key
                FROM market_listing WHERE group_id=? AND quantity>0
                ORDER BY category_id, is_system DESC, price ASC, id ASC
                """,
                (group_id,),
            )
        rows = cur.fetchall()
        return [
            {
                "id": int(r[0]),
                "group_id": str(r[1]),
                "category_id": str(r[2]),
                "item_id": str(r[3]),
                "item_name": str(r[4]),
                "price": int(r[5]),
                "quantity": int(r[6]),
                "seller_user_id": str(r[7]),
                "is_system": int(r[8]),
                "day_key": str(r[9]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def db_consume_market_listing(db_path: Path, listing_id: int, quantity: int) -> bool:
    need = max(1, int(quantity))
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT quantity FROM market_listing WHERE id=?", (int(listing_id),))
        row = cur.fetchone()
        if not row:
            return False
        have = int(row[0])
        if have < need:
            return False
        remain = have - need
        if remain > 0:
            conn.execute("UPDATE market_listing SET quantity=? WHERE id=?", (remain, int(listing_id)))
        else:
            conn.execute("DELETE FROM market_listing WHERE id=?", (int(listing_id),))
        conn.commit()
        return True
    finally:
        conn.close()


def db_delete_expired_system_listings(db_path: Path, group_id: str, day_key: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM market_listing WHERE group_id=? AND is_system=1 AND day_key<>?",
            (group_id, day_key),
        )
        conn.commit()
    finally:
        conn.close()
