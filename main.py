import json
import random
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_arknights_authorization", "codex", "明日方舟通行证盲盒互动插件", "1.3.0")
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
        self.runtime_config_path = self.data_dir / "runtime_config.json"
        self.db_path = self.data_dir / "blindbox.db"

        self.box_config: Dict[str, dict] = {}
        self.pool_state: Dict[str, List[str]] = {}
        self.slot_state: Dict[str, List[int]] = {}
        self.sessions: Dict[str, str] = {}
        self.runtime_config: Dict[str, object] = {}

        self._runtime_config_mtime: float = 0
        self._box_config_mtime: float = 0
        self._last_context_sync: float = 0

    async def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default_config()
        self._ensure_default_runtime_config()
        self._load_all()
        self._sync_runtime_config_from_context()
        self._ensure_states_initialized()
        self._init_db()
        logger.info("[arknights_blindbox] 插件初始化完成。")

    @filter.command("方舟盲盒")
    async def arknights_blindbox(self, event: AstrMessageEvent):
        """明日方舟通行证盲盒：注册/钱包/列表/选择/开/状态/刷新/管理员。"""
        self._maybe_reload_runtime_data()
        self._sync_runtime_config_from_context()

        args = self._extract_command_args(event.message_str)
        if not args:
            yield event.plain_result(self._build_help_text())
            return

        action = args[0].lower()

        if action in {"注册", "signup", "reg"}:
            identity = self._get_identity(event)
            if identity is None:
                yield event.plain_result("无法识别你的账号ID，暂时无法注册。")
                return
            group_id, user_id = identity
            if self._db_get_user(group_id, user_id) is not None:
                balance = self._db_get_balance(group_id, user_id)
                yield event.plain_result(f"你已注册，当前余额：{balance} 元")
                return

            init_balance = int(self.runtime_config.get("initial_balance", 200))
            self._db_register_user(group_id, user_id, init_balance)
            yield event.plain_result(f"注册成功，初始余额：{init_balance} 元\n当前群：{group_id}")
            return

        if action in {"钱包", "balance", "money"}:
            identity = self._get_identity(event)
            if identity is None:
                yield event.plain_result("无法识别你的账号ID，暂时无法查询钱包。")
                return
            group_id, user_id = identity
            balance = self._db_get_balance(group_id, user_id)
            if balance is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return
            yield event.plain_result(f"当前余额：{balance} 元\n当前群：{group_id}")
            return

        if action in {"列表", "list", "types"}:
            yield event.plain_result(self._build_category_list_text())
            return

        # 需要注册后才能开始玩法
        if action in {"选择", "开", "开启", "open", "状态", "status", "刷新", "reset", "refresh"}:
            identity = self._get_identity(event)
            if identity is None:
                yield event.plain_result("无法识别你的账号ID，暂时无法进行盲盒操作。")
                return
            group_id, user_id = identity
            if self._db_get_user(group_id, user_id) is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return

        if action in {"选择", "select"}:
            if len(args) < 2:
                yield event.plain_result("请指定盲盒种类ID，例如：/方舟盲盒 选择 num_vc17")
                return
            category_id = args[1]
            if category_id not in self.box_config:
                yield event.plain_result(f"不存在种类 `{category_id}`。\n\n{self._build_category_list_text()}")
                return

            session_key = self._build_session_key(event)
            self.sessions[session_key] = category_id
            self._save_json(self.session_path, self.sessions)

            category = self.box_config[category_id]
            remain_pool = len(self.pool_state.get(category_id, []))
            remain_slots = len(self.slot_state.get(category_id, []))
            slots = int(category.get("slots", 0))
            price = self._get_category_price(category_id)

            if remain_pool == 0 or remain_slots == 0:
                yield event.plain_result(
                    f"你已选择【{category.get('name', category_id)}】\n"
                    f"当前卡池剩余：{remain_pool}\n"
                    f"当前可选序号数：{remain_slots}/{slots}\n"
                    f"当前单抽价格：{price} 元\n"
                    "该种类已不可继续开启。你可以：\n"
                    f"1) /方舟盲盒 刷新 {category_id}\n"
                    "2) /方舟盲盒 列表（换种类）"
                )
                return

            tip_text = (
                f"你已选择【{category.get('name', category_id)}】\n"
                f"当前卡池剩余：{remain_pool}\n"
                f"当前可选序号数：{remain_slots}/{slots}\n"
                f"当前单抽价格：{price} 元\n"
                f"可选序号：{self._format_available_slots(category_id)}\n"
                "请发送指令：/方舟盲盒 开 <序号>"
            )
            image = category.get("selection_image", "")
            for result in self._build_results_with_optional_image(event, tip_text, image):
                yield result
            return

        if action in {"开", "开启", "open"}:
            identity = self._get_identity(event)
            assert identity is not None
            group_id, user_id = identity
            balance = self._db_get_balance(group_id, user_id)
            if balance is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return

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
            if category_id not in self.box_config:
                yield event.plain_result("当前会话中的种类已失效，请重新选择。")
                return

            category = self.box_config[category_id]
            box_no = int(args[1])
            slots = int(category.get("slots", 0))
            if box_no < 1 or box_no > slots:
                yield event.plain_result(f"序号超出范围，请输入 1 ~ {slots} 之间的数字。")
                return

            draw_pool = self.pool_state.get(category_id, [])
            available_slots = self.slot_state.get(category_id, [])
            if not draw_pool or not available_slots:
                yield event.plain_result(
                    f"【{category.get('name', category_id)}】当前卡池或序号已耗尽。\n"
                    f"请发送：/方舟盲盒 刷新 {category_id}，或切换种类。"
                )
                return
            if box_no not in available_slots:
                yield event.plain_result(
                    f"序号 {box_no} 已被选择，当前可选序号：{self._format_available_slots(category_id)}"
                )
                return

            price = self._get_category_price(category_id)
            if balance < price:
                yield event.plain_result(
                    f"余额不足，当前余额：{balance} 元，当前种类单抽价格：{price} 元。"
                )
                return

            selected_item_id = random.choice(draw_pool)
            draw_pool.remove(selected_item_id)
            available_slots.remove(box_no)
            self._db_update_balance(group_id, user_id, balance - price)

            self._save_json(self.state_path, self.pool_state)
            self._save_json(self.slot_state_path, self.slot_state)

            item = category.get("items", {}).get(selected_item_id, {})
            item_name = item.get("name", selected_item_id)
            item_image = item.get("image", "")

            remain_pool = len(draw_pool)
            remain_slots = len(available_slots)
            new_balance = self._db_get_balance(group_id, user_id)
            msg = (
                f"你选择了第 {box_no} 号盲盒，开启结果：\n"
                f"所属种类：{category.get('name', category_id)}\n"
                f"奖品名称：{item_name}\n"
                f"当前卡池剩余：{remain_pool}\n"
                f"当前可选序号数：{remain_slots}/{slots}\n"
                f"本次花费：{price} 元，当前余额：{new_balance} 元\n"
                f"当前群：{group_id}"
            )
            for result in self._build_results_with_optional_image(event, msg, item_image):
                yield result

            if remain_pool == 0 or remain_slots == 0:
                yield event.plain_result(
                    "⚠️ 当前种类已不可继续开启。\n"
                    f"如需重置请发送：/方舟盲盒 刷新 {category_id}\n"
                    "或发送 /方舟盲盒 列表 选择其他种类。"
                )
            return

        if action in {"刷新", "reset", "refresh"}:
            if len(args) < 2:
                session_key = self._build_session_key(event)
                category_id = self.sessions.get(session_key)
                if not category_id:
                    yield event.plain_result("请使用：/方舟盲盒 刷新 <种类ID>，或先选择种类后再刷新。")
                    return
            else:
                category_id = args[1]

            if category_id not in self.box_config:
                yield event.plain_result(f"不存在种类 `{category_id}`。")
                return

            self._reset_category_state(category_id)
            category = self.box_config[category_id]
            yield event.plain_result(
                f"【{category.get('name', category_id)}】已刷新。\n"
                f"卡池剩余：{len(self.pool_state.get(category_id, []))}\n"
                f"可选序号：{self._format_available_slots(category_id)}"
            )
            return

        if action in {"状态", "status"}:
            if len(args) < 2:
                session_key = self._build_session_key(event)
                category_id = self.sessions.get(session_key)
                if not category_id:
                    yield event.plain_result("请使用：/方舟盲盒 状态 <种类ID> 或先选择种类后再查看状态。")
                    return
            else:
                category_id = args[1]

            if category_id not in self.box_config:
                yield event.plain_result(f"不存在种类 `{category_id}`。")
                return

            category = self.box_config[category_id]
            identity = self._get_identity(event)
            assert identity is not None
            group_id, user_id = identity
            balance = self._db_get_balance(group_id, user_id)
            yield event.plain_result(
                f"【{category.get('name', category_id)}】\n"
                f"卡池状态：{len(self.pool_state.get(category_id, []))}/{len(category.get('items', {}))}\n"
                f"序号状态：{len(self.slot_state.get(category_id, []))}/{int(category.get('slots', 0))}\n"
                f"单抽价格：{self._get_category_price(category_id)} 元\n"
                f"你的余额：{balance if balance is not None else '未注册'}\n"
                f"当前群：{group_id}"
            )
            return

        if action in {"管理员", "admin"}:
            for result in self._handle_admin_command(event, args[1:]):
                yield result
            return

        if action in {"重载配置", "reload"}:
            if not self._is_admin(event):
                yield event.plain_result("仅管理员可执行该命令。")
                return
            self._load_all()
            self._ensure_states_initialized()
            yield event.plain_result("配置与状态已重载。")
            return

        yield event.plain_result(self._build_help_text())

    def _handle_admin_command(self, event: AstrMessageEvent, args: List[str]):
        if not args:
            return [event.plain_result("管理员指令：列表/添加 <user_id>/移除 <user_id>/特殊定价 <种类ID> <金额>")]

        action = args[0]
        current_user = self._get_identity(event)
        if current_user is None:
            return [event.plain_result("无法识别你的账号ID，无法执行管理员操作。")]
        _, current_user_id = current_user
        admins = self._get_admin_ids()

        if action == "列表":
            return [event.plain_result(f"管理员列表：{', '.join(admins) if admins else '暂无'}")]

        if action in {"添加", "add"}:
            if len(args) < 2:
                return [event.plain_result("用法：/方舟盲盒 管理员 添加 <user_id>")]
            target = args[1]
            if admins and current_user_id not in admins:
                return [event.plain_result("仅管理员可添加管理员。")]
            if target not in admins:
                admins.append(target)
                self.runtime_config["admin_ids"] = admins
                self._save_json(self.runtime_config_path, self.runtime_config)
            return [event.plain_result(f"已添加管理员：{target}")]

        if action in {"移除", "remove"}:
            if len(args) < 2:
                return [event.plain_result("用法：/方舟盲盒 管理员 移除 <user_id>")]
            target = args[1]
            if current_user_id not in admins:
                return [event.plain_result("仅管理员可移除管理员。")]
            if target in admins:
                admins.remove(target)
                self.runtime_config["admin_ids"] = admins
                self._save_json(self.runtime_config_path, self.runtime_config)
            return [event.plain_result(f"已移除管理员：{target}")]

        if action in {"特殊定价", "setprice"}:
            if len(args) < 3:
                return [event.plain_result("用法：/方舟盲盒 管理员 特殊定价 <种类ID> <金额>")]
            if current_user_id not in admins:
                return [event.plain_result("仅管理员可设置特殊盒价格。")]

            category_id = args[1]
            if category_id not in self.box_config:
                return [event.plain_result(f"不存在种类 `{category_id}`")]
            if not args[2].isdigit() or int(args[2]) < 0:
                return [event.plain_result("金额必须为非负整数。")]

            category = self.box_config[category_id]
            if category.get("box_type", "number") != "special":
                return [event.plain_result("该种类不是特殊盒（box_type=special），无需设置特殊定价。")]

            special_prices = self.runtime_config.get("special_box_prices", {})
            special_prices[category_id] = int(args[2])
            self.runtime_config["special_box_prices"] = special_prices
            self._save_json(self.runtime_config_path, self.runtime_config)
            return [event.plain_result(f"已设置特殊盒 {category_id} 单抽价格：{args[2]} 元")]

        return [event.plain_result("未知管理员指令。")]

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
            "1) /方舟盲盒 注册\n"
            "2) /方舟盲盒 钱包\n"
            "3) /方舟盲盒 列表\n"
            "4) /方舟盲盒 选择 <种类ID>\n"
            "5) /方舟盲盒 开 <序号>\n"
            "6) /方舟盲盒 状态 [种类ID]\n"
            "7) /方舟盲盒 刷新 [种类ID]\n"
            "8) /方舟盲盒 管理员 ...（列表/添加/移除/特殊定价）"
        )

    def _build_category_list_text(self) -> str:
        if not self.box_config:
            return "当前没有可用的盲盒种类，请先配置 data/box_config.json"

        lines = ["可用盲盒种类："]
        for category_id, category in self.box_config.items():
            box_type = category.get("box_type", "number")
            lines.append(
                f"- {category_id}: {category.get('name', category_id)}"
                f"（类型: {box_type}，价格: {self._get_category_price(category_id)} 元，"
                f"卡池: {len(self.pool_state.get(category_id, []))}/{len(category.get('items', {}))}，"
                f"序号: {len(self.slot_state.get(category_id, []))}/{int(category.get('slots', 0))}）"
            )
        lines.append("\n使用：/方舟盲盒 选择 <种类ID>")
        return "\n".join(lines)

    def _build_session_key(self, event: AstrMessageEvent) -> str:
        group_id = str(getattr(event, "group_id", "") or getattr(event, "session_id", "") or "private")
        user_id = str(getattr(event, "user_id", "") or getattr(event, "sender_id", "") or "")
        return f"{group_id}:{user_id or 'unknown'}"

    def _get_identity(self, event: AstrMessageEvent) -> Optional[Tuple[str, str]]:
        user_id = str(getattr(event, "user_id", "") or getattr(event, "sender_id", "") or "")
        if not user_id or user_id == "unknown":
            return None
        group_id = str(getattr(event, "group_id", "") or getattr(event, "session_id", "") or "private")
        return group_id, user_id

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        identity = self._get_identity(event)
        if identity is None:
            return False
        _, user_id = identity
        return user_id in self._get_admin_ids()

    def _get_admin_ids(self) -> List[str]:
        return [str(v) for v in self.runtime_config.get("admin_ids", [])]

    def _get_category_price(self, category_id: str) -> int:
        category = self.box_config.get(category_id, {})
        box_type = category.get("box_type", "number")
        if box_type == "number":
            return int(self.runtime_config.get("number_box_price", 25))

        special_prices = self.runtime_config.get("special_box_prices", {})
        if category_id in special_prices:
            return int(special_prices[category_id])
        return int(category.get("price", self.runtime_config.get("special_box_default_price", 40)))

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
        category = self.box_config.get(category_id, {})
        self.pool_state[category_id] = list(category.get("items", {}).keys())
        slot_count = int(category.get("slots", 0))
        self.slot_state[category_id] = list(range(1, slot_count + 1))
        self._save_json(self.state_path, self.pool_state)
        self._save_json(self.slot_state_path, self.slot_state)

    def _load_all(self):
        self.box_config = self._load_json(self.config_path, default={})
        self.pool_state = self._load_json(self.state_path, default={})
        self.slot_state = self._load_json(self.slot_state_path, default={})
        self.sessions = self._load_json(self.session_path, default={})
        self.runtime_config = self._load_json(self.runtime_config_path, default={})

        self._runtime_config_mtime = self._safe_mtime(self.runtime_config_path)
        self._box_config_mtime = self._safe_mtime(self.config_path)

    def _ensure_states_initialized(self):
        changed_pool = False
        changed_slot = False
        for category_id, category in self.box_config.items():
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

    def _maybe_reload_runtime_data(self):
        runtime_mtime = self._safe_mtime(self.runtime_config_path)
        box_mtime = self._safe_mtime(self.config_path)
        if runtime_mtime > self._runtime_config_mtime:
            self.runtime_config = self._load_json(self.runtime_config_path, default=self.runtime_config)
            self._runtime_config_mtime = runtime_mtime
            logger.info("[arknights_blindbox] 已自动重载 runtime_config.json")

        if box_mtime > self._box_config_mtime:
            self.box_config = self._load_json(self.config_path, default=self.box_config)
            self._ensure_states_initialized()
            self._box_config_mtime = box_mtime
            logger.info("[arknights_blindbox] 已自动重载 box_config.json")

    def _sync_runtime_config_from_context(self):
        now = time.time()
        if now - self._last_context_sync < 3:
            return
        self._last_context_sync = now

        # 兼容不同 AstrBot 版本的配置读取函数
        conf = None
        for getter_name in ("get_config", "get_plugin_config", "get_star_config"):
            getter = getattr(self.context, getter_name, None)
            if callable(getter):
                try:
                    conf = getter()
                    break
                except Exception as ex:
                    logger.warning(f"[arknights_blindbox] 读取 WebUI 配置失败({getter_name})：{ex}")

        if not isinstance(conf, dict) or not conf:
            return

        merged = dict(self.runtime_config)
        for key in ["initial_balance", "number_box_price", "special_box_default_price", "admin_ids", "special_box_prices"]:
            if key in conf:
                merged[key] = conf[key]

        if merged != self.runtime_config:
            self.runtime_config = merged
            self._save_json(self.runtime_config_path, self.runtime_config)
            logger.info("[arknights_blindbox] 已同步并保存 WebUI 插件配置")

    def _ensure_default_runtime_config(self):
        if self.runtime_config_path.exists():
            return
        self._save_json(
            self.runtime_config_path,
            {
                "initial_balance": 200,
                "number_box_price": 25,
                "special_box_default_price": 40,
                "admin_ids": [],
                "special_box_prices": {},
            },
        )

    def _ensure_default_config(self):
        if self.config_path.exists():
            return
        default_config = {
            "num_vc17": {
                "name": "2024音律联觉通行证盲盒（数字盒）",
                "box_type": "number",
                "slots": 14,
                "selection_image": "https://example.com/ak-vc17-selection.jpg",
                "items": {
                    "vc17-01": {"name": "山 通行证卡套", "image": "https://example.com/ak-vc17-01.jpg"},
                    "vc17-02": {"name": "W 通行证卡套", "image": "https://example.com/ak-vc17-02.jpg"},
                    "vc17-03": {"name": "缪尔赛思 通行证卡套", "image": "https://example.com/ak-vc17-03.jpg"},
                },
            },
            "sp_anniv": {
                "name": "周年系列通行证盲盒（特殊盒）",
                "box_type": "special",
                "price": 68,
                "slots": 12,
                "selection_image": "https://example.com/ak-anniv-selection.jpg",
                "items": {
                    "anniv-01": {"name": "阿米娅 通行证卡套", "image": "https://example.com/ak-anniv-01.jpg"},
                    "anniv-02": {"name": "能天使 通行证卡套", "image": "https://example.com/ak-anniv-02.jpg"},
                },
            },
        }
        self._save_json(self.config_path, default_config)

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
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
            conn.commit()
        finally:
            conn.close()

    def _db_get_user(self, group_id: str, user_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT group_id, user_id, balance, registered_at FROM user_wallet WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            )
            return cur.fetchone()
        finally:
            conn.close()

    def _db_get_balance(self, group_id: str, user_id: str) -> Optional[int]:
        row = self._db_get_user(group_id, user_id)
        return int(row[2]) if row else None

    def _db_register_user(self, group_id: str, user_id: str, balance: int):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO user_wallet(group_id, user_id, balance, registered_at) VALUES (?, ?, ?, ?)",
                (group_id, user_id, int(balance), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()

    def _db_update_balance(self, group_id: str, user_id: str, balance: int):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE user_wallet SET balance=? WHERE group_id=? AND user_id=?",
                (int(balance), group_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _safe_mtime(self, path: Path) -> float:
        return path.stat().st_mtime if path.exists() else 0

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
        self._save_json(self.runtime_config_path, self.runtime_config)
        logger.info("[arknights_blindbox] 插件已卸载，状态已保存。")
