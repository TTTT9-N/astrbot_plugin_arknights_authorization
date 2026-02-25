# astrbot_plugin_arknights_authorization

明日方舟通行证盲盒互动插件（AstrBot）。

## 图片资源目录

按需加入想要的通行证：

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
└─ 7.0/
   ├─ selection.jpg
   ├─ 1-水月精一.png
   ├─ 2-水月精二.jpg
   └─ 3-水陈精一.webp
```

- `7.0`：种类 ID（用于 `/方舟盲盒 选择 7.0`）
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
- `/方舟盲盒 列表`
- `/方舟盲盒 选择 <种类ID>`
- `/方舟盲盒 开 <序号>`
- `/方舟盲盒 状态 [种类ID]`
- `/方舟盲盒 刷新 [种类ID]`
- `/方舟盲盒 重载资源`
- `/方舟盲盒 管理员 列表|添加|移除|特殊定价`


## WebUI 配置项

插件配置页可直接设置以下参数：

- `initial_balance`：新用户初始金额（默认 200）
- `number_box_price`：数字盒单抽价格（默认 25）
- `special_box_default_price`：特殊盒默认单抽价格（默认 40）
- `admin_ids`：管理员账号 ID 列表
- `special_box_prices`：特殊盒单独定价对象（如 `{"sp_xxx": 66}`）

> 插件已改为使用仓库根目录 `_conf_schema.json` 注册 WebUI 配置项（符合 AstrBot 插件配置文档）。
