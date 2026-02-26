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
