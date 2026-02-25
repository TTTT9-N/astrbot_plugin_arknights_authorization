import json
import random
from pathlib import Path
from typing import Dict, List

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_arknights_authorization", "codex", "明日方舟通行证盲盒互动插件", "1.1.0")
class ArknightsBlindBoxPlugin(Star):
    """明日方舟通行证盲盒互动插件。"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.base_dir = Path(__file__).resolve().parent
        self.data_dir = self.base_dir / "data"
        self.config_path = self.data_dir / "box_config.json"
        self.state_path = self.data_dir / "pool_state.json"
        self.slot_state_path = self.data_dir / "slot_state.json"
        self.session_path = self.data_dir / "sessions.json"

        self.config: Dict[str, dict] = {}
        self.pool_state: Dict[str, List[str]] = {}
        self.slot_state: Dict[str, List[int]] = {}
        self.sessions: Dict[str, str] = {}

    async def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default_config()
        self._load_all()
        self._ensure_states_initialized()
        logger.info("[arknights_blindbox] 插件初始化完成。")

    @filter.command("方舟盲盒")
    async def arknights_blindbox(self, event: AstrMessageEvent):
        """明日方舟通行证盲盒：列表/选择/开启/状态/刷新。"""
        args = self._extract_command_args(event.message_str)
        if not args:
            yield event.plain_result(self._build_help_text())
            return

        action = args[0].lower()
        if action in {"列表", "list", "types"}:
            yield event.plain_result(self._build_category_list_text())
            return

        if action in {"选择", "select"}:
            if len(args) < 2:
                yield event.plain_result("请指定盲盒种类ID，例如：/方舟盲盒 选择 vc17")
                return
            category_id = args[1]
            if category_id not in self.config:
                yield event.plain_result(f"不存在种类 `{category_id}`。\n\n{self._build_category_list_text()}")
                return

            session_key = self._build_session_key(event)
            self.sessions[session_key] = category_id
            self._save_json(self.session_path, self.sessions)

            category = self.config[category_id]
            remain_pool = len(self.pool_state.get(category_id, []))
            remain_slots = len(self.slot_state.get(category_id, []))
            slots = int(category.get("slots", 0))

            if remain_pool == 0:
                yield event.plain_result(
                    f"你已选择【{category.get('name', category_id)}】\n"
                    "当前卡池剩余：0\n"
                    "该种类卡池已空。你可以：\n"
                    f"1) /方舟盲盒 刷新 {category_id}\n"
                    "2) /方舟盲盒 列表（换种类）"
                )
                return

            tip_text = (
                f"你已选择【{category.get('name', category_id)}】\n"
                f"当前卡池剩余：{remain_pool}\n"
                f"当前可选序号数：{remain_slots}/{slots}\n"
                f"可选序号：{self._format_available_slots(category_id)}\n"
                "请发送指令：/方舟盲盒 开 <序号>"
            )
            image = category.get("selection_image", "")
            for result in self._build_results_with_optional_image(event, tip_text, image):
                yield result
            return

        if action in {"开", "开启", "open"}:
            if len(args) < 2:
                yield event.plain_result("请提供序号，例如：/方舟盲盒 开 3")
                return
            if not args[1].isdigit():
                yield event.plain_result("序号必须是数字，例如：/方舟盲盒 开 3")
                return

            session_key = self._build_session_key(event)
            category_id = self.sessions.get(session_key)
            if not category_id:
                yield event.plain_result("你还没有选择盲盒种类，请先发送：/方舟盲盒 选择 <种类ID>")
                return
            if category_id not in self.config:
                yield event.plain_result("当前会话中的种类已失效，请重新选择。")
                return

            category = self.config[category_id]
            box_no = int(args[1])
            slots = int(category.get("slots", 0))
            if box_no < 1 or box_no > slots:
                yield event.plain_result(f"序号超出范围，请输入 1 ~ {slots} 之间的数字。")
                return

            draw_pool = self.pool_state.get(category_id, [])
            if not draw_pool:
                yield event.plain_result(
                    f"【{category.get('name', category_id)}】当前卡池剩余：0\n"
                    "该种类卡池已空。你可以：\n"
                    f"1) /方舟盲盒 刷新 {category_id}\n"
                    "2) /方舟盲盒 列表（换种类）"
                )
                return

            available_slots = self.slot_state.get(category_id, [])
            if box_no not in available_slots:
                yield event.plain_result(
                    f"序号 {box_no} 已被选择，当前可选序号：{self._format_available_slots(category_id)}"
                )
                return

            selected_item_id = random.choice(draw_pool)
            draw_pool.remove(selected_item_id)
            available_slots.remove(box_no)
            self._save_json(self.state_path, self.pool_state)
            self._save_json(self.slot_state_path, self.slot_state)

            item = category.get("items", {}).get(selected_item_id, {})
            item_name = item.get("name", selected_item_id)
            item_image = item.get("image", "")

            remain_pool = len(draw_pool)
            remain_slots = len(available_slots)

            msg = (
                f"你选择了第 {box_no} 号盲盒，开启结果：\n"
                f"所属种类：{category.get('name', category_id)}\n"
                f"奖品名称：{item_name}\n"
                f"当前卡池剩余：{remain_pool}\n"
                f"当前可选序号数：{remain_slots}/{slots}"
            )
            for result in self._build_results_with_optional_image(event, msg, item_image):
                yield result

            if remain_pool == 0:
                yield event.plain_result(
                    "⚠️ 当前种类卡池已为 0。\n"
                    f"如需重置请发送：/方舟盲盒 刷新 {category_id}\n"
                    "或发送 /方舟盲盒 列表 选择其他种类。"
                )
            return

        if action in {"刷新", "reset", "refresh"}:
            session_key = self._build_session_key(event)
            category_id = args[1] if len(args) > 1 else self.sessions.get(session_key)
            if not category_id:
                yield event.plain_result("请使用：/方舟盲盒 刷新 <种类ID>，或先选择种类后再刷新。")
                return
            if category_id not in self.config:
                yield event.plain_result(f"不存在种类 `{category_id}`。")
                return

            self._reset_category_state(category_id)
            category = self.config[category_id]
            yield event.plain_result(
                f"【{category.get('name', category_id)}】已刷新。\n"
                f"卡池剩余：{len(self.pool_state.get(category_id, []))}\n"
                f"可选序号：{self._format_available_slots(category_id)}"
            )
            return

        if action in {"状态", "status"}:
            session_key = self._build_session_key(event)
            category_id = args[1] if len(args) > 1 else self.sessions.get(session_key)
            if not category_id:
                yield event.plain_result("请使用：/方舟盲盒 状态 <种类ID> 或先选择种类后再查看状态。")
                return
            if category_id not in self.config:
                yield event.plain_result(f"不存在种类 `{category_id}`。")
                return

            category = self.config[category_id]
            yield event.plain_result(
                f"【{category.get('name', category_id)}】\n"
                f"卡池状态：{len(self.pool_state.get(category_id, []))}/{len(category.get('items', {}))}\n"
                f"序号状态：{len(self.slot_state.get(category_id, []))}/{int(category.get('slots', 0))}"
            )
            return

        yield event.plain_result(self._build_help_text())

    def _extract_command_args(self, raw_message: str) -> List[str]:
        text = (raw_message or "").strip()
        if not text:
            return []
        parts = [p for p in text.split() if p]
        if not parts:
            return []

        first = parts[0].lstrip("/")
        if first == "方舟盲盒":
            return parts[1:]
        return parts

    def _build_help_text(self) -> str:
        return (
            "明日方舟通行证盲盒指令：\n"
            "1) /方舟盲盒 列表\n"
            "2) /方舟盲盒 选择 <种类ID>\n"
            "3) /方舟盲盒 开 <序号>\n"
            "4) /方舟盲盒 状态 [种类ID]\n"
            "5) /方舟盲盒 刷新 [种类ID]"
        )

    def _build_category_list_text(self) -> str:
        if not self.config:
            return "当前没有可用的盲盒种类，请先配置 data/box_config.json"

        lines = ["可用盲盒种类："]
        for category_id, category in self.config.items():
            lines.append(
                f"- {category_id}: {category.get('name', category_id)}"
                f"（卡池: {len(self.pool_state.get(category_id, []))}/{len(category.get('items', {}))}，"
                f"序号: {len(self.slot_state.get(category_id, []))}/{int(category.get('slots', 0))}）"
            )
        lines.append("\n使用：/方舟盲盒 选择 <种类ID>")
        return "\n".join(lines)

    def _build_session_key(self, event: AstrMessageEvent) -> str:
        room = str(getattr(event, "group_id", "") or getattr(event, "session_id", "") or "private")
        user = str(getattr(event, "user_id", "") or getattr(event, "sender_id", "") or "unknown")
        return f"{room}:{user}"

    def _build_results_with_optional_image(self, event: AstrMessageEvent, text: str, image: str):
        image = (image or "").strip()
        if image and hasattr(event, "image_result"):
            return [event.image_result(image), event.plain_result(text)]
        if image:
            return [event.plain_result(f"{text}\n图片：{image}")]
        return [event.plain_result(text)]

    def _format_available_slots(self, category_id: str) -> str:
        slots = self.slot_state.get(category_id, [])
        if not slots:
            return "无"
        if len(slots) <= 20:
            return ", ".join(str(v) for v in slots)
        return f"{slots[0]} ~ {slots[-1]}（共 {len(slots)} 个）"

    def _reset_category_state(self, category_id: str):
        category = self.config.get(category_id, {})
        self.pool_state[category_id] = list(category.get("items", {}).keys())
        slot_count = int(category.get("slots", 0))
        self.slot_state[category_id] = list(range(1, slot_count + 1))
        self._save_json(self.state_path, self.pool_state)
        self._save_json(self.slot_state_path, self.slot_state)

    def _load_all(self):
        self.config = self._load_json(self.config_path, default={})
        self.pool_state = self._load_json(self.state_path, default={})
        self.slot_state = self._load_json(self.slot_state_path, default={})
        self.sessions = self._load_json(self.session_path, default={})

    def _ensure_states_initialized(self):
        changed_pool = False
        changed_slot = False
        for category_id, category in self.config.items():
            if category_id not in self.pool_state or not isinstance(self.pool_state[category_id], list):
                self.pool_state[category_id] = list(category.get("items", {}).keys())
                changed_pool = True
            if category_id not in self.slot_state or not isinstance(self.slot_state[category_id], list):
                slot_count = int(category.get("slots", 0))
                self.slot_state[category_id] = list(range(1, slot_count + 1))
                changed_slot = True

        if changed_pool:
            self._save_json(self.state_path, self.pool_state)
        if changed_slot:
            self._save_json(self.slot_state_path, self.slot_state)

    def _ensure_default_config(self):
        if self.config_path.exists():
            return
        default_config = {
            "vc17": {
                "name": "2024音律联觉通行证盲盒",
                "slots": 14,
                "selection_image": "https://example.com/ak-vc17-selection.jpg",
                "items": {
                    "vc17-01": {"name": "山 通行证卡套", "image": "https://example.com/ak-vc17-01.jpg"},
                    "vc17-02": {"name": "W 通行证卡套", "image": "https://example.com/ak-vc17-02.jpg"},
                    "vc17-03": {"name": "缪尔赛思 通行证卡套", "image": "https://example.com/ak-vc17-03.jpg"}
                }
            },
            "anniv": {
                "name": "周年系列通行证盲盒",
                "slots": 12,
                "selection_image": "https://example.com/ak-anniv-selection.jpg",
                "items": {
                    "anniv-01": {"name": "阿米娅 通行证卡套", "image": "https://example.com/ak-anniv-01.jpg"},
                    "anniv-02": {"name": "能天使 通行证卡套", "image": "https://example.com/ak-anniv-02.jpg"}
                }
            }
        }
        self._save_json(self.config_path, default_config)

    def _load_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as ex:
            logger.warning(f"[arknights_blindbox] 读取 {path.name} 失败：{ex}")
            return default

    def _save_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def terminate(self):
        self._save_json(self.state_path, self.pool_state)
        self._save_json(self.slot_state_path, self.slot_state)
        self._save_json(self.session_path, self.sessions)
        logger.info("[arknights_blindbox] 插件已卸载，状态已保存。")
