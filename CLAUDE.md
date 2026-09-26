# CLAUDE.md

见 [AGENTS.md](AGENTS.md) —— 本仓库的 AI 助手约定。

最要紧的两条：

1. **安装 / 配置 / 排查前先读** `README.zh-CN.md` 的 §2 前置条件、§3 安装、§7 常见故障（英文见 `README.md`）。
2. **「装好了」的判据是 `doctor` 回 `kind=ok`**（`curl -sS http://127.0.0.1:9877/doctor` 与 `blender_viewport(op="doctor")`），没到就不是装好。
