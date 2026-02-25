# astrbot_plugin_arknights_authorization

明日方舟通行证盲盒互动插件（AstrBot）。

## 功能说明

- 支持预设多个盲盒种类，并区分为：
  - 数字盒（固定价格，默认 25 元/抽）
  - 特殊盒（由管理员定义价格）
- 新增金钱系统：用户需先注册才可开盒，默认初始金额 200 元。
- 钱包按“QQ群 + 用户QQ”维度独立，群组之间互不影响。
- 开盒结果从对应种类奖池中随机抽取，抽中后从奖池移除。
- 每个序号在同一种类中仅可选择一次，直到手动刷新该种类。
- 当卡池或可选序号耗尽时提示用户，可自主选择“刷新该种类”或“切换种类”。
- 不同盲盒种类奖池彼此独立，互不影响。

## 指令

- `/方舟盲盒 注册`：注册并领取初始金额。
- `/方舟盲盒 钱包`：查看当前余额。
- `/方舟盲盒 列表`：查看可用盲盒种类与价格。
- `/方舟盲盒 选择 <种类ID>`：选择某个盲盒种类并返回当前种类示意图。
- `/方舟盲盒 开 <序号>`：开启指定序号盲盒（按种类价格扣费）。
- `/方舟盲盒 状态 [种类ID]`：查看当前奖池与可选号剩余情况。
- `/方舟盲盒 刷新 [种类ID]`：手动刷新当前（或指定）种类。
- `/方舟盲盒 管理员 列表`
- `/方舟盲盒 管理员 添加 <user_id>`
- `/方舟盲盒 管理员 移除 <user_id>`
- `/方舟盲盒 管理员 特殊定价 <种类ID> <金额>`

## WebUI 配置

插件支持从 AstrBot WebUI 插件配置读取并同步以下参数（若当前 AstrBot 版本支持该能力）：

- `initial_balance`：注册初始金额（默认 200）
- `number_box_price`：数字盒单抽价格（默认 25）
- `special_box_default_price`：特殊盒默认价格（默认 40）
- `admin_ids`：管理员账号 ID 列表
- `special_box_prices`：特殊盒按种类定价

此外，插件会自动监控 `data/runtime_config.json` 和 `data/box_config.json` 文件变更并热重载。

## 数据文件

插件首次运行会自动生成以下文件：

- `data/box_config.json`：盲盒种类配置（含 `box_type` 和可选 `price`）。
- `data/pool_state.json`：各盲盒种类当前剩余奖池状态。
- `data/slot_state.json`：各盲盒种类当前剩余可选序号状态。
- `data/sessions.json`：用户会话（记录每个用户当前选中的盲盒种类）。
- `data/blindbox.db`：SQLite 数据库，记录不同群组下用户QQ与余额（群组隔离）。
- `data/runtime_config.json`：运行时配置（价格、管理员等）。

### `box_config.json` 结构示例

```json
{
  "num_vc17": {
    "name": "2024音律联觉通行证盲盒（数字盒）",
    "box_type": "number",
    "slots": 14,
    "selection_image": "https://example.com/ak-vc17-selection.jpg",
    "items": {
      "vc17-01": {
        "name": "山 通行证卡套",
        "image": "https://example.com/ak-vc17-01.jpg"
      }
    }
  },
  "sp_anniv": {
    "name": "周年系列通行证盲盒（特殊盒）",
    "box_type": "special",
    "price": 68,
    "slots": 12,
    "selection_image": "https://example.com/ak-anniv-selection.jpg",
    "items": {}
  }
}
```

## 备注

- `selection_image` 建议使用你已经标好序号（从左到右、从上到下）的图。
- 插件优先尝试发送图片消息；若适配器不支持，会回退为文字 + 图片链接。


## WebUI 配置排查

- 若插件配置弹窗显示“这个插件没有配置”，请升级到此版本并重载插件。
- 本版本在 `metadata.yaml` 中同时提供 `config`、`configs`、`config_schema` 三种字段，用于兼容不同 AstrBot 版本的配置读取方式。


- 若仍提示“无法识别账号ID”，请在 NapCat 适配器下确认事件包含 `sender/user_id` 字段；本版本已增强多字段兼容识别。


## 持久化说明

- 本版本开始，插件数据默认优先存放在独立持久化目录（优先 `/opt/AstrBot/data/plugin_data/astrbot_plugin_arknights_authorization`），避免插件热重载/卸载时因代码目录变更导致数据丢失。
- 升级后会自动尝试把旧 `data/` 目录中的历史数据迁移到持久化目录。

- 本版本支持热重载 `pool_state.json` 与 `slot_state.json`，并兼容监听旧 `data/` 目录更新后自动同步。
