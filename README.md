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
- `/方舟盲盒 市场 [种类ID]`
- `/方舟盲盒 选择 <种类ID>`
- `/方舟盲盒 开 <序号>`
- `/方舟盲盒 状态 [种类ID]`
- `/方舟盲盒 刷新 [种类ID]`
- `/方舟盲盒 重载资源`
- `/方舟盲盒 管理员 <列表|添加|移除|特殊定价|余额|黑名单> ...`


## WebUI 配置项

插件配置页可直接设置以下参数：

- `initial_balance`：新用户初始金额（默认 200）
- `number_box_price`：数字盒基准单抽价格（默认 25）
- `special_box_default_price`：特殊盒默认单抽价格（默认 0，表示待定不可抽）
- `admin_ids`：管理员账号 ID 列表
- `special_box_prices`：特殊盒单独定价对象（如 `{"sp_xxx": 66}`）
- `daily_gift_amount`：每日赠送金额（在 `daily_gift_hour_utc8` 指定时刻自动发放，默认 100）
- `daily_gift_hour_utc8`：每日赠送发放小时（UTC+8，0-23，默认 6）
- `admin_balance_set_enabled`：是否允许管理员使用余额设置指令（默认 true）
- `open_cooldown_seconds`：开盲盒冷却秒数（默认 10，可在 WebUI 修改）
- `blacklist_user_ids`：黑名单用户 ID 列表（命中后无法使用任何 `/方舟盲盒` 指令，支持列表或逗号分隔字符串）
- `market_volatility`：市场波动率（建议 0.1-0.5）
- `market_scarcity_weight`：稀缺溢价系数（数量越少价格越高的强度）

> 插件已改为使用仓库根目录 `_conf_schema.json` 注册 WebUI 配置项（符合 AstrBot 插件配置文档）。


## 代码结构

- `main.py`：插件入口、指令分发、业务流程编排
- `db_service.py`：SQLite 读写与状态持久化
- `resource_service.py`：资源扫描、奖品解析、签名构建
- `time_service.py`：时间工具（UTC+8 日期/小时）
- `market_service.py`：市场价格模型（波动率 + 稀缺溢价）
- `resource_index_service.py`：资源盲盒索引生成（用于市场逐盒定价）


- 冷却机制：同一用户在同一群组开完一发后需等待冷却时间后才能继续开启（默认 10 秒，可在 WebUI 配置）。


- 库存系统：用户抽到的奖品会自动进入库存并持久化保存（按群隔离），使用 `/方舟盲盒 库存` 查看。
- 黑名单机制：`blacklist_user_ids` 中的用户将被静默拦截（不回复任何内容）。
- 管理员可用 `/方舟盲盒 管理员 黑名单 列表|添加 <user_id>|移除 <user_id>` 手动维护黑名单。

- 市场系统：`/方舟盲盒 市场` 可查看市场总览，`/方舟盲盒 市场 <种类ID>` 可查看该种类的当日定价细节。
- 定价模型：`最终价 = 基准价 × 市场波动系数 × 稀缺系数`，并且剩余数量越少价格越高。
- 库存价格展示：`/方舟盲盒 库存` 会显示每个条目的市场单价、数量和数量总价。

- 新增资源索引文件：插件会在数据目录自动生成 `resource_box_index.json`，实时同步 `resources/number_box` 和 `resources/special_box` 的盲盒文件名（自动排除引导图 selection/cover）。
- 市场价格为“同种类内每个盲盒独立定价”，例如一个种类 14 盒会分别计算 14 个价格。
- 市场支持用户上架：`/方舟盲盒 市场 上架 <种类ID> <奖品名> <价格> [数量]`。
- 市场支持购买：`/方舟盲盒 市场 购买 <种类ID> <奖品名> [数量]`。
- 用户上架价格会影响该商品的整体市场价格（均价影响）。
- 系统市场每天 0 点刷新，每天随机上架 3 种盲盒商品，售完即止。
