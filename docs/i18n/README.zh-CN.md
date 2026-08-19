# Loopbox

**面向 macOS Apple 芯片（M1–M5）的本地优先、兼容 E2B 协议的沙箱。**

[English](../../README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox 让 AI Agent harness（Codex CLI、Claude Code、DSH / DeepSeek Harness 以及你自建的
runner）在你自己的 Mac 上真正的沙箱里执行不可信的工作——不依赖云端。日常任务使用
Seatbelt 进程沙箱实现秒级启动；需要整机级隔离时，可启用实验性的
Virtualization.framework 后端，获得运行中虚拟机的暂停 / 分叉 / 恢复能力。

```bash
pip install -e .        # Python 3.10+，macOS 13+，Apple Silicon
loopbox doctor          # 自检：架构、Seatbelt、APFS 克隆、冒烟测试

SID=$(loopbox new)                        # 创建沙箱
loopbox exec $SID -- echo "hello sandbox" # 在沙箱内执行
loopbox snapshot $SID --name v1           # APFS 写时复制快照
loopbox fork $SID --snapshot v1           # 分叉出一个完全相同的副本
loopbox pause $SID && loopbox resume $SID # 暂停 / 恢复
loopbox harness codex                     # 在沙箱里启动 Codex CLI
loopbox rm $SID --purge
```

## 为什么选择 Loopbox

托管沙箱（如 E2B）很优秀，但有时代码、凭证和延迟预算必须留在你自己的机器上。
Loopbox 保留 E2B 的**使用协议**（SDK 形态、HTTP API 形态、快照语义），执行完全在本地。

## 特性

- **Apple Silicon 原生** —— 支持 M1 至 M5，要求 macOS 13+（实验性 `vz` 虚拟机后端需 macOS 14+）。
- **两种隔离引擎**
  - `seatbelt`（默认）：基于 `sandbox-exec` 的 macOS Seatbelt 进程沙箱。秒级启动、
    写入范围隔离；凭证目录（`~/.ssh`、钥匙串、浏览器 Cookie、云 CLI 配置）永不可读；
    每个沙箱独立的网络策略（`outbound` / `all` / `deny`）。
  - `vz`（实验性）：通过内置 Swift `vzrunner` 使用 Virtualization.framework 微型虚拟机。
    整机状态带来真正的暂停 → 快照 → 分叉 → 恢复。
- **兼容 E2B 协议** —— Python SDK 与 E2B SDK 同形（`Sandbox.create()`、
  `commands.run()`、`files.read/write()`、`pause()`、`fork()`、`kill()`）；
  HTTP API 采用 E2B 风格路由，并使用与 E2B 相同的 `X-API-Key` 令牌鉴权。
- **完整本地 CLI** —— 所有功能均可通过 `loopbox ...` 使用，全面支持 `--json` 便于脚本化。
- **高性能快照** —— APFS 写时复制克隆，未变更数据的开销为 O(1)。
- **为 Agent harness 而生** —— `loopbox harness codex|claude|dsh` 在沙箱内启动 Agent CLI；
  `loopbox loop` 提供带人工门禁（Human-in-the-Loop）的循环引擎。
- **零运行时依赖** —— 仅使用 Python 标准库。

## 在沙箱中运行 Agent harness

```bash
loopbox harness codex                # Codex CLI，隔离运行，工作区即沙箱
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness，-- 后为透传参数
```

harness 进程运行在 Seatbelt 之下：可以读取工具链、按默认策略访问外网，但只能**写入**
沙箱工作区，且永远无法读取你的凭证。你可以在另一个终端 `loopbox pause` 整个会话、
`loopbox snapshot` 快照、`loopbox fork` 出探索分支、再 `loopbox resume`——人始终在环中。

## 循环引擎与人工门禁

`loopbox loop` 围绕一个目标执行「自检 → 规划 → 执行 → 验证」循环，状态持久化在
`.loopbox/`。当循环需要人类判断（批准计划、高风险操作、证据不足）时，它会写出
`GATE.md` 问题并等待人工回答，而不是自行猜测。配合
[LoopX](https://github.com/huangruiteng/loopx) 可获得跨 harness 的目标状态、配额与心跳，
详见 [../loopx-integration.md](../loopx-integration.md)。

## 安全模型（如实说明）

- `seatbelt` 是强力的**进程级**沙箱：写入限制、凭证隔离、可选网络封禁。它不是虚拟机；
  内核级攻击不在防护范围内——有此需求请使用 `vz` 后端。
- HTTP 服务默认仅绑定 `127.0.0.1`，除设置 `LOOPBOX_NO_AUTH=1` 外一律要求令牌
  （请勿在共享机器上这样做）。
- 沙箱状态存放在 `~/.loopbox`（可用 `LOOPBOX_HOME` 覆盖）。

## 开发

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # 单元测试
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # 含真实 Seatbelt 集成测试
```

代码与注释使用英文；文档提供多语言版本。

## 许可证

Apache-2.0
