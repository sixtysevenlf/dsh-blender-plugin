# `dsh_blender_bridge` 包 —— Blender 侧 addon（本仓附带，唯一受支持版本）

配套 [`@dsh-external/dsh-blender-plugin`](../zh-README.md) 的 Blender 内 addon。

> **v3.0 起 addon 以包 + zip 安装，单文件 `dsh_blender_bridge.py` 不再是安装入口，且已从本仓删除。**
> 旧单文件安装过的用户请先在 Blender 里停用并移除它，再安装 `dsh_blender_bridge.zip`（见
> [`../MIGRATIONS.md`](../MIGRATIONS.md)）。上游 `MCP for Blender`（模块 `blender_mcp`）与
> harveyxiacn `blender_mcp_addon` 同样不再支持。

## 这个包是什么

| 项 | 值 |
|---|---|
| 安装包 / 模块名 | `dsh_blender_bridge.zip`（包根 `dsh_blender_bridge/`） / `dsh_blender_bridge` |
| 源码目录 | `addon/dsh_blender_bridge/`（`__init__.py` 为入口，子包 `commands/`、`recorders/`、`assets/`、`integrations/`、`premium/`、`ui/`） |
| 显示名（Blender 插件列表里） | **DSH Blender Bridge** |
| 版本 / 协议 | addon **v1.11 / protocol 11** |
| 标识前缀 | `dshbb_`（注册类 `bpy.types.dshbb_server`、5 个集成开关 `dshbb_use_*`） |
| TCP 端口 | `127.0.0.1:9876`（只监听本机） |
| 命令面 | 39 条（23 条常驻 + 16 条集成门控；命令名、参数、返回结构与 pause 语义与 v1.10 一致） |

## 来源与许可（MIT）

本包是 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) 的**衍生改名版**（MIT）。
改名只动了**显示名、模块名与标识前缀**，**版权与许可文本原样保留**：

```
# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025
    "author": "Siddharth Ahuja",
```

上游 MIT 许可要求保留上述版权声明 —— **请勿删除**。

## 构建 zip

仓库已提交 `addon/dsh_blender_bridge.zip`；改源码后重新构建：

```bash
node scripts/build-addon.mjs          # 写 addon/dsh_blender_bridge.zip
node scripts/build-addon.mjs --check  # 只校验现有 zip 清单与版本
```

zip 内只有一个包根 `dsh_blender_bridge/`，不含 `__pycache__` / `.pyc`，并使用固定时间戳保证可复现。

## 装法

1. Blender → `Edit ▸ Preferences ▸ Add-ons`：
   - 如果列表里有旧单文件 addon（模块 `dsh_blender_bridge` 的 `.py` 版本）、旧 `MCP for Blender` /
     `blender_mcp` / `blender_mcp_addon`：**先取消勾选并卸载/移除**（同一模块名不能同时装两份，两个 addon 也会抢 9876）。
   - `Install...` → 选本目录的 `dsh_blender_bridge.zip`（不是目录、不是单个 `.py`）。
2. 勾选启用 **DSH Blender Bridge**。
3. 3D 视图按 `N` → 找到「DSH Blender Bridge」面板 → 点 **Connect**（端口 9876）。

> 也可以让插件**一键拉起**：`blender_viewport(op="launch")` —— 拉起 GUI Blender 并**自动扫到并启用**
> 本 addon（目录或 zip 形式的 `dsh_blender_bridge` / `dshbb_*`），并校验 addon v1.10+ / protocol 11。

## 验证

```bash
blender_rt_cmd get_addon_info     # addon_version 应为 [1, 11]，protocol_version 应为 11
blender_rt_cmd get_activity_state # state 可能为 idle / human / agent / both
```

装好后在 Blender 里选物体 → `N` →「Selection → DSH」→ 发送，选中会进 **DSH 当前会话草稿**（不自动发送）。
编辑模式选顶点/边/面、姿态模式选骨骼、编辑曲线/晶格控制点也会一起发送（payload 里 `selection.sub`）；
N 面板的对象 / 成员列表默认折叠，展开后带滚动条且高度可调。

面板结构：活动状态置顶（AI 操作时显示红色警告）+「暂停 AI 操作」按钮；
「环境与角色模型API调用」为父面板，内含「环境生成」（Poly Haven / Sketchfab / Poly Pizza）与
「模型生成」（Hyper3D / Hunyuan3D / Tripo）两个子面板，默认全部折叠。暂停期间 socket 只放行只读命令。

## 环境变量（旧名仍兼容）

新名优先、旧名回退：`DSHBB_HYPER3D_API_KEY` → 回退 `BLENDERMCP_HYPER3D_API_KEY`，其余同理
（`SKETCHFAB` / `POLYPIZZA` / `HUNYUAN3D_*` / `PREMIUM_*`）。**你已设的旧变量不会失效**（这只是 key 名兼容，不代表支持旧 addon）。
