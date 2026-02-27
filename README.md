# astrbot_plugin_arknights_authorization

明日方舟通行证盲盒互动插件（AstrBot）。

## 本次修复

1. GitHub 仓库中的盲盒资源目录改为长期保留、由你自主维护。
2. 资源目录只保留两类：`number_box` 与 `special_box`。
3. 去除 `revealed_box`（手动盲盒结果目录）相关说明与依赖。

## 资源目录（主分支长期保留）

请在仓库中保留并按需维护以下目录：

```text
resources/
├─ number_box/
└─ special_box/
```

> 建议目录下保留 `.gitkeep`，保证空目录也会被 Git 跟踪。

## 放图规则

以 `number_box` 为例：

```text
resources/number_box/
└─ num_vc17/
   ├─ selection.jpg
   ├─ 1-山.png
   ├─ 2-W.jpg
   └─ 3-缪尔赛思.webp
```

- `num_vc17`：种类 ID（用于 `/方舟盲盒 选择 num_vc17`）
- 奖品文件名：`<序号>-<名称>.<扩展名>` 或 `<序号>_<名称>.<扩展名>`
- 支持扩展名：`jpg / jpeg / png / webp`
- `selection.jpg/png` 或 `cover.jpg/png` 可作为选择引导图

## 刷新资源

手动放图后可发送：

```text
/方舟盲盒 重载资源
```

然后发送 `/方舟盲盒 列表` 检查是否加载成功。

## 指令

- `/方舟盲盒 帮助`
- `/方舟盲盒 注册`
- `/方舟盲盒 钱包`
- `/方舟盲盒 库存`
- `/方舟盲盒 列表`
- `/方舟盲盒 选择 <种类ID>`
- `/方舟盲盒 开 <序号>`
- `/方舟盲盒 状态 [种类ID]`
- `/方舟盲盒 刷新 [种类ID]`
- `/方舟盲盒 重载资源`
- `/方舟盲盒 管理员 列表|添加|移除|特殊定价|余额`


## WebUI 配置项

插件配置页可直接设置以下参数：

- `initial_balance`：新用户初始金额（默认 200）
- `number_box_price`：数字盒单抽价格（默认 25）
- `special_box_default_price`：特殊盒默认单抽价格（默认 40）
- `admin_ids`：管理员账号 ID 列表
- `special_box_prices`：特殊盒单独定价对象（如 `{"sp_xxx": 66}`）
- `daily_gift_amount`：每日赠送金额（在 `daily_gift_hour_utc8` 指定时刻自动发放，默认 100）
- `daily_gift_hour_utc8`：每日赠送发放小时（UTC+8，0-23，默认 6）
- `admin_balance_set_enabled`：是否允许管理员使用余额设置指令（默认 true）
- `open_cooldown_seconds`：开盲盒冷却秒数（默认 10，可在 WebUI 修改）
- `blacklist_user_ids`：黑名单用户 ID 列表（命中后无法使用任何 `/方舟盲盒` 指令，支持列表或逗号分隔字符串）

> 插件已改为使用仓库根目录 `_conf_schema.json` 注册 WebUI 配置项（符合 AstrBot 插件配置文档）。


## 代码结构

- `main.py`：插件入口、指令分发、业务流程编排
- `db_service.py`：SQLite 读写与状态持久化
- `resource_service.py`：资源扫描、奖品解析、签名构建
- `time_service.py`：时间工具（UTC+8 日期/小时）


- 冷却机制：同一用户在同一群组开完一发后需等待冷却时间后才能继续开启（默认 10 秒，可在 WebUI 配置）。


- 库存系统：用户抽到的奖品会自动进入库存并持久化保存（按群隔离），使用 `/方舟盲盒 库存` 查看。
