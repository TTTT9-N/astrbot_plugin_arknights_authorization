"""Resource index helpers for market/recycle per-box pricing."""

from pathlib import Path
from typing import Dict
import json


def build_box_index(categories: Dict[str, dict]) -> Dict[str, dict]:
    data: Dict[str, dict] = {}
    for category_id, category in categories.items():
        items = category.get("items", {})
        boxes = []
        for item_id, item in items.items():
            boxes.append(
                {
                    "item_id": item_id,
                    "name": str(item.get("name", item_id)),
                    "slot_no": int(item.get("slot_no", 0)),
                }
            )
        boxes.sort(key=lambda x: (x.get("slot_no", 0), x.get("item_id", "")))
        data[category_id] = {
            "box_type": category.get("box_type", ""),
            "box_count": len(boxes),
            "boxes": boxes,
        }
    return data


def sync_box_index_file(path: Path, categories: Dict[str, dict]) -> Dict[str, dict]:
    index_data = build_box_index(categories)
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    if old != index_data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_data
