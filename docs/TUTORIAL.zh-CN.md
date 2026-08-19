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

`loopbox doctor` 逐项自检并报告。除 `vzrunner helper` 之外全部应为 `[ ok ]`；
`vzrunner helper` 缺了只显示 `[warn]`，不影响 seatbelt 后端，只有要用 `vz` 虚拟机后端时才需要它：

| 检查项 | 含义 |
| --- | --- |
| arm64 architecture | 必须 Apple Silicon（M1–M5 均可） |
| macOS version | 13+（`vz` 后端 14+） |
| sandbox-exec available | macOS 自带 Seatbelt 沙箱工具 |
| APFS clonefile support | 快照/fork 依赖的写时复制能力 |
| vzrunner helper (vz backend) | 可选，VM 后端的 Swift 助手 |
| seatbelt smoke test (echo ok) | 真实跑一个沙箱内 `echo ok` |

## 第 2 步：五分钟玩转沙箱

```bash
# 创建沙箱（返回 id，例如 sbx_9f2c41ab07d1）
SID=$(loopbox new)

# 在沙箱内执行命令。CLI 把 `--` 之后的 argv 原样执行（不带 shell），
# 需要管道和重定向就自己显式包一层 shell：
loopbox exec $SID -- zsh -lc 'echo hello > note.txt && cat note.txt'
#（SDK 的 commands.run 和 HTTP /exec 收到字符串时会自动套上 /bin/zsh -lc）

# 验证隔离：试图写到沙箱外会被 Seatbelt 拒绝
loopbox exec $SID -- zsh -lc 'echo hack > ~/escape.txt'   # 会失败
loopbox exec $SID -- zsh -lc 'cat ~/.ssh/id_ed25519'      # 永远被拒绝

# 快照与回滚（seatbelt 快照是工作区的 APFS 写时复制克隆，O(1) 开销；
# 只含文件，不含进程/内存——整机状态是 vz 后端的能力）
loopbox snapshot $SID --name v1
loopbox snapshots $SID           # 列出快照
loopbox restore $SID v1          # 把工作区回滚到 v1

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
r = sbx.commands.run("echo hello && uname -m")   # 字符串命令走 /bin/zsh -lc
sbx.files.write("notes/a.txt", "hi")             # 相对工作区根目录
sbx.pause()                                      # = E2B beta_pause
snap = sbx.snapshot(name="v1")                   # 返回快照 id
twin = sbx.fork(snapshot_id=snap)                # 分叉出一个新沙箱
sbx.resume()
sbx.kill()
```

## 第 3 步：启动 E2B 兼容 HTTP 服务

```bash
loopbox serve --port 31885 &   # 31885 就是默认端口，可省略 --port
# 令牌自动生成在 ~/.loopbox/auth.json（权限 0600）
KEY=$(python3 -c "import json;print(json.load(open('$HOME/.loopbox/auth.json'))['token'])")

curl -X POST localhost:31885/sandboxes -H "X-API-Key: $KEY" -d '{"templateID":"seatbelt"}'
curl -X POST localhost:31885/sandboxes/<sandboxID>/exec -H "X-API-Key: $KEY" \
     -d '{"command":"echo via-http"}'
```

注意建沙箱请求体里的模板字段名是 E2B 的 `templateID`（不是 `template`），
取值是 loopbox 后端名（`seatbelt` 或 `vz`）。

路由形态与 E2B 一致（`POST /sandboxes`、`/exec`、`/pause`、`/resume`、`/fork`、
`/snapshots`、`GET|PUT /files`），鉴权头也是 E2B 同款 `X-API-Key`——
现有 E2B 客户端换个 base URL 就能指向本机。`GET /health` 无需鉴权，可直接探活。

## 第 4 步：让 Codex / Claude Code / DSH 在沙箱里跑

harness 跑在一个先建好的沙箱里；`run` 把 `--` 之后的参数原样透传给 harness CLI：

```bash
SID=$(loopbox new)                         # harness 进程的沙箱
loopbox harness list                       # 已知 harness CLI 及检测状态
loopbox harness describe codex             # 某个 harness 的适配说明与启动示例
loopbox harness doctor                     # 装了哪些 harness + 接入指引

loopbox harness run $SID codex  -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh    -- cli     # DeepSeek Harness 交互式终端
loopbox harness run $SID <任意二进制名> -- ...   # 自定义 harness（按 PATH 上的名字）
```

此刻 harness 进程被 Seatbelt 约束：能读工具链、能联网（默认 `outbound`），
但**只能写沙箱工作区**（外加 `/tmp` 等临时目录），且永远读不到 `~/.ssh`、
钥匙串、浏览器 Cookie。loopbox 也**不会**替你注入
`--dangerously-skip-permissions` 之类的免授权参数——是否使用由你决定，
并且这类参数只应在沙箱内使用。

**Agent 探索过程中的人机协同**（同一个 `$SID`，或另开终端用 `loopbox ls` 找到它）：

```bash
loopbox pause $SID         # 暂停整个 Agent 会话（冻结进程组）
loopbox snapshot $SID --name before-risky-step
loopbox fork $SID --snapshot before-risky-step   # 分叉去探索另一条路
loopbox resume $SID        # 主线继续
```

这就是「随时 pause → fork → resume」。

## 第 5 步：接入 LoopX，让 Loop 长期自我运转

```bash
cd loopbox
loopx connect    # 在 .loopx/ 下建立本仓库的 LoopX 状态
loopx status     # 查看当前目标、门禁和下一条动作
```

如果还没有目标，用引导式创建：

```bash
loopx start-goal --guided --project . --goal-text \
  "Continuously harden and extend Loopbox per GOAL.md: vz backend, PTY streaming, templates, CI."
```

然后在你常用的 Agent 里驱动 Loop：

| 宿主 | 驱动方式 |
| --- | --- |
| Codex CLI | 在项目根启动 `codex`，让它跑 `loopx doctor`；用 `$loopx <任务>` 或 `/skills` 里的 loopx 技能维持循环体 |
| Claude Code | 先 `loopx slash-commands --install` 安装技能，再用 `/loopx <任务>` + `/loop` |
| DSH | 用 `loopx new-project-prompt` 生成「Connect this repo to LoopX」提示词贴给它 |
| 本仓库自带引擎 | `loopbox loop` 直接跑「自检 → 规划 → 执行 → 验证」循环（见第 6 步） |

LoopX 会让 Loop **可持续**：配额（quota）决定下一次 tick 是否该跑、
`scheduler_hint` 决定退避，目标/门禁/证据跨会话持久化，换 harness 不丢状态。

## 第 6 步：Human-in-the-Loop（人的参与点）

本仓库自带的 Loop 引擎（`loopbox loop`）在以下情况**不会**自行决定，
而是挂起一个 Gate 并等待你（LoopX 侧对应 user gate）：

1. **批准计划**（`approve_plan`）——开始大动作前列出计划让你过目；
2. **高风险步骤**（`approve_step`）——`rm -rf`、`sudo`、`git push` 等危险命令
   必定过门禁，除非 `run` 时加了 `--auto-approve`；引擎想不清怎么继续时
   （证据不足、需求歧义）也会以这类 Gate 向你提问；
3. **步骤失败**（`on_failure`）——由你决定重试、纠正方向还是中止。

每个挂起的 Gate 都会落到 Loop 自己的目录 `~/.loopbox/loops/<loop_id>/`：
`GATE.md`（人读的问题与答复指引）和 `gate.json`（机读、可手改）。
账本 `loop.json` 每步落盘，Loop 被杀后用 `loopbox loop run <loop_id>` 从检查点续跑。

回答 Gate 有三种方式：

- **在 `run` 的终端里**：stdin 是终端时引擎会直接提示，回答
  `a`（批准）/ `r [原因]`（拒绝）/ `s <说明>`（steer）；
- **另开终端用 CLI（推荐）**：

```bash
loopbox loop approve <loop_id> [gate_id] [--note TEXT]
loopbox loop reject  <loop_id> [gate_id] --reason "方向不对"   # 会把 Loop 标记为失败
loopbox loop steer   <loop_id> --note 'run: make test'  # `run:` 前缀会把命令排进沙箱执行
loopbox loop run     <loop_id>                          # 回答后恢复推进
```

- **直接编辑 `gate.json`**：把 `"status"` 改成 `"approved"` / `"rejected"` /
  `"steered"`（可附 `"note"`），下一次 `run` 会自动采纳。

仓库自带 Loop 引擎的完整流程：

```bash
loopbox loop new --goal "create hello.txt containing 'hi' and verify it" --sandbox seatbelt
loopbox loop run <loop_id>       # 第一次会先停在计划门禁上
loopbox loop approve <loop_id>   # 批准计划
loopbox loop run <loop_id>       # 在沙箱里逐步执行
loopbox loop status <loop_id> [--json]
loopbox loop history <loop_id>
```

`loopbox loop run` 的退出码：`0` 目标达成，`1` 失败，`2` 预算耗尽或运行被
中断，`3` 有 Gate 等待人工处理。装了 `codex` 或 `claude` 时「思考」步骤会交给
对应 CLI（可用 `LOOPBOX_HARNESS="cmd {prompt}"` 覆盖，`LOOPBOX_HARNESS_TIMEOUT`
调超时）；都没有则退回确定性规则兜底，并把需要判断的决策一律升级给 Gate。

## 第 7 步：验证 Loop 在正确运转

- `loopx doctor` 全绿；
- `loopx status` 显示当前目标、当前 user gate、下一条 agent todo；
- `python3 -m pytest tests/` 与 `LOOPBOX_INTEGRATION=1 python3 -m pytest tests/` 全过；
- `loopbox doctor` 全过；
- 仓库里只有英文代码/注释，`.loopx/`、`.loopbox/`、`.codex/goals/`、`.local/`
  未被提交（`.gitignore` 已覆盖）。

## 常见问题

- **`vzrunner not built`**：可选。需要 VM 级隔离时 `cd vzrunner && ./build.sh`
  （需 Xcode CLT、macOS 14+；脚本会自动 ad-hoc 签名虚拟化 entitlement，产物在
  `vzrunner/.build/release/vzrunner`）。注意 `vz` 后端仍是**实验性**的：
  `exec` 目前以 guest 内核命令行 `init=` shim 实现——每次 exec 都是一次
  全新启动，命令 stdout 不会写进 `ExecResult`，也还没有 guest 内 vsock agent，
  因此 pause/restore 无法跨 exec 携带运行中的 shell。详见 `vzrunner/README.md`。
- **login shell 启动有告警**：沙箱内 `zsh -l` 会加载你的 `.zprofile`，其中对
  沙箱外路径的写入会被 Seatbelt 拦截并打印告警，不影响命令本身；用
  `["/bin/zsh","-c",...]`（不带 `-l`）可完全避免。
- **HTTP 401**：确认请求头带的是 `~/.loopbox/auth.json` 里的最新 token
  （`Authorization: Bearer <token>` 也可以）。纯本地调试可用
  `LOOPBOX_NO_AUTH=1` 关掉鉴权——只用于本机，勿对外暴露。
