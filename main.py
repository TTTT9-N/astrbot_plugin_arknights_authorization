import asyncio
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .db_service import (
        db_add_market_listing,
        db_consume_market_listing,
        db_delete_expired_system_listings,
        db_ensure_category_state,
        db_get_balance,
        db_get_category_state,
        db_get_kv,
        db_list_market_listings,
        db_get_user,
        db_grant_daily_gift,
        db_register_user,
        db_set_category_state,
        db_set_kv,
        db_update_balance,
        init_db,
    )
    from . import resource_service as resource_service_module
    from .time_service import utc8_date_hour
    from .inventory_service import (
        add_inventory_item,
        consume_inventory_item,
        get_user_inventory,
        get_user_inventory_by_category,
        init_inventory_table,
    )
    from .market_service import (
        build_market_breakdown,
        calc_scarcity_multiplier,
        clamp_non_negative_float,
        clamp_volatility,
        get_daily_market_multiplier_for_item,
    )
    from .resource_index_service import sync_box_index_file
except Exception:
    plugin_dir = str(Path(__file__).resolve().parent)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    from db_service import (
        db_add_market_listing,
        db_consume_market_listing,
        db_delete_expired_system_listings,
        db_ensure_category_state,
        db_get_balance,
        db_get_category_state,
        db_get_kv,
        db_list_market_listings,
        db_get_user,
        db_grant_daily_gift,
        db_register_user,
        db_set_category_state,
        db_set_kv,
        db_update_balance,
        init_db,
    )
    try:
        import resource_service as resource_service_module
    except Exception:
        resource_service_module = None
    from time_service import utc8_date_hour
    from inventory_service import (
        add_inventory_item,
        consume_inventory_item,
        get_user_inventory,
        get_user_inventory_by_category,
        init_inventory_table,
    )
    from market_service import (
        build_market_breakdown,
        calc_scarcity_multiplier,
        clamp_non_negative_float,
        clamp_volatility,
        get_daily_market_multiplier_for_item,
    )
    from resource_index_service import sync_box_index_file


@register("astrbot_plugin_arknights_authorization", "codex", "明日方舟通行证盲盒互动插件", "1.7.2")
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
        self.resource_index_path = self.data_dir / "resource_box_index.json"

        self.resource_dir = self.base_dir / "resources"
        self.number_box_dir = self.resource_dir / "number_box"
        self.special_box_dir = self.resource_dir / "special_box"

        self.sessions: Dict[str, str] = {}
        self.runtime_config: Dict[str, object] = {}
        self.categories: Dict[str, dict] = {}
        self.resource_box_index: Dict[str, dict] = {}

        self._runtime_config_mtime: float = 0
        self._last_context_sync: float = 0
        self._daily_gift_task: Optional[asyncio.Task] = None
        self._last_open_ts: Dict[str, float] = {}

    async def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_data_if_needed()
        self._ensure_default_runtime_config()
        self._load_all()
        self._sync_runtime_config_from_context()
        self._init_db()
        self._grant_daily_gift_if_due()
        self._refresh_categories_and_states()
        self._daily_gift_task = asyncio.create_task(self._daily_gift_loop())
        logger.info("[arknights_blindbox] 插件初始化完成。")

    @filter.command("方舟盲盒")
    async def arknights_blindbox(self, event: AstrMessageEvent):
        self._maybe_reload_runtime_data()
        self._sync_runtime_config_from_context()
        self._grant_daily_gift_if_due()
        self._refresh_categories_and_states()

        args = self._extract_command_args(event.message_str)
        if not args:
            yield event.plain_result(self._build_help_text())
            return

        action = args[0].lower()

        if self._is_blacklisted(event):
            return

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

        if action in {"库存", "bag", "inventory"}:
            identity = self._get_identity(event)
            if identity is None:
                yield event.plain_result("无法识别你的账号ID，暂时无法查询库存。")
                return
            group_id, user_id = identity
            if self._db_get_user(group_id, user_id) is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return
            rows = self._db_get_user_inventory(group_id, user_id)
            if not rows:
                yield event.plain_result(f"当前库存为空。\n当前群：{group_id}")
                return
            lines = ["当前库存："]
            for category_id, item_name, count in rows:
                unit_price, unit_text = self._get_inventory_unit_price(group_id, category_id, item_name)
                total_text = f"{unit_price * count} 元" if unit_price is not None else "待定"
                lines.append(
                    f"- [{category_id}] {item_name} x{count} | 通行证单价：{unit_text} | 数量总价：{total_text}"
                )
            lines.append(f"\n当前群：{group_id}")
            yield event.plain_result("\n".join(lines))
            return

        if action in {"市场", "market", "行情"}:
            identity = self._get_identity(event)
            if identity is None:
                yield event.plain_result("无法识别你的账号ID，暂时无法查看市场。")
                return
            group_id, user_id = identity
            if self._db_get_user(group_id, user_id) is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return
            for r in self._handle_market_command(event, group_id, user_id, args[1:]):
                yield r
            return

        if action in {"列表", "list", "types"}:
            yield event.plain_result(self._build_category_list_text())
            return

        if action in {"帮助", "help"}:
            yield event.plain_result(self._build_help_text())
            return

        if action in {"重载资源", "reload", "reload_resources", "rescan"}:
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
                    f"你已选择【{category['id']}】\n"
                    f"当前卡池剩余：{len(remain_items)}\n"
                    f"当前单抽价格：{self._format_price_text(price)}\n"
                    "该种类已不可继续开启。你可以：\n"
                    f"1) /方舟盲盒 刷新 {category_id}\n"
                    "2) /方舟盲盒 列表（换种类）"
                )
                return

            tip = (
                f"你已选择【{category['id']}】\n"
                f"当前卡池剩余：{len(remain_items)}\n"
                f"当前单抽价格：{self._format_price_text(price)}\n"
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
                yield event.plain_result(f"【{category['id']}】卡池或序号已耗尽，请发送：/方舟盲盒 刷新 {category_id}")
                return
            if choose_slot not in remain_slots:
                yield event.plain_result(f"序号 {choose_slot} 已不可用，可选序号：{self._format_slots(remain_slots)}")
                return

            balance = self._db_get_balance(group_id, user_id)
            if balance is None:
                yield event.plain_result("你还未注册，请先发送：/方舟盲盒 注册")
                return
            price = self._get_category_price(category_id)
            if price <= 0:
                yield event.plain_result("当前种类的通行证价格待定，请联系管理员设置特殊定价后再开启。")
                return
            if balance < price:
                yield event.plain_result(f"余额不足，当前余额：{balance} 元，当前单抽价格：{price} 元")
                return

            cooldown_key = self._build_session_key(event)
            now_ts = time.time()
            cooldown_seconds = self._get_open_cooldown_seconds()
            last_ts = self._get_last_open_ts(cooldown_key)
            remain_cd = cooldown_seconds - (now_ts - last_ts)
            if remain_cd > 0:
                wait_sec = int(remain_cd) + (0 if remain_cd.is_integer() else 1)
                yield event.plain_result(f"操作过快，请等待 {wait_sec} 秒后再开盲盒。")
                return

            selected = random.choice(remain_items)
            remain_items.remove(selected)
            remain_slots.remove(choose_slot)
            self._db_set_category_state(category_id, remain_items, remain_slots)
            self._db_update_balance(group_id, user_id, balance - price)
            self._set_last_open_ts(cooldown_key, now_ts)

            item = category["items"][selected]
            self._db_add_inventory_item(group_id, user_id, category_id, item["name"], 1)
            prize_image = item.get("image")
            msg = (
                f"你选择了第 {choose_slot} 号盲盒，开启结果：\n"
                f"所属种类：{category['id']}\n"
                f"奖品名称：{item['name']}\n"
                f"当前卡池剩余：{len(remain_items)}\n"
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
                f"【{self.categories[category_id]['id']}】已刷新。\n"
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
                f"【{category['id']}】\n"
                f"卡池状态：{len(remain_items)}/{len(category['items'])}\n"
                f"序号状态：{len(remain_slots)}/{category['slot_total']}\n"
                f"单抽价格：{self._format_price_text(self._get_category_price(category_id))}\n"
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
            return [event.plain_result("管理员指令：\n- 管理员 列表|添加|移除 <user_id>\n- 特殊定价 <种类ID> <金额>\n- 余额 <user_id> <金额> [group_id]\n- 黑名单 列表|添加|移除 <user_id>")]

        identity = self._get_identity(event)
        if identity is None:
            return [event.plain_result("无法识别你的账号ID，无法执行管理员操作。")]
        _, current_user_id = identity
        admins = self._get_admin_ids()
        action = args[0]
        action_alias = {"list": "列表", "add": "添加", "remove": "移除", "setprice": "特殊定价", "setbalance": "余额", "blacklist": "黑名单"}
        action = action_alias.get(action, action)

        if action == "列表":
            return [event.plain_result(f"管理员列表：{', '.join(admins) if admins else '暂无'}")]

        if action == "添加":
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

        if action == "移除":
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

        if action == "特殊定价":
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

        if action == "余额":
            if len(args) < 3:
                return [event.plain_result("用法：/方舟盲盒 管理员 余额 <user_id> <金额> [group_id]")]
            if current_user_id not in admins:
                return [event.plain_result("仅管理员可设置用户余额。")]
            if not bool(self.runtime_config.get("admin_balance_set_enabled", True)):
                return [event.plain_result("WebUI 已关闭管理员余额设置功能。")]
            target_user_id, amount = args[1], args[2]
            target_group_id = args[3] if len(args) > 3 else identity[0]
            if not amount.isdigit() or int(amount) < 0:
                return [event.plain_result("金额必须是非负整数。")]
            if self._db_get_user(target_group_id, target_user_id) is None:
                return [event.plain_result(f"用户 {target_user_id} 在群 {target_group_id} 未注册。")]
            self._db_update_balance(target_group_id, target_user_id, int(amount))
            return [event.plain_result(f"已设置余额：群 {target_group_id} 用户 {target_user_id} = {amount} 元")]

        if action == "黑名单":
            if current_user_id not in admins:
                return [event.plain_result("仅管理员可管理黑名单。")]
            if len(args) < 2:
                return [event.plain_result("用法：/方舟盲盒 管理员 黑名单 列表|添加 <user_id>|移除 <user_id>")]
            sub_action = {"list": "列表", "add": "添加", "remove": "移除"}.get(args[1], args[1])
            blacklist = self._get_blacklist_user_ids()
            if sub_action == "列表":
                return [event.plain_result(f"黑名单列表：{', '.join(blacklist) if blacklist else '暂无'}")]
            if len(args) < 3:
                return [event.plain_result("用法：/方舟盲盒 管理员 黑名单 添加 <user_id> 或 /方舟盲盒 管理员 黑名单 移除 <user_id>")]
            target = self._parse_user_id_input(args[2])
            if not target:
                return [event.plain_result("无法识别用户ID，请直接填写数字ID。")]
            if sub_action == "添加":
                if target not in blacklist:
                    blacklist.append(target)
                    self.runtime_config["blacklist_user_ids"] = blacklist
                    self._save_json(self.runtime_config_path, self.runtime_config)
                return [event.plain_result(f"已加入黑名单：{target}")]
            if sub_action == "移除":
                if target in blacklist:
                    blacklist.remove(target)
                    self.runtime_config["blacklist_user_ids"] = blacklist
                    self._save_json(self.runtime_config_path, self.runtime_config)
                return [event.plain_result(f"已移出黑名单：{target}")]
            return [event.plain_result("未知黑名单指令。")]

        return [event.plain_result("未知管理员指令。")]


    def _extract_command_args(self, raw_message: str) -> List[str]:
        text = (raw_message or "").strip()
        if not text:
            return []
        compact_prefix_map = {
            "/方舟盲盒市场": "市场",
            "/方舟盲盒重载资源": "重载资源",
            "/方舟盲盒帮助": "帮助",
            "/方舟盲盒列表": "列表",
            "/方舟盲盒注册": "注册",
            "/方舟盲盒钱包": "钱包",
            "/方舟盲盒库存": "库存",
            "/方舟盲盒状态": "状态",
        }
        for prefix, action in compact_prefix_map.items():
            if text.startswith(prefix):
                remain = text.replace(prefix, "", 1).strip()
                return [action] + ([p for p in remain.split() if p] if remain else [])
        parts = [p for p in text.split() if p]
        first = parts[0].lstrip("/") if parts else ""
        return parts[1:] if first == "方舟盲盒" else parts

    def _build_help_text(self) -> str:
        return (
            "明日方舟通行证盲盒指令：\n"
            "1) /方舟盲盒 注册\n"
            "2) /方舟盲盒 钱包\n"
            "3) /方舟盲盒 库存\n"
            "4) /方舟盲盒 列表\n"
            "5) /方舟盲盒 市场 [种类ID]\n"
            "6) /方舟盲盒 市场 上架 <种类ID> <奖品名> <价格> [数量]\n"
            "7) /方舟盲盒 市场 购买 <种类ID> <奖品名> [数量]\n"
            "8) /方舟盲盒 选择 <种类ID>\n"
            "9) /方舟盲盒 开 <序号>\n"
            "10) /方舟盲盒 状态 [种类ID]\n"
            "11) /方舟盲盒 刷新 [种类ID]\n"
            "12) /方舟盲盒 重载资源\n"
            "13) /方舟盲盒 管理员 <列表|添加|移除|特殊定价|余额|黑名单> ..."
        )

    def _build_category_list_text(self) -> str:
        if not self.categories:
            return "当前未发现盲盒资源。请先在 resources/number_box 或 resources/special_box 下放入资源。"
        lines = ["可用盲盒种类："]
        for category_id, category in self.categories.items():
            remain_items, remain_slots = self._db_get_category_state(category_id)
            lines.append(
                f"- {category_id}（类型: {category['box_type']}，价格: {self._format_price_text(self._get_category_price(category_id))}，"
                f"卡池: {len(remain_items)}/{len(category['items'])}，序号: {len(remain_slots)}/{category['slot_total']}）"
            )
        lines.append("\n使用：/方舟盲盒 选择 <种类ID>")
        return "\n".join(lines)

    def _handle_market_command(self, event: AstrMessageEvent, group_id: str, user_id: str, args: List[str]):
        self._refresh_system_market(group_id)

        if not args:
            return [event.plain_result(self._build_market_text("", group_id))]

        action = str(args[0]).strip().lower()
        alias = {"list": "列表", "sell": "上架", "buy": "购买"}
        action = alias.get(action, action)

        if action == "上架":
            if len(args) < 4:
                return [event.plain_result("用法：/方舟盲盒 市场 上架 <种类ID> <奖品名> <价格> [数量]")]
            category_id = args[1]
            item_name = args[2]
            if not str(args[3]).isdigit():
                return [event.plain_result("价格必须是正整数。")]
            price = int(args[3])
            quantity = int(args[4]) if len(args) > 4 and str(args[4]).isdigit() else 1
            if price <= 0 or quantity <= 0:
                return [event.plain_result("价格和数量必须大于 0。")]

            item_id = self._find_item_id_by_name(category_id, item_name)
            if not item_id:
                return [event.plain_result(f"种类 {category_id} 中不存在奖品 `{item_name}`。")]

            if not self._db_consume_inventory_item(group_id, user_id, category_id, item_name, quantity):
                return [event.plain_result(f"上架失败：库存不足（{item_name}）。")]

            self._db_add_market_listing(
                group_id,
                category_id,
                item_id,
                item_name,
                price,
                quantity,
                user_id,
                is_system=0,
                day_key="",
            )
            return [event.plain_result(f"上架成功：[{category_id}] {item_name} x{quantity}，售价 {price} 元/个")]

        if action == "购买":
            if len(args) < 3:
                return [event.plain_result("用法：/方舟盲盒 市场 购买 <种类ID> <奖品名> [数量]")]
            category_id = args[1]
            item_name = args[2]
            quantity = int(args[3]) if len(args) > 3 and str(args[3]).isdigit() else 1
            if quantity <= 0:
                return [event.plain_result("购买数量必须大于 0。")]

            listing = self._pick_listing_for_buy(group_id, category_id, item_name)
            if not listing:
                return [event.plain_result(f"当前市场没有可购买的 [{category_id}] {item_name}。")]
            if listing["quantity"] < quantity:
                return [event.plain_result(f"库存不足，当前可购买：{listing['quantity']}。")]

            total_price = int(listing["price"]) * quantity
            balance = self._db_get_balance(group_id, user_id) or 0
            if balance < total_price:
                return [event.plain_result(f"余额不足，需 {total_price} 元，当前余额 {balance} 元。")]

            if not self._db_consume_market_listing(int(listing["id"]), quantity):
                return [event.plain_result("购买失败：商品已被抢完，请重试。")]

            self._db_update_balance(group_id, user_id, balance - total_price)
            self._db_add_inventory_item(group_id, user_id, category_id, item_name, quantity)
            return [event.plain_result(f"购买成功：{item_name} x{quantity}，花费 {total_price} 元，当前余额 {balance-total_price} 元")]

        category_id = args[0]
        return [event.plain_result(self._build_market_text(category_id, group_id))]

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

    def _normalize_id_list(self, value) -> List[str]:
        if value is None:
            return []
        raw_list = []
        if isinstance(value, (list, tuple, set)):
            raw_list = list(value)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    raw_list = parsed
                else:
                    raw_list = [text]
            except Exception:
                raw_list = [v.strip() for v in text.replace("，", ",").split(",") if v.strip()]
        else:
            raw_list = [value]

        result: List[str] = []
        for v in raw_list:
            t = str(v).strip()
            if t and t.lower() not in {"none", "null", "[]"}:
                result.append(t)
        return result


    def _parse_user_id_input(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        if raw.isdigit():
            return raw
        for key in ("qq=", "id=", "user_id="):
            idx = raw.find(key)
            if idx >= 0:
                start = idx + len(key)
                digits = []
                while start < len(raw) and raw[start].isdigit():
                    digits.append(raw[start])
                    start += 1
                if digits:
                    return "".join(digits)
        digits = "".join(ch for ch in raw if ch.isdigit())
        return digits

    def _get_admin_ids(self) -> List[str]:
        return self._normalize_id_list(self.runtime_config.get("admin_ids", []))

    def _get_blacklist_user_ids(self) -> List[str]:
        return self._normalize_id_list(self.runtime_config.get("blacklist_user_ids", []))

    def _is_blacklisted(self, event: AstrMessageEvent) -> bool:
        identity = self._get_identity(event)
        return bool(identity and identity[1] in self._get_blacklist_user_ids())


    def _format_price_text(self, price: int) -> str:
        return f"{price} 元" if price > 0 else "待定"

    def _get_inventory_unit_price(self, group_id: str, category_id: str, item_name: str) -> Tuple[Optional[int], str]:
        item_id = self._find_item_id_by_name(category_id, item_name)
        price, _ = self._get_market_price_breakdown(group_id, category_id, item_id)
        return (price, self._format_price_text(price)) if price > 0 else (None, "待定")

    def _get_category_price(self, category_id: str) -> int:
        category = self.categories.get(category_id, {})
        if category.get("box_type") == "number":
            return int(self.runtime_config.get("number_box_price", 25))
        if not category:
            if category_id.startswith("num"):
                return int(self.runtime_config.get("number_box_price", 25))
            if category_id.startswith("sp") or category_id.startswith("special"):
                special_prices = self.runtime_config.get("special_box_prices", {})
                if isinstance(special_prices, dict) and category_id in special_prices:
                    return int(special_prices[category_id])
                return int(self.runtime_config.get("special_box_default_price", 0))
        special_prices = self.runtime_config.get("special_box_prices", {})
        if isinstance(special_prices, dict) and category_id in special_prices:
            return int(special_prices[category_id])
        return int(self.runtime_config.get("special_box_default_price", 0))

    def _find_item_id_by_name(self, category_id: str, item_name: str) -> str:
        target = str(item_name or "").strip()
        idx = self.resource_box_index.get(category_id, {})
        for box in idx.get("boxes", []) if isinstance(idx, dict) else []:
            if str(box.get("name", "")).strip() == target:
                return str(box.get("item_id", ""))

        category = self.categories.get(category_id, {})
        items = category.get("items", {}) if isinstance(category, dict) else {}
        for item_id, item in items.items():
            if str(item.get("name", "")).strip() == target:
                return item_id
        return ""


    def _get_market_price_breakdown(self, group_id: str, category_id: str, item_id: str = "") -> Tuple[int, str]:
        base_price = self._get_category_price(category_id)
        if base_price <= 0:
            return 0, "基准价待定"

        category = self.categories.get(category_id)
        if not category:
            return base_price, f"基准价 {base_price}"

        remain_items, _ = self._db_get_category_state(category_id)
        total_items = len(category.get("items", {}))
        volatility = clamp_volatility(self.runtime_config.get("market_volatility", 0.2))
        scarcity_weight = clamp_non_negative_float(self.runtime_config.get("market_scarcity_weight", 0.8))

        current_date, _ = self._utc8_date_hour()
        item_key = item_id or "_category_default_"
        market_multiplier = get_daily_market_multiplier_for_item(
            date_str=current_date,
            category_id=category_id,
            item_id=item_key,
            volatility=volatility,
            kv_getter=self._db_get_kv,
            kv_setter=self._db_set_kv,
        )
        scarcity_multiplier = calc_scarcity_multiplier(len(remain_items), total_items, scarcity_weight)
        price, detail = build_market_breakdown(
            base_price=base_price,
            market_multiplier=market_multiplier,
            scarcity_multiplier=scarcity_multiplier,
        )
        user_prices = [
            int(x["price"]) for x in self._db_list_market_listings(group_id, category_id)
            if int(x.get("is_system", 0)) == 0 and (not item_id or str(x.get("item_id")) == item_id)
        ]
        if user_prices:
            avg_user = sum(user_prices) / len(user_prices)
            price = max(1, int(round((price * 0.7) + (avg_user * 0.3))))
            detail = f"{detail}；用户上架均价影响后={price}"
        return price, detail

    def _build_market_text(self, category_id: str = "", group_id: str = "") -> str:
        if not self.categories:
            return "当前未发现盲盒资源。请先在 resources/number_box 或 resources/special_box 下放入资源。"

        if category_id:
            category = self.categories.get(category_id)
            if not category:
                return f"不存在种类 `{category_id}`。\n\n{self._build_category_list_text()}"

            remain_items, _ = self._db_get_category_state(category_id)
            lines = [
                f"【市场】{category_id}",
                f"剩余盲盒数量：{len(remain_items)}/{len(category.get('items', {}))}",
                "单盒价格（按盲盒独立计算）：",
            ]
            for item_id, item in sorted(category.get("items", {}).items(), key=lambda x: (x[1].get("slot_no", 0), x[0])):
                price, detail = self._get_market_price_breakdown(group_id, category_id, item_id)
                sold_text = "（已开出）" if item_id not in remain_items else ""
                lines.append(
                    f"- #{item.get('slot_no', 0)} {item.get('name', item_id)}：{self._format_price_text(price)} {sold_text}"
                )
                lines.append(f"  · {detail}")
            listings = self._db_list_market_listings(group_id, category_id)
            if listings:
                lines.append("\n当前在售：")
                for row in listings:
                    seller = "系统" if int(row.get("is_system", 0)) == 1 else f"用户{row.get('seller_user_id')}"
                    lines.append(f"- {row['item_name']} x{row['quantity']} | {row['price']} 元 | 来源：{seller}")
            return "\n".join(lines)

        lines = ["【市场总览】"]
        for cid, category in self.categories.items():
            remain_items, _ = self._db_get_category_state(cid)
            price_list = [self._get_market_price_breakdown(group_id, cid, item_id)[0] for item_id in category.get("items", {}).keys()]
            valid = [p for p in price_list if p > 0]
            if valid:
                price_text = f"{min(valid)}~{max(valid)} 元"
            else:
                price_text = "待定"
            lines.append(
                f"- {cid}（类型: {category['box_type']}，单盒价格区间: {price_text}，"
                f"剩余: {len(remain_items)}/{len(category.get('items', {}))}）"
            )
        system_cnt = len([x for x in self._db_list_market_listings(group_id) if int(x.get("is_system", 0)) == 1])
        lines.append(f"\n系统在售数量：{system_cnt}（每天 0 点刷新，最多 3 种）")
        lines.append("\n查看详情：/方舟盲盒 市场 <种类ID>")
        lines.append("上架：/方舟盲盒 市场 上架 <种类ID> <奖品名> <价格> [数量]")
        lines.append("购买：/方舟盲盒 市场 购买 <种类ID> <奖品名> [数量]")
        return "\n".join(lines)

    def _pick_listing_for_buy(self, group_id: str, category_id: str, item_name: str) -> Optional[dict]:
        rows = self._db_list_market_listings(group_id, category_id)
        target = str(item_name).strip()
        for row in rows:
            if str(row.get("item_name", "")).strip() == target and int(row.get("quantity", 0)) > 0:
                return row
        return None

    def _refresh_system_market(self, group_id: str):
        date_key, _ = self._utc8_date_hour()
        self._db_delete_expired_system_listings(group_id, date_key)

        existing = [x for x in self._db_list_market_listings(group_id) if int(x.get("is_system", 0)) == 1 and x.get("day_key") == date_key]
        if existing:
            return

        all_items = []
        for category_id, category in self.categories.items():
            remain_items, _ = self._db_get_category_state(category_id)
            for item_id in remain_items:
                item = category.get("items", {}).get(item_id)
                if item:
                    all_items.append((category_id, item_id, str(item.get("name", item_id))))

        random.shuffle(all_items)
        for category_id, item_id, item_name in all_items[:3]:
            price, _ = self._get_market_price_breakdown(group_id, category_id, item_id)
            if price <= 0:
                continue
            self._db_add_market_listing(
                group_id,
                category_id,
                item_id,
                item_name,
                price,
                1,
                "system",
                is_system=1,
                day_key=date_key,
            )

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
        for key in ["initial_balance", "number_box_price", "special_box_default_price", "admin_ids", "special_box_prices", "daily_gift_amount", "daily_gift_hour_utc8", "admin_balance_set_enabled", "open_cooldown_seconds", "blacklist_user_ids", "market_volatility", "market_scarcity_weight"]:
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
        self.resource_box_index = sync_box_index_file(self.resource_index_path, self.categories)

    def _scan_categories(self) -> Dict[str, dict]:
        scanner = getattr(resource_service_module, "scan_categories", None)
        if callable(scanner):
            return scanner(self.number_box_dir, self.special_box_dir, self.GUIDE_CANDIDATES)
        return self._scan_categories_fallback()

    def _scan_categories_fallback(self) -> Dict[str, dict]:
        result: Dict[str, dict] = {}
        for box_type, root in (("number", self.number_box_dir), ("special", self.special_box_dir)):
            if not root.exists():
                continue
            for cat_dir in root.iterdir():
                if not cat_dir.is_dir():
                    continue
                category_id = cat_dir.name
                items, slots = self._parse_prize_items_fallback(cat_dir)
                if not items or not slots:
                    continue
                guide = self._find_guide_image_fallback(cat_dir)
                result[category_id] = {
                    "id": category_id,
                    "box_type": box_type,
                    "guide_image": guide,
                    "items": items,
                    "slot_total": len(slots),
                    "slots": sorted(slots),
                    "signature": self._build_category_signature_fallback(list(items.keys()), slots),
                }
        return result

    def _find_guide_image_fallback(self, cat_dir: Path) -> Optional[Path]:
        for name in self.GUIDE_CANDIDATES:
            p = cat_dir / name
            if p.exists():
                return p
        return None

    def _parse_prize_items_fallback(self, cat_dir: Path) -> Tuple[Dict[str, dict], List[int]]:
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

    def _build_category_signature_fallback(self, item_ids: List[str], slots: List[int]) -> str:
        return "|".join(sorted(item_ids)) + "::" + ",".join(map(str, sorted(slots)))

    def _ensure_default_runtime_config(self):
        if self.runtime_config_path.exists():
            return
        self._save_json(self.runtime_config_path, {
            "initial_balance": 200,
            "number_box_price": 25,
            "special_box_default_price": 0,
            "admin_ids": [],
            "special_box_prices": {},
            "daily_gift_amount": 100,
            "daily_gift_hour_utc8": 6,
            "admin_balance_set_enabled": True,
            "open_cooldown_seconds": 10,
            "blacklist_user_ids": [],
            "market_volatility": 0.2,
            "market_scarcity_weight": 0.8,
        })


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
        if self.legacy_data_dir.exists() and self.legacy_data_dir.resolve() != self.data_dir.resolve():
            for file_name in ["sessions.json", "runtime_config.json", "blindbox.db"]:
                src = self.legacy_data_dir / file_name
                dst = self.data_dir / file_name
                if src.exists() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        self._sync_legacy_resource_dirs()

    def _sync_legacy_resource_dirs(self):
        sub_dir_map = {
            "number_box": "number_box",
            "special_box": "special_box",
            "数字盒": "number_box",
            "特殊盒": "special_box",
        }
        source_roots = [
            self.legacy_data_dir / "resources",
            self.legacy_data_dir / "资源",
            self.data_dir / "resources",
            self.data_dir / "资源",
        ]
        for root in source_roots:
            if not root.exists():
                continue
            for src_sub, dst_sub in sub_dir_map.items():
                src = root / src_sub
                dst = self.resource_dir / dst_sub
                if src.exists():
                    shutil.copytree(src, dst, dirs_exist_ok=True)

    def _init_db(self):
        init_db(self.db_path)
        init_inventory_table(self.db_path)

    def _db_get_user(self, group_id: str, user_id: str):
        return db_get_user(self.db_path, group_id, user_id)

    def _db_get_balance(self, group_id: str, user_id: str) -> Optional[int]:
        return db_get_balance(self.db_path, group_id, user_id)

    def _db_register_user(self, group_id: str, user_id: str, balance: int):
        db_register_user(self.db_path, group_id, user_id, balance)

    def _db_update_balance(self, group_id: str, user_id: str, balance: int):
        db_update_balance(self.db_path, group_id, user_id, balance)

    def _db_add_inventory_item(self, group_id: str, user_id: str, category_id: str, item_name: str, count: int = 1):
        add_inventory_item(self.db_path, group_id, user_id, category_id, item_name, count)

    def _db_get_user_inventory(self, group_id: str, user_id: str):
        return get_user_inventory(self.db_path, group_id, user_id)

    def _db_get_user_inventory_by_category(self, group_id: str, user_id: str, category_id: str):
        return get_user_inventory_by_category(self.db_path, group_id, user_id, category_id)

    def _db_consume_inventory_item(self, group_id: str, user_id: str, category_id: str, item_name: str, count: int = 1) -> bool:
        return consume_inventory_item(self.db_path, group_id, user_id, category_id, item_name, count)

    def _db_ensure_category_state(self, category_id: str, category: dict):
        db_ensure_category_state(self.db_path, category_id, category)

    def _db_get_category_state(self, category_id: str) -> Tuple[List[str], List[int]]:
        return db_get_category_state(self.db_path, category_id)

    def _db_set_category_state(self, category_id: str, items: List[str], slots: List[int]):
        signature = self.categories.get(category_id, {}).get("signature", "")
        db_set_category_state(self.db_path, category_id, signature, items, slots)

    def _db_reset_category_state(self, category_id: str, category: dict):
        self._db_set_category_state(category_id, list(category["items"].keys()), list(category["slots"]))

    def _utc8_date_hour(self) -> Tuple[str, int]:
        return utc8_date_hour()

    def _db_get_kv(self, key: str) -> Optional[str]:
        return db_get_kv(self.db_path, key)

    def _db_set_kv(self, key: str, value: str):
        db_set_kv(self.db_path, key, value)

    def _db_grant_daily_gift(self, amount: int) -> int:
        return db_grant_daily_gift(self.db_path, amount)

    def _db_add_market_listing(
        self,
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
        db_add_market_listing(
            self.db_path,
            group_id,
            category_id,
            item_id,
            item_name,
            price,
            quantity,
            seller_user_id,
            is_system,
            day_key,
        )

    def _db_list_market_listings(self, group_id: str, category_id: str = "") -> List[dict]:
        return db_list_market_listings(self.db_path, group_id, category_id)

    def _db_consume_market_listing(self, listing_id: int, quantity: int) -> bool:
        return db_consume_market_listing(self.db_path, listing_id, quantity)

    def _db_delete_expired_system_listings(self, group_id: str, day_key: str):
        db_delete_expired_system_listings(self.db_path, group_id, day_key)

    def _get_open_cooldown_seconds(self) -> int:
        value = int(self.runtime_config.get("open_cooldown_seconds", 10))
        return max(0, value)

    def _get_last_open_ts(self, cooldown_key: str) -> float:
        if cooldown_key in self._last_open_ts:
            return self._last_open_ts[cooldown_key]
        db_value = self._db_get_kv(f"last_open_ts:{cooldown_key}")
        if not db_value:
            return 0.0
        try:
            last_ts = float(db_value)
        except Exception:
            return 0.0
        self._last_open_ts[cooldown_key] = last_ts
        return last_ts

    def _set_last_open_ts(self, cooldown_key: str, ts: float):
        self._last_open_ts[cooldown_key] = float(ts)
        self._db_set_kv(f"last_open_ts:{cooldown_key}", str(ts))

    def _grant_daily_gift_if_due(self) -> bool:
        amount = int(self.runtime_config.get("daily_gift_amount", 100))
        if amount <= 0:
            return False
        current_date, current_hour = self._utc8_date_hour()
        grant_hour = int(self.runtime_config.get("daily_gift_hour_utc8", 6))
        grant_hour = min(23, max(0, grant_hour))
        if current_hour < grant_hour:
            return False
        last_date = self._db_get_kv("last_daily_gift_date")
        if last_date == current_date:
            return False
        affected = self._db_grant_daily_gift(amount)
        self._db_set_kv("last_daily_gift_date", current_date)
        logger.info(f"[arknights_blindbox] 每日赠送已发放：日期={current_date} 金额={amount} 覆盖用户数={affected}")
        return True

    async def _daily_gift_loop(self):
        while True:
            try:
                self._grant_daily_gift_if_due()
            except Exception as ex:
                logger.warning(f"[arknights_blindbox] 每日赠送任务异常：{ex}")
            await asyncio.sleep(60)

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
        if self._daily_gift_task:
            self._daily_gift_task.cancel()
            self._daily_gift_task = None
        self._save_json(self.session_path, self.sessions)
        self._save_json(self.runtime_config_path, self.runtime_config)
        logger.info("[arknights_blindbox] 插件已卸载，状态已保存。")
