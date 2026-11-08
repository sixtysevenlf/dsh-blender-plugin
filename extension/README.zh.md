# `dsh_blender_bridge` 包 —— Blender 侧扩展（本仓附带，唯一受支持版本）

[English](README.md) | 中文

配套 [`dsh-blender-plugin`](../docs/zh/README.md) 的 Blender 内扩展。

> **3.1 起 Blender 侧是 Blender 4.2 Extension 包：zip 只有一个顶层 `dsh_blender_bridge/`，内含
> `blender_manifest.toml`；单文件 `dsh_blender_bridge.py` 不再是安装入口，且已从本仓删除。**
> 旧单文件安装过的用户请先在 Blender 里停用并移除它，再安装 `dsh_blender_bridge.zip`（见
> [`../MIGRATIONS.md`](../MIGRATIONS.md)）。上游 `MCP for Blender`（模块 `blender_mcp`）与
> harveyxiacn `blender_mcp_addon` 同样不再支持。

## 这个包是什么

| 项 | 值 |
|---|---|
| 安装包 / 模块名 | `dsh_blender_bridge.zip`（包根 `dsh_blender_bridge/`，含 `blender_manifest.toml`） / `dsh_blender_bridge` |
| 源码目录 | `extension/dsh_blender_bridge/`（`__init__.py` 为入口，子包 `commands/`、`recorders/`、`assets/`、`integrations/`、`ui/`） |
| 显示名（Blender 插件列表里） | **DSH Blender Bridge** |
| 版本 / 协议 | extension **v2.0.0 / protocol 12**；extension manifest v2.0.0 |
| 标识前缀 | `dshbb_`（注册类 `bpy.types.dshbb_server`、5 个集成开关 `dshbb_use_*`） |
| TCP 端口 | Gateway `127.0.0.1:39877`；addon 主动连接，不监听入站端口 |
| 命令面 | 25 条（18 条常驻 + 7 条集成门控；其中 21 条只读、4 条内部传输；命令名、参数与返回结构向后不兼容，见 MIGRATIONS） |

## 来源与许可（GPL-3.0-or-later，上游 MIT）

本包是 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) 的**衍生改名版**（上游 MIT）。
改名只动了**显示名、模块名与标识前缀**，**上游版权与 MIT 许可声明保留**；本包整体按
**GPL-3.0-or-later** 分发（见 extension 根目录的 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)，以及
`blender_manifest.toml` 的 `license`）。DSH 宿主插件本体仍是 BSD-3-Clause（仓库根 `LICENSE`）。

```
# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025
```

上游 MIT 许可要求保留上述版权声明 —— **请勿删除**；完整 MIT 文本与 GPL 文本随扩展 zip 分发。

## 构建 zip

仓库已提交 `extension/dsh_blender_bridge.zip`；改源码后重新构建：

```bash
node scripts/build-extension.mjs          # 写 extension/dsh_blender_bridge.zip
node scripts/build-extension.mjs --check  # 只校验现有 zip 清单与版本
```

zip 内只有一个包根 `dsh_blender_bridge/`，含 `blender_manifest.toml`、`LICENSE`、`NOTICE` 与全部 `.py` 模块，不含
`__pycache__` / `.pyc`，并使用固定时间戳保证可复现。构建脚本同时校验 manifest、`gateway_schema.py`
与 `config/gateway.json` 的版本一致，并用 Blender 4.2 的 `--command extension validate` 验证产物。

## 装法

1. Blender → `Edit ▸ Preferences ▸ Extensions ▸ Install from Disk...` → 选本目录的 `dsh_blender_bridge.zip`（Blender 扩展包，不是目录、不是单个 `.py`）。
   - 如果列表里有旧单文件扩展（模块 `dsh_blender_bridge` 的 `.py` 版本）、旧 `MCP for Blender` /
     `blender_mcp` / `blender_mcp_addon`：**先取消勾选并卸载/移除**（同一模块名不能同时装两份，两个扩展也会同时向 Gateway 注册）。
2. 勾选启用 **DSH Blender Bridge**。
3. 3D 视图按 `N` → 找到「DSH Blender Bridge」面板 → 连接到本地 Gateway（`127.0.0.1:39877`）。

> Blender 与 Gateway 的启动/连接由用户与宿主管理：模型面没有 launch 工具。先打开 Blender 并启用本扩展，
> 再在面板里 Connect；Gateway 由 DSH 宿主启动。

## 验证

```bash
blender_inspect(command="get_addon_info")     # addon_version 应为 [2, 0, 0]，protocol_version 应为 12
blender_inspect(command="get_activity_state") # state 可能为 idle / human / agent / both
```

装好后在 Blender 里选物体 → `N` →「Selection → DSH」→ 发送，选中会进 **DSH 当前会话草稿**（不自动发送）。
编辑模式选顶点/边/面、姿态模式选骨骼、编辑曲线/晶格控制点也会一起发送（payload 里 `selection.sub`）；
N 面板的对象 / 成员列表默认折叠，展开后带滚动条且高度可调。

面板结构：活动状态置顶（AI 操作时显示红色警告）+「暂停 AI 操作」按钮；
「环境与角色模型API调用」为父面板，内含「环境生成」（Poly Haven / Sketchfab / Poly Pizza）与
「模型生成」（Hyper3D / Hunyuan3D）两个子面板，默认全部折叠。暂停期间 socket 只放行只读命令。

## 环境变量（旧名仍兼容）

新名优先、旧名回退：`DSHBB_HYPER3D_API_KEY` → 回退 `BLENDERMCP_HYPER3D_API_KEY`，其余同理
（`SKETCHFAB` / `POLYPIZZA` / `HUNYUAN3D_*`）。**你已设的旧变量不会失效**（这只是 key 名兼容，不代表支持旧扩展）。