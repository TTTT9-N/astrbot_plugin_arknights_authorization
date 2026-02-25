# astrbot_plugin_arknights_authorization

明日方舟通行证盲盒互动插件（AstrBot）。

## 本次修复

1. 去除插件运行时自动创建资源目录逻辑。
2. 改为由你在 GitHub 仓库中手动维护 `resources` 目录结构。
3. 移除 `/方舟盲盒 资源路径` 指令，避免和帮助流程混淆。

## 资源目录（手动维护）

请在插件仓库中手动创建并提交以下目录：

```text
resources/
├─ number_box/
├─ special_box/
└─ revealed_box/
```

> 建议在三个目录下各放一个 `.gitkeep`，确保空目录也能被 Git 跟踪。

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
- `/方舟盲盒 列表`
- `/方舟盲盒 选择 <种类ID>`
- `/方舟盲盒 开 <序号>`
- `/方舟盲盒 状态 [种类ID]`
- `/方舟盲盒 刷新 [种类ID]`
- `/方舟盲盒 重载资源`
- `/方舟盲盒 管理员 列表|添加|移除|特殊定价`
