"""Resource scanning and parsing helpers for blind-box plugin."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def scan_categories(number_box_dir: Path, special_box_dir: Path, guide_candidates: List[str]) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    for box_type, root in (("number", number_box_dir), ("special", special_box_dir)):
        if not root.exists():
            continue
        for cat_dir in root.iterdir():
            if not cat_dir.is_dir():
                continue
            category_id = cat_dir.name
            guide = find_guide_image(cat_dir, guide_candidates)
            items, slots = parse_prize_items(cat_dir, guide_candidates)
            if not items or not slots:
                continue
            result[category_id] = {
                "id": category_id,
                "box_type": box_type,
                "guide_image": guide,
                "items": items,
                "slot_total": len(slots),
                "slots": sorted(slots),
                "signature": build_category_signature(list(items.keys()), slots),
            }
    return result


def find_guide_image(cat_dir: Path, guide_candidates: List[str]) -> Optional[Path]:
    for n in guide_candidates:
        p = cat_dir / n
        if p.exists():
            return p
    return None


def parse_prize_items(cat_dir: Path, guide_candidates: List[str]) -> Tuple[Dict[str, dict], List[int]]:
    slots: List[int] = []
    items: Dict[str, dict] = {}
    pattern = re.compile(r"^(\d+)[-_](.+)$")
    for f in sorted(cat_dir.iterdir()):
        if not f.is_file():
            continue
        if f.name in guide_candidates:
            continue
        if f.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        m = pattern.match(f.stem)
        if not m:
            continue
        slot_no = int(m.group(1))
        display_name = m.group(2).strip() or f.stem
        item_id = f.name
        items[item_id] = {"name": display_name, "image": f, "slot_no": slot_no}
        if slot_no not in slots:
            slots.append(slot_no)
    return items, sorted(slots)


def build_category_signature(item_ids: List[str], slots: List[int]) -> str:
    return "|".join(sorted(item_ids)) + "::" + ",".join(map(str, sorted(slots)))
