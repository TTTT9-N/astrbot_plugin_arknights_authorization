import sqlite3
from pathlib import Path
from typing import List, Tuple


def init_inventory_table(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_inventory (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                category_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (group_id, user_id, category_id, item_name)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def add_inventory_item(db_path: Path, group_id: str, user_id: str, category_id: str, item_name: str, count: int = 1):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO user_inventory(group_id,user_id,category_id,item_name,count)
            VALUES (?,?,?,?,?)
            ON CONFLICT(group_id,user_id,category_id,item_name)
            DO UPDATE SET count = count + excluded.count
            """,
            (group_id, user_id, category_id, item_name, int(count)),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_inventory(db_path: Path, group_id: str, user_id: str) -> List[Tuple[str, str, int]]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT category_id,item_name,count FROM user_inventory WHERE group_id=? AND user_id=? ORDER BY category_id,item_name",
            (group_id, user_id),
        )
        return [(str(r[0]), str(r[1]), int(r[2])) for r in cur.fetchall()]
    finally:
        conn.close()
