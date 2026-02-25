import json
import random
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_arknights_authorization", "codex", "明日方舟通行证盲盒互动插件", "1.4.1")
class ArknightsBlindBoxPlugin(Star):
    """明日方舟通行证盲盒互动插件。"""

    GUIDE_CANDIDATES = ["selection.jpg", "selection.png", "cover.jpg", "cover.png"]

    def __init__(self, context: Context):
        super().__init__(context)
        self.base_dir = Path(__file__).resolve().parent
        self.legacy_data_dir = self.base_dir / "data"
        self.data_dir = self._resolve_persistent_data_dir()

        self.runtime_config_path = self.data_dir / "runtime_config.json"
        self.session_path = self.data_dir / "sessions.json"
        self.db_path = self.data_dir / "blindbox.db"

        self.resource_dir = self.data_dir / "资源"
        self.number_box_dir = self.resource_dir / "数字盒"
        self.special_box_dir = self.resource_dir / "特殊盒"
        self.revealed_dir = self.resource_dir / "开出盲盒"

        self.sessions: Dict[str, str] = {}
        self.runtime_config: Dict[str, object] = {}
        self.categories: Dict[str, dict] = {}

        self._runtime_config_mtime: float = 0
        self._last_context_sync: float = 0

    async def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_data_if_needed()
        self._ensure_resource_dirs()
        self._ensure_default_runtime_config()
        self._load_all()
        self._sync_runtime_config_from_context()
        self._init_db()
        self._refresh_categories_and_states()
        logger.info("[arknights_blindbox] 插件初始化完成。")

    @filter.command("方舟盲盒")
    async def arknights_blindbox(self, event: AstrMessageEvent):
        self._maybe_reload_runtime_data()
        self._sync_runtime_config_from_context()
        self._refresh_categories_and_states()

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
                yield event.plain_result(f"你已注册，当前余额：{self._db_get_balance(group_id, user_id)} 元")
                return
            balance = int(self.runtime_config.get("initial_balance", 200))
            self._db_register_user(group_id, user_id, balance)
            yield event.plain_result(f"注册成功，初始余额：{balance} 元\n当前群：{group_id}")
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

        if action in {"资源路径", "资源目录", "path"}:
            yield event.plain_result(
                "资源目录如下（首次加载会自动创建）：\n"
                f"- {self.number_box_dir}\n"
                f"- {self.special_box_dir}\n"
                f"- {self.revealed_dir}"
            )
            return

        if action in {"重载资源", "reload", "reload_resources"}:
            self._refresh_categories_and_states(force_sync_legacy=True)
            yield event.plain_result(
                "资源已重新扫描。\n"
                f"当前已加载种类数：{len(self.categories)}\n"
                "可发送 /方舟盲盒 列表 查看最新种类。"
            )
            return

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
            category = self.categories.get(category_id)
            if not category:
                yield event.plain_result(f"不存在种类 `{category_id}`。\n\n{self._build_category_list_text()}")
                return

            self.sessions[self._build_session_key(event)] = category_id
            self._save_json(self.session_path, self.sessions)

            remain_items, remain_slots = self._db_get_category_state(category_id)
            price = self._get_category_price(category_id)

            if not remain_items or not remain_slots:
                yield event.plain_result(
                    f"你已选择【{category['name']}】\n"
                    f"当前卡池剩余：{len(remain_items)}\n"
                    f"当前可选序号数：{len(remain_slots)}/{category['slot_total']}\n"
                    f"当前单抽价格：{price} 元\n"
                    "该种类已不可继续开启。你可以：\n"
                    f"1) /方舟盲盒 刷新 {category_id}\n"
                    "2) /方舟盲盒 列表（换种类）"
                )
                return

            tip = (
                f"你已选择【{category['name']}】\n"
                f"当前卡池剩余：{len(remain_items)}\n"
                f"当前可选序号数：{len(remain_slots)}/{category['slot_total']}\n"
                f"当前单抽价格：{price} 元\n"
                f"可选序号：{self._format_slots(remain_slots)}\n"
                "请发送指令：/方舟盲盒 开 <序号>"
            )
            for r in self._build_results_with_optional_image(event, tip, category.get("guide_image")):
                yield r
            return

        if action in {"开", "开启", "open"}:
            identity = self._get_identity(event)
            assert identity is not None
            group_id, user_id = identity

            if len(args) < 2 or not args[1].isdigit():
                yield event.plain_result("请提供数字序号，例如：/方舟盲盒 开 3")
                return
            choose_slot = int(args[1])

            category_id = self.sessions.get(self._build_session_key(event))
            if not category_id or category_id not in self.categories:
                yield event.plain_result("你还没有选择盲盒种类，请先发送：/方舟盲盒 选择 <种类ID>")
                return
            category = self.categories[category_id]

            remain_items, remain_slots = self._db_get_category_state(category_id)
            if not remain_items or not remain_slots:
                yield event.plain_result(f"【{category['name']}】卡池或序号已耗尽，请发送：/方舟盲盒 刷新 {category_id}")
                return
            if choose_slot not in remain_slots:
                yield event.plain_result(f"序号 {choose_slot} 已不可用，可选序号：{self._format_slots(remain_slots)}")
                return

            balance = self._db_get_balance(group_id, user_id)
            if balance is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return
            price = self._get_category_price(category_id)
            if balance < price:
                yield event.plain_result(f"余额不足，当前余额：{balance} 元，当前单抽价格：{price} 元")
                return

            selected = random.choice(remain_items)
            remain_items.remove(selected)
            remain_slots.remove(choose_slot)
            self._db_set_category_state(category_id, remain_items, remain_slots)
            self._db_update_balance(group_id, user_id, balance - price)

            item = category["items"][selected]
            prize_image = self._record_revealed_image(item.get("image"), group_id, user_id)
            msg = (
                f"你选择了第 {choose_slot} 号盲盒，开启结果：\n"
                f"所属种类：{category['name']}\n"
                f"奖品名称：{item['name']}\n"
                f"当前卡池剩余：{len(remain_items)}\n"
                f"当前可选序号数：{len(remain_slots)}/{category['slot_total']}\n"
                f"当前可选序号：{self._format_slots(remain_slots)}\n"
                f"本次花费：{price} 元，当前余额：{self._db_get_balance(group_id, user_id)} 元\n"
                f"当前群：{group_id}"
            )
            for r in self._build_results_with_optional_image(event, msg, prize_image):
                yield r
            return

        if action in {"刷新", "reset", "refresh"}:
            category_id = args[1] if len(args) > 1 else self.sessions.get(self._build_session_key(event))
            if not category_id or category_id not in self.categories:
                yield event.plain_result("请使用：/方舟盲盒 刷新 <种类ID>")
                return
            self._db_reset_category_state(category_id, self.categories[category_id])
            remain_items, remain_slots = self._db_get_category_state(category_id)
            yield event.plain_result(
                f"【{self.categories[category_id]['name']}】已刷新。\n"
                f"卡池剩余：{len(remain_items)}\n"
                f"可选序号：{self._format_slots(remain_slots)}"
            )
            return

        if action in {"状态", "status"}:
            category_id = args[1] if len(args) > 1 else self.sessions.get(self._build_session_key(event))
            if not category_id or category_id not in self.categories:
                yield event.plain_result("请使用：/方舟盲盒 状态 <种类ID>")
                return
            identity = self._get_identity(event)
            assert identity is not None
            group_id, user_id = identity
            remain_items, remain_slots = self._db_get_category_state(category_id)
            category = self.categories[category_id]
            yield event.plain_result(
                f"【{category['name']}】\n"
                f"卡池状态：{len(remain_items)}/{len(category['items'])}\n"
                f"序号状态：{len(remain_slots)}/{category['slot_total']}\n"
                f"单抽价格：{self._get_category_price(category_id)} 元\n"
                f"你的余额：{self._db_get_balance(group_id, user_id)}\n"
                f"当前群：{group_id}"
            )
            return

        if action in {"管理员", "admin"}:
            for r in self._handle_admin_command(event, args[1:]):
                yield r
            return

        yield event.plain_result(self._build_help_text())

    def _handle_admin_command(self, event: AstrMessageEvent, args: List[str]):
        if not args:
            return [event.plain_result("管理员指令：列表/添加 <user_id>/移除 <user_id>/特殊定价 <种类ID> <金额>")]

        identity = self._get_identity(event)
        if identity is None:
            return [event.plain_result("无法识别你的账号ID，无法执行管理员操作。")]
        _, current_user_id = identity
        admins = self._get_admin_ids()
        action = args[0]

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
            category_id, amount = args[1], args[2]
            if category_id not in self.categories:
                return [event.plain_result(f"不存在种类 `{category_id}`")]
            if self.categories[category_id]["box_type"] != "special":
                return [event.plain_result("该种类不是特殊盒。")]
            if not amount.isdigit() or int(amount) < 0:
                return [event.plain_result("金额必须是非负整数。")]
            sp = self.runtime_config.get("special_box_prices", {})
            sp[category_id] = int(amount)
            self.runtime_config["special_box_prices"] = sp
            self._save_json(self.runtime_config_path, self.runtime_config)
            return [event.plain_result(f"已设置特殊盒 {category_id} 价格：{amount} 元")]

        return [event.plain_result("未知管理员指令。")]

    def _extract_command_args(self, raw_message: str) -> List[str]:
        text = (raw_message or "").strip()
        if not text:
            return []
        parts = [p for p in text.split() if p]
        first = parts[0].lstrip("/") if parts else ""
        return parts[1:] if first == "方舟盲盒" else parts

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
            "8) /方舟盲盒 重载资源\n"
            "9) /方舟盲盒 资源路径\n"
            "10) /方舟盲盒 管理员 ..."
        )

    def _build_category_list_text(self) -> str:
        if not self.categories:
            return "当前未发现盲盒资源。请先在 资源/数字盒 或 资源/特殊盒 下放入资源。"
        lines = ["可用盲盒种类："]
        for category_id, category in self.categories.items():
            remain_items, remain_slots = self._db_get_category_state(category_id)
            lines.append(
                f"- {category_id}: {category['name']}（类型: {category['box_type']}，价格: {self._get_category_price(category_id)} 元，"
                f"卡池: {len(remain_items)}/{len(category['items'])}，序号: {len(remain_slots)}/{category['slot_total']}）"
            )
        lines.append("\n使用：/方舟盲盒 选择 <种类ID>")
        return "\n".join(lines)

    def _build_session_key(self, event: AstrMessageEvent) -> str:
        identity = self._get_identity(event)
        if identity is None:
            return "private:unknown"
        return f"{identity[0]}:{identity[1]}"

    def _get_identity(self, event: AstrMessageEvent) -> Optional[Tuple[str, str]]:
        candidates_user = [
            getattr(event, "user_id", None),
            getattr(event, "sender_id", None),
            getattr(event, "from_user_id", None),
            getattr(event, "author_id", None),
        ]
        for getter_name in ("get_sender_id", "get_user_id", "get_author_id"):
            getter = getattr(event, getter_name, None)
            if callable(getter):
                try:
                    candidates_user.append(getter())
                except Exception:
                    pass

        message_obj = getattr(event, "message_obj", None)
        if isinstance(message_obj, dict):
            sender = message_obj.get("sender")
            if isinstance(sender, dict):
                candidates_user.extend([sender.get("user_id"), sender.get("id"), sender.get("uin")])
            candidates_user.extend([message_obj.get("user_id"), message_obj.get("sender_id")])

        user_id = ""
        for value in candidates_user:
            t = str(value or "").strip()
            if t and t.lower() not in {"unknown", "none", "null"}:
                user_id = t
                break
        if not user_id:
            return None

        candidates_group = [getattr(event, "group_id", None), getattr(event, "session_id", None)]
        for getter_name in ("get_group_id", "get_session_id"):
            getter = getattr(event, getter_name, None)
            if callable(getter):
                try:
                    candidates_group.append(getter())
                except Exception:
                    pass
        if isinstance(message_obj, dict):
            candidates_group.extend([message_obj.get("group_id"), message_obj.get("conversation_id")])

        group_id = "private"
        for value in candidates_group:
            t = str(value or "").strip()
            if t and t.lower() not in {"none", "null"}:
                group_id = t
                break
        return group_id, user_id

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        identity = self._get_identity(event)
        return bool(identity and identity[1] in self._get_admin_ids())

    def _get_admin_ids(self) -> List[str]:
        return [str(v) for v in self.runtime_config.get("admin_ids", [])]

    def _get_category_price(self, category_id: str) -> int:
        category = self.categories.get(category_id, {})
        if category.get("box_type") == "number":
            return int(self.runtime_config.get("number_box_price", 25))
        special_prices = self.runtime_config.get("special_box_prices", {})
        if category_id in special_prices:
            return int(special_prices[category_id])
        return int(self.runtime_config.get("special_box_default_price", 40))

    def _build_results_with_optional_image(self, event: AstrMessageEvent, text: str, image: Optional[Path]):
        image_str = str(image) if image else ""
        if image_str and hasattr(event, "image_result"):
            return [event.image_result(image_str), event.plain_result(text)]
        if image_str:
            return [event.plain_result(f"{text}\n图片：{image_str}")]
        return [event.plain_result(text)]

    def _format_slots(self, slots: List[int]) -> str:
        if not slots:
            return "无"
        slots = sorted(slots)
        return ", ".join(str(v) for v in slots)

    def _record_revealed_image(self, src: Optional[Path], group_id: str, user_id: str) -> Optional[Path]:
        if not src or not src.exists():
            return src
        self.revealed_dir.mkdir(parents=True, exist_ok=True)
        dst = self.revealed_dir / f"{group_id}_{user_id}_{int(time.time())}_{src.name}"
        shutil.copy2(src, dst)
        return dst

    def _load_all(self):
        self.sessions = self._load_json(self.session_path, default={})
        self.runtime_config = self._load_json(self.runtime_config_path, default={})
        self._runtime_config_mtime = self._safe_mtime(self.runtime_config_path)

    def _maybe_reload_runtime_data(self):
        self._sync_legacy_resource_dirs()
        runtime_mtime = self._safe_mtime(self.runtime_config_path)
        if runtime_mtime > self._runtime_config_mtime:
            self.runtime_config = self._load_json(self.runtime_config_path, default=self.runtime_config)
            self._runtime_config_mtime = runtime_mtime
            logger.info("[arknights_blindbox] 已自动重载 runtime_config.json")

    def _sync_runtime_config_from_context(self):
        now = time.time()
        if now - self._last_context_sync < 3:
            return
        self._last_context_sync = now

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

    def _refresh_categories_and_states(self, force_sync_legacy: bool = False):
        if force_sync_legacy:
            self._sync_legacy_resource_dirs()
        scanned = self._scan_categories()
        self.categories = scanned
        for category_id, category in scanned.items():
            self._db_ensure_category_state(category_id, category)

    def _scan_categories(self) -> Dict[str, dict]:
        result: Dict[str, dict] = {}
        for box_type, root in (("number", self.number_box_dir), ("special", self.special_box_dir)):
            if not root.exists():
                continue
            for cat_dir in root.iterdir():
                if not cat_dir.is_dir():
                    continue
                category_id = cat_dir.name
                guide = self._find_guide_image(cat_dir)
                items, slots = self._parse_prize_items(cat_dir)
                if not items or not slots:
                    continue
                result[category_id] = {
                    "id": category_id,
                    "name": category_id,
                    "box_type": box_type,
                    "guide_image": guide,
                    "items": items,
                    "slot_total": len(slots),
                    "slots": sorted(slots),
                    "signature": self._build_category_signature(list(items.keys()), slots),
                }
        return result

    def _find_guide_image(self, cat_dir: Path) -> Optional[Path]:
        for n in self.GUIDE_CANDIDATES:
            p = cat_dir / n
            if p.exists():
                return p
        return None

    def _parse_prize_items(self, cat_dir: Path) -> Tuple[Dict[str, dict], List[int]]:
        slots: List[int] = []
        items: Dict[str, dict] = {}
        pattern = re.compile(r"^(\d+)[-_](.+)$")
        for f in sorted(cat_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name in self.GUIDE_CANDIDATES:
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

    def _build_category_signature(self, item_ids: List[str], slots: List[int]) -> str:
        return "|".join(sorted(item_ids)) + "::" + ",".join(map(str, sorted(slots)))

    def _ensure_default_runtime_config(self):
        if self.runtime_config_path.exists():
            return
        self._save_json(self.runtime_config_path, {
            "initial_balance": 200,
            "number_box_price": 25,
            "special_box_default_price": 40,
            "admin_ids": [],
            "special_box_prices": {},
        })

    def _ensure_resource_dirs(self):
        self.number_box_dir.mkdir(parents=True, exist_ok=True)
        self.special_box_dir.mkdir(parents=True, exist_ok=True)
        self.revealed_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_persistent_data_dir(self) -> Path:
        for getter_name in ("get_data_dir", "get_plugin_data_dir", "get_storage_dir"):
            getter = getattr(self.context, getter_name, None)
            if callable(getter):
                try:
                    value = getter()
                    if value:
                        return Path(value) / "astrbot_plugin_arknights_authorization"
                except Exception:
                    pass
        astrbot_data = Path("/opt/AstrBot/data")
        if astrbot_data.exists():
            return astrbot_data / "plugin_data" / "astrbot_plugin_arknights_authorization"
        return Path.home() / ".astrbot" / "plugin_data" / "astrbot_plugin_arknights_authorization"

    def _migrate_legacy_data_if_needed(self):
        if not self.legacy_data_dir.exists() or self.legacy_data_dir.resolve() == self.data_dir.resolve():
            return
        for file_name in ["sessions.json", "runtime_config.json", "blindbox.db"]:
            src = self.legacy_data_dir / file_name
            dst = self.data_dir / file_name
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        self._sync_legacy_resource_dirs()

    def _sync_legacy_resource_dirs(self):
        for root_name in ["resources", "资源"]:
            legacy_root = self.legacy_data_dir / root_name
            if not legacy_root.exists():
                continue
            for sub in ["数字盒", "特殊盒", "开出盲盒"]:
                src = legacy_root / sub
                dst = self.resource_dir / sub
                if src.exists():
                    shutil.copytree(src, dst, dirs_exist_ok=True)

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
            conn.commit()
        finally:
            conn.close()

    def _db_get_user(self, group_id: str, user_id: str):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT group_id,user_id,balance,registered_at FROM user_wallet WHERE group_id=? AND user_id=?",
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
                "INSERT OR REPLACE INTO user_wallet(group_id,user_id,balance,registered_at) VALUES (?,?,?,?)",
                (group_id, user_id, int(balance), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()

    def _db_update_balance(self, group_id: str, user_id: str, balance: int):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE user_wallet SET balance=? WHERE group_id=? AND user_id=?", (int(balance), group_id, user_id))
            conn.commit()
        finally:
            conn.close()

    def _db_ensure_category_state(self, category_id: str, category: dict):
        conn = sqlite3.connect(self.db_path)
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

    def _db_get_category_state(self, category_id: str) -> Tuple[List[str], List[int]]:
        conn = sqlite3.connect(self.db_path)
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

    def _db_set_category_state(self, category_id: str, items: List[str], slots: List[int]):
        conn = sqlite3.connect(self.db_path)
        try:
            signature = self.categories.get(category_id, {}).get("signature", "")
            conn.execute(
                "UPDATE category_state SET signature=?, remaining_items=?, remaining_slots=?, updated_at=? WHERE category_id=?",
                (signature, json.dumps(items, ensure_ascii=False), json.dumps(slots, ensure_ascii=False), int(time.time()), category_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _db_reset_category_state(self, category_id: str, category: dict):
        self._db_set_category_state(category_id, list(category["items"].keys()), list(category["slots"]))

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

    def _safe_mtime(self, path: Path) -> float:
        return path.stat().st_mtime if path.exists() else 0

    async def terminate(self):
        self._save_json(self.session_path, self.sessions)
        self._save_json(self.runtime_config_path, self.runtime_config)
        logger.info("[arknights_blindbox] 插件已卸载，状态已保存。")
