# Loopbox

本地优先、兼容 E2B 协议的沙箱，面向 Apple Silicon（M1–M5）上的 macOS。

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox 让 AI agent harness（Codex CLI、Claude Code、DSH / DeepSeek Harness，
或你自己的 runner）在你自己的 Mac 上的真实沙箱中执行不可信的工作——不依赖云端，
日常任务也没有 Linux 虚拟机的开销——同时保持 E2B 的*使用协议*：SDK 形态、
HTTP API 形态以及快照语义。默认后端使用 macOS Seatbelt 进程沙箱；实验性的
Virtualization.framework 后端额外提供整机虚拟机的暂停 / 快照 / 分叉 /
恢复能力。运行时仅使用 Python 标准库。

## 特性

- **两种隔离后端**
  - `seatbelt`（默认）：通过 `sandbox-exec` 实现的 macOS 进程沙箱。即时
    启动、限定写入范围的文件系统隔离、按沙箱配置的网络策略
    （`outbound` / `all` / `deny`）、基于 `SIGSTOP`/`SIGCONT` 的暂停/恢复，
    以及 APFS 写时复制 clonefile 快照。
  - `vz`（实验性）：通过内置 Swift 辅助程序 `vzrunner` 运行的
    Virtualization.framework ARM64 Linux 虚拟机。通过
    `saveMachineStateToURL` / `restoreMachineStateFromURL` 实现机器状态快照，
    通过 APFS bundle 克隆实现分叉。
- **兼容 E2B 的接口面**
  - 与 E2B SDK 同形的 Python SDK：`Sandbox.create()`、
    `sandbox.commands.run()`、`sandbox.files.read/write/list()`、
    `sandbox.pause()`、`sandbox.fork()`、`sandbox.kill()`。
  - 采用 E2B 风格路由的 REST API（`POST /sandboxes`，pause/resume/timeout），
    外加本地扩展（exec、files、snapshots、fork），使用 `X-API-Key` 令牌鉴权。
- **凭证隔离**——`~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.config/gh`、
  钥匙串（keychain）和浏览器 Cookie 存储在 seatbelt 沙箱内永远无法读取；
  无论规则顺序如何，deny 规则始终优先于 allow 规则。
- **Harness 集成**——`loopbox harness run <sandbox> codex|claude|dsh -- ...`
  将整个 agent CLI 运行在沙箱内，因此无论 harness 自身如何配置，
  loopbox 边界都始终生效。Loopbox 绝不会暗中注入绕过权限的 flag。
- **循环引擎**——`loopbox loop` 运行可持续的自检 → 自思考 →
  自迭代循环，带有人工门禁（human-in-the-loop gate），每一步之后都会
  checkpoint 到 JSON，被 kill 之后随时可以恢复。
- **零运行时依赖**——Python ≥ 3.10，仅使用标准库。

## 工作原理

```
┌────────────┐   ┌────────────┐   ┌────────────────────────┐
│ CLI        │   │ Python SDK │   │ HTTP service           │
│ loopbox …  │   │ from …     │   │ :31885, E2B-shaped     │
│            │   │ import …   │   │ (loopbox.service)      │
└─────┬──────┘   └─────┬──────┘   └───────────┬────────────┘
      │                │                      │
      └────────────────┴──────────┬───────────┘
                                  │ backend interface
                     ┌────────────┴─────────────┐
                     ▼                          ▼
             ┌──────────────┐          ┌────────────────────┐
             │ seatbelt     │          │ vz                 │
             │ sandbox-exec │          │ vzrunner (Swift) → │
             │ profile      │          │ Virtualization.fw  │
             └──────┬───────┘          └─────────┬──────────┘
                    └──────────────┬─────────────┘
                                   ▼
              ~/.loopbox/  (LOOPBOX_HOME override)
                sandboxes.json        fcntl-locked atomic registry
                sandboxes/<id>/       workspace/ + profile.sb
                snapshots/<id>/<sid>/ APFS clonefile snapshots
                loops/<loop_id>/      loop ledgers, GATE.md, gate.json
                vms/<id>/             vz VM bundles
                auth.json             service token (mode 0600)
```

CLI 直接驱动注册表（registry）和后端（从不经过 HTTP 层）；SDK 同样如此。
服务只是同一套注册表/后端之上的一层薄薄的 E2B 风格接口，
因此三种视角看到的沙箱状态完全一致。

## 环境要求

- Apple Silicon（arm64）上的 macOS 13+；`vz` 后端需要 macOS 14+。
- Python ≥ 3.10（`python3 --version`）。无第三方运行时依赖。
- APFS（Apple Silicon 上的标准文件系统），用于写时复制快照。
- 状态文件的 JSON schema 属于内部实现；`~/.loopbox` 可用
  `LOOPBOX_HOME` 迁移到其他位置。
- 仅当需要构建 `vz` 辅助程序时：Xcode Command Line Tools
  （`xcode-select --install`）。

运行 `loopbox doctor` 可以验证以上所有条件（架构、macOS 版本、
`sandbox-exec`、APFS 克隆支持、vzrunner 是否存在、seatbelt 冒烟测试）。

## 快速上手

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

创建并使用沙箱（CLI）：

```bash
SID=$(loopbox new)                           # seatbelt sandbox, network=outbound
loopbox exec $SID -- echo "hello sandbox"    # exit code mirrored to caller
loopbox exec $SID --cwd sub --timeout 30 -- make test
loopbox ls
loopbox pause $SID && loopbox resume $SID    # SIGSTOP / SIGCONT freeze
loopbox snapshot $SID --name v1              # APFS copy-on-write snapshot
loopbox snapshots $SID
loopbox fork $SID --snapshot v1              # branch off an identical twin
loopbox restore $SID v1                      # roll workspace back
loopbox rm $SID --purge                      # kill + delete files for real
```

或者使用 SDK：

```python
from loopbox import Sandbox

sbx = Sandbox.create(template="seatbelt", network="deny",
                     timeout=60, metadata={"job": "test"})
result = sbx.commands.run("echo hello > note.txt && cat note.txt")
assert result.ok
sbx.files.write("more.txt", "hi")
snap = sbx.snapshot(name="v1")
twin = sbx.fork(snapshot_id=snap)     # a new Sandbox, running
sbx.pause(); sbx.resume()
sbx.kill()                            # files kept; CLI rm --purge deletes
```

运行 HTTP 服务并用 curl 调用：

```bash
loopbox serve                              # 127.0.0.1:31885, X-API-Key auth
TOKEN=$(python3 -c "import json,os.path; \
  print(json.load(open(os.path.expanduser('~/.loopbox/auth.json')))['token'])")
curl -s -X POST http://127.0.0.1:31885/sandboxes \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"templateID": "seatbelt", "timeout": 600}'
curl -s -X POST http://127.0.0.1:31885/sandboxes/<sandboxID>/exec \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"command": "uname -m"}'             # {"stdout": "arm64\n", ...}
```

令牌在首次运行时生成，存放于 `~/.loopbox/auth.json`（权限 0600）；
`Authorization: Bearer <token>` 可作为等价的别名使用。`GET /health`
不需要鉴权。`LOOPBOX_NO_AUTH=1` 会关闭鉴权——仅限本地开发时使用。
后台 sweeper 负责强制执行沙箱超时。

## E2B 兼容性

Loopbox 实现了 E2B 控制 API 的一个子集，外加本地扩展。E2B 的
template ID 映射到 loopbox 的后端名称（`seatbelt`、`vz`）。

| Endpoint | Status |
|---|---|
| `POST /sandboxes` | 支持（`{"templateID", "timeout", "metadata", "envVars"}` → 201） |
| `GET /sandboxes` | 支持 |
| `GET /sandboxes/{id}` | 支持（E2B 风格的记录） |
| `DELETE /sandboxes/{id}` | 支持（kill + 注销，204） |
| `POST /sandboxes/{id}/timeout` | 支持（设置 `timeout_deadline`；由 sweeper 强制执行） |
| `POST /sandboxes/{id}/pause` | 支持（204） |
| `POST /sandboxes/{id}/resume` | 支持（204） |
| `POST /sandboxes/{id}/exec` | Loopbox 扩展——替代 envd 的 `POST /process`；字符串命令通过 `/bin/zsh -lc` 执行 |
| `GET /sandboxes/{id}/files?path=…` | Loopbox 扩展——列出工作区条目 |
| `PUT /sandboxes/{id}/files` | Loopbox 扩展——写入 `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Loopbox 扩展（201 `{"snapshotID"}`） |
| `GET /sandboxes/{id}/snapshots` | Loopbox 扩展 |
| `POST /sandboxes/{id}/fork` | Loopbox 扩展（`{"snapshotID"?}` → 201 `{"sandboxID"}`） |
| `GET /health` | Loopbox 扩展，无需鉴权 |
| envd process streaming / websockets | 未实现 |
| E2B 模板构建/管理 API | 未实现——模板即本地后端名称 |
| 仅托管版提供的接口（teams、metrics、auth0） | 超出范围——loopbox 仅限本地使用 |

错误响应为 E2B 风格：`{"code": <int>, "message": <str>}`。

## Harness 集成

harness 的运行时取决于它*在哪里执行*：在宿主机上启动 `codex` 或
`claude`，意味着把防护交给 harness 自带的沙箱。Loopbox 会把整个
harness CLI 启动在沙箱之内，因此无论 harness 如何配置，loopbox 的
Seatbelt profile（或虚拟机）都是最外层的边界：

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

`--` 之后的所有内容都会原样透传给 harness。Loopbox 刻意绝不注入
harness 原生的权限 flag：`--dangerously-skip-permissions` 或
`--sandbox danger-full-access` 是*调用者*自己的选择，只在沙箱内
有意义，绝不是 loopbox 替你添加的东西
（参见 [SECURITY.md](SECURITY.md)）。

## 带人工门禁的循环工程

`loopbox loop` 是一个可持续运行的循环引擎（概念来自
[LoopX](https://github.com/huangruiteng/loopx)；执行由 loopbox
沙箱隔离）。`~/.loopbox/loops/<loop_id>/` 下的台账（ledger）在每一步之后
都会 checkpoint——被 kill 的循环只需再次 `run` 即可恢复。

```bash
loopbox loop new --goal "create hello.txt containing 'hi' and verify it" --sandbox seatbelt
loopbox loop run <loop_id>                    # exits 3: blocked on the plan gate
loopbox loop approve <loop_id>                # answer the gate, then:
loopbox loop run <loop_id>                    # executes steps inside the sandbox
loopbox loop status <loop_id> [--json]
loopbox loop history <loop_id>
loopbox loop steer <loop_id> --note "run: make test"   # enqueue a command
loopbox loop reject <loop_id> --reason "wrong approach" # marks loop failed
```

- **自思考（Self-think）**：由 LLM harness CLI（PATH 上存在时使用
  `codex exec` 或 `claude -p`；可用 `LOOPBOX_HARNESS="cmd {prompt}"`、
  `LOOPBOX_HARNESS_TIMEOUT` 覆盖）提出下一步动作。思考步骤在宿主机上
  运行；*执行*发生在沙箱内。没有 harness 时，确定性的基于规则的
  回退方案会负责规划，并把判断升级给人工门禁。
- **自检（Self-check）**：提议的命令通过 SDK 在循环的沙箱内执行；
  可选的 `verify` 命令也必须以退出码 0 结束。
- **人工门禁（Human gates）**：`approve_plan`、`approve_step`
  （`rm -rf` 或 `git push` 之类的高风险命令总是会触发门禁，除非使用
  `--auto-approve`）、`on_failure`。可以通过 TTY 提示、从另一个终端
  使用 CLI、或直接编辑循环目录中的 `gate.json` / `GATE.md` 来应答。
- `run` 的退出码：`0` 目标达成，`1` 失败，`2` 因预算/中断而停止，
  `3` 阻塞在等待中的门禁上。

## 各后端的快照、分叉与恢复

| 操作 | `seatbelt` | `vz`（实验性） |
|---|---|---|
| `pause` / `resume` | 对已记录的进程组发送 `SIGSTOP`/`SIGCONT`——即时生效，内存保持存活 | `VZVirtualMachine.pause()` / `.resume()`——整机冻结 |
| `snapshot` | 对工作区做 APFS 写时复制克隆（`cp -c`）→ `snapshots/<id>/<name>/`；未变更数据的开销为 O(1)；**仅文件系统，不含进程/内存状态** | `saveMachineStateToURL` → `snapshots/<name>/machine-state`，外加 `disk.img` 的 APFS 克隆；**整机状态** |
| `fork` | 将工作区（或某个快照）克隆进一个拥有独立 profile 的新沙箱；子沙箱注册为运行中 | Python 侧对整个 VM bundle（磁盘 + 已保存状态）做 `cp -Rc` 克隆 |
| `restore` | 工作区被替换为快照克隆 | 快照磁盘被克隆回线上磁盘；下一次 `exec` 从恢复后的状态启动 |

`vz` 后端能捕获真正的机器状态，这是 Seatbelt 做不到的。它目前唯一
真正的缺口是客户机（guest）侧的控制：`exec` 是在一次全新启动中通过
`init=` 内核 shim 运行命令的（尚没有 guest 内的 vsock agent），因此
命令的 stdout 不会被捕获到 `ExecResult` 中，pause/restore 也无法让
运行中的 shell 跨越 `exec` 调用存续。关于 guest bundle 格式和当前
限制的详细信息，参见 [vzrunner/README.md](vzrunner/README.md)。

## 安全模型

简要版——完整的威胁模型请阅读 [SECURITY.md](SECURITY.md)：

- `seatbelt` 是强力的**进程级**沙箱：写入被限制在工作区（+ 临时
  scratch tmp）内，凭证存储禁止读取，按沙箱配置网络策略，信号作用域
  限定在沙箱内。它**不是虚拟机**——内核攻击面仍然存在，CPU/RAM 也
  不设上限。面对敌意代码，请使用 `vz`。
- `vz` 提供虚拟机级别的隔离，但属于实验性（限制见上文）。
- HTTP 服务默认绑定 `127.0.0.1`，除非你显式设置
  `LOOPBOX_NO_AUTH=1`，否则一律要求令牌（0600 权限的令牌文件）。
- Loopbox 自身绝不会向 harness CLI 传递
  `--dangerously-skip-permissions`；循环中的高风险命令总是会撞上
  人工门禁，除非你用 `--auto-approve` 主动关闭。

请通过
[GitHub security advisories](https://github.com/omnigeeker/loopbox/security/advisories/new)
报告漏洞。

## 仓库结构

```
loopbox/
├── pyproject.toml            # package metadata; deps: none (stdlib only)
├── README.md / LICENSE / SECURITY.md
├── GOAL.md                   # LoopX goal file used while developing this repo
├── loopbox/
│   ├── __init__.py           # public SDK exports: Sandbox, SandboxError
│   ├── cli.py                # `loopbox` entry point (subcommands, --json)
│   ├── sdk.py                # E2B-shaped Python SDK
│   ├── store.py              # fcntl-locked atomic JSON registry; ~/.loopbox layout
│   ├── auth.py               # X-API-Key token auth for the HTTP service
│   ├── service.py            # E2B-compatible REST API (stdlib ThreadingHTTPServer)
│   ├── server.py             # legacy pre-service HTTP façade — superseded, not importable
│   ├── harness.py            # `loopbox harness` adapters: codex, claude, dsh, custom
│   ├── backends/
│   │   ├── base.py           # Backend protocol + ExecResult
│   │   ├── seatbelt.py       # default backend: sandbox-exec, SIGSTOP, APFS clones
│   │   └── vz.py             # experimental backend: Virtualization.framework
│   └── loop/
│       ├── cli.py            # `loopbox loop` sub-CLI
│       ├── engine.py         # self-think / self-check / self-iterate driver
│       ├── gates.py          # human-in-the-loop gates (GATE.md / gate.json)
│       └── state.py          # durable per-loop JSON ledger
├── tests/                    # pytest suite (LOOPBOX_INTEGRATION=1 for live tests)
├── vzrunner/
│   ├── build.sh              # swiftc build, ad-hoc signs the vz entitlement
│   ├── Sources/vzrunner/main.swift
│   └── README.md             # helper CLI contract + guest bundle format
└── docs/
    ├── TUTORIAL.en.md        # guided tutorial (English)
    ├── TUTORIAL.zh-CN.md     # guided tutorial (简体中文)
    ├── ARCHITECTURE.md       # internals: modules, data model, state machines
    ├── loopx-integration.md  # LoopX concept mapping for the loop engine
    └── i18n/                 # translated READMEs
```

## 开发

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

CLI 退出码：`1` 运行时错误，`2` 用法错误，`124` 命令超时；
`loop run` 按上文文档使用 `0..3`。设置 `LOOPBOX_DEBUG=1` 可输出
traceback。

## 路线图 / 已知限制

- **`vz` 的 guest exec 是 `init=` shim**：每次 `exec` 都是一次全新启动，
  stdout 不会被捕获到 `ExecResult` 中；跨 `exec` 边界的机器状态恢复
  需要 guest 内的 vsock agent（属于未来工作；bundle/socket 的管线
  已经为此做好准备）。
- **没有资源配额**：两个后端都不限制沙箱内工作的 CPU、RAM 或
  wall time（存在按命令生效的 `--timeout`）。
- **`seatbelt` ≠ 虚拟机**：共享内核之上的进程沙箱；参见
  [SECURITY.md](SECURITY.md)。
- **`loopbox/server.py` 是遗留代码**：仅供参照保留，不可导入；
  请使用 `loopbox/service.py`（`loopbox serve`）。
- **打包发布**：PyPI 上尚未发布 `pip install loopbox`；
  请从本仓库安装。

## 许可证

Apache-2.0 —— 参见 [LICENSE](LICENSE)。© 2026 Loopbox Contributors。

## 文档

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) —— 引导式教程（English）
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) —— 引导式教程（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 内部实现深入解析
- [docs/loopx-integration.md](docs/loopx-integration.md) —— LoopX 概念映射
- [vzrunner/README.md](vzrunner/README.md) —— `vz` 辅助程序与 VM bundle 格式
- [SECURITY.md](SECURITY.md) —— 威胁模型与漏洞披露政策
