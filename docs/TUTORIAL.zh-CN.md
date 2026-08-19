# Loopbox 保姆级教程：从零搭建一个能自我迭代的本地沙箱 Loop

> 面向 macOS Apple 芯片（M1–M5）。全程大约 15 分钟。
> 目标产物：一个本地运行的 E2B 兼容沙箱系统 + 一个由 LoopX 驱动、能
> 「自检 → 思考 → 迭代」、并在关键处等待你确认的长期 Loop。

---

## 第 0 步：准备环境（一次性）

你需要：macOS 13+（虚拟机后端需 14+）、Apple Silicon、Python 3.10+、Git。

```bash
# 1. 安装 GitHub CLI 并登录（用于建仓库）
brew install gh
gh auth login

# 2. 安装 LoopX（控制平面，驱动 Loop 的心跳/配额/门禁）
python3 -m pip install --upgrade loopx
# 如果 pip 安装后 loopx 不在 PATH，可用官方免克隆安装器：
# curl -fsSL https://huangruiteng.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor   # 看到 ok: True 即可
```

## 第 1 步：拿到 Loopbox 代码并安装

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
python3 -m pip install -e .
loopbox doctor
```

`loopbox doctor` 会逐项自检并报告，全部应为 `[ ok ]`：

| 检查项 | 含义 |
| --- | --- |
| arm64 architecture | 必须 Apple Silicon（M1–M5 均可） |
| macOS version | 13+（`vz` 后端 14+） |
| sandbox-exec available | macOS 自带 Seatbelt 沙箱工具 |
| APFS clonefile support | 快照/fork 依赖的写时复制能力 |
| vzrunner helper | 可选，VM 后端的 Swift 助手 |
| seatbelt smoke test | 真实跑一个沙箱内 `echo ok` |

## 第 2 步：五分钟玩转沙箱

```bash
# 创建沙箱（返回 id，例如 sbx_9f2c41ab07d1）
SID=$(loopbox new)

# 在沙箱内执行命令（字符串会走 /bin/zsh -lc，支持管道和重定向）
loopbox exec $SID -- zsh -lc 'echo hello > note.txt && cat note.txt'

# 验证隔离：试图写到沙箱外会被 Seatbelt 拒绝
loopbox exec $SID -- zsh -lc 'echo hack > ~/escape.txt'   # 会失败
loopbox exec $SID -- zsh -lc 'cat ~/.ssh/id_ed25519'      # 永远被拒绝

# 快照（APFS 写时复制，O(1) 开销）
loopbox snapshot $SID --name v1

# 从快照分叉一个完全相同的沙箱
TWIN=$(loopbox fork $SID --snapshot v1)

# 暂停 / 恢复（SIGSTOP/SIGCONT 整个进程组）
loopbox pause $SID && loopbox resume $SID

# 清理
loopbox rm $TWIN --purge
loopbox rm $SID --purge
```

Python SDK（与 E2B SDK 同形）：

```python
from loopbox import Sandbox

sbx = Sandbox.create(template="seatbelt")        # template 对应后端
r = sbx.commands.run("echo hello && uname -m")   # shell 语义
sbx.files.write("notes/a.txt", "hi")             # 相对工作区根目录
sbx.pause()                                      # = E2B beta_pause
twin = sbx.fork()                                # 分叉
sbx.resume()
sbx.kill()
```

## 第 3 步：启动 E2B 兼容 HTTP 服务

```bash
loopbox serve --port 31885 &
# 令牌自动生成在 ~/.loopbox/auth.json（权限 0600）
KEY=$(python3 -c "import json;print(json.load(open('$HOME/.loopbox/auth.json'))['token'])")

curl -X POST localhost:31885/sandboxes -H "X-API-Key: $KEY" -d '{"template":"seatbelt"}'
curl -X POST localhost:31885/sandboxes/<sandboxID>/exec -H "X-API-Key: $KEY" \
     -d '{"command":"echo via-http"}'
```

路由形态与 E2B 一致（`POST /sandboxes`、`/exec`、`/pause`、`/resume`、`/fork`、
`/snapshots`、`GET|PUT /files`），鉴权头也是 E2B 同款 `X-API-Key`——
现有 E2B 客户端换个 base URL 就能指向本机。

## 第 4 步：让 Codex / Claude Code / DSH 在沙箱里跑

```bash
loopbox harness codex    # Codex CLI 在沙箱内启动，cwd = 沙箱工作区
loopbox harness claude   # Claude Code 同理
loopbox harness dsh      # DeepSeek Harness；-- 之后参数原样透传
```

此刻 harness 进程被 Seatbelt 约束：能读工具链、能联网（默认 `outbound`），
但**只能写沙箱工作区**，且永远读不到 `~/.ssh`、钥匙串、浏览器 Cookie。

**Agent 探索过程中的人机协同**：另开一个终端——

```bash
loopbox ls                 # 找到 harness 沙箱 id
loopbox pause $SID         # 暂停整个 Agent 会话（它会被冻结）
loopbox snapshot $SID --name before-risky-step
loopbox fork $SID --snapshot before-risky-step   # 分叉去探索另一条路
loopbox resume $SID        # 主线继续
```

这就是「随时 pause → fork → resume」。

## 第 5 步：接入 LoopX，让 Loop 长期自我运转

```bash
cd loopbox
loopx connect
loopx status
```

如果提示没有目标状态，用引导式创建：

```bash
loopx start-goal --guided --project . --goal-text \
  "Continuously harden and extend Loopbox per GOAL.md: vz backend, PTY streaming, templates, CI."
```

然后在你常用的 Agent 里驱动 Loop：

| 宿主 | 驱动方式 |
| --- | --- |
| Codex CLI | 在项目根启动 `codex`，让它 `loopx doctor` 并用 `$loopx <任务>` 或 `/skills` 里的 loopx；循环体用 `/goal <thin task_body>` 维持 |
| Claude Code | 安装 LoopX 适配器后用 `/loopx <任务>` + `/loop` |
| DSH | `dsh` 启动后把上面的「Connect this repo to LoopX」提示词贴给它 |
| 本仓库自带引擎 | `loopbox loop` 直接跑「自检 → 规划 → 执行 → 验证」循环 |

LoopX 会让 Loop **可持续**：配额（quota）决定下一次 tick 是否该跑、
`scheduler_hint` 决定退避，目标/门禁/证据跨会话持久化，换 harness 不丢状态。

## 第 6 步：Human-in-the-Loop（人的参与点）

Loop 在以下情况**不会**自行决定，而是写出 `.loopbox/loops/<loop_id>/GATE.md`
（或 LoopX 的 user gate）并等待你：

1. **批准计划**——开始大动作前列出计划让你过目；
2. **高风险操作**——放宽 Seatbelt 配置、删除数据、发布到外部；
3. **证据不足**——测试矛盾、需求歧义。

你只需要回答 Gate 里的问题（编辑 GATE.md 或在 harness 里回复），Loop 继续。

## 第 7 步：验证 Loop 在正确运转

- `loopx doctor` 全绿；
- `loopx status` 显示当前目标、当前 user gate、下一条 agent todo；
- `python3 -m pytest tests/` 与 `LOOPBOX_INTEGRATION=1 python3 -m pytest tests/` 全过；
- `loopbox doctor` 全过；
- 仓库里只有英文代码/注释，`.loopx/`、`.loopbox/`、`.local/` 未被提交。

## 常见问题

- **`vzrunner not built`**：可选。需要 VM 级隔离时 `cd vzrunner && ./build.sh`
  （需 Xcode CLT、macOS 14+；脚本会自动 ad-hoc 签名虚拟化 entitlement）。
- **login shell 启动有告警**：沙箱内 `zsh -l` 会加载你的 `.zprofile`，其中对
  沙箱外路径的写入会被 Seatbelt 拦截并打印告警，不影响命令本身；用
  `["/bin/zsh","-c",...]`（不带 `-l`）可完全避免。
- **HTTP 401**：确认请求头带的是 `~/.loopbox/auth.json` 里的最新 token。
