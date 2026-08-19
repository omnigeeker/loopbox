# Loopbox

為 macOS Apple Silicon（M1–M5）打造的本機優先、相容 E2B 協定的沙盒。

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox 讓 AI Agent harness（Codex CLI、Claude Code、DSH / DeepSeek Harness，
或你自己的 runner）在你自己的 Mac 上真正的沙盒中執行不可信的工作——
不依賴雲端，日常任務也不必承擔 Linux VM 的額外開銷——同時保留 E2B 的
*使用協定*：SDK 形態、HTTP API 形態與快照語義。預設後端採用 macOS
Seatbelt 處理程序沙盒；實驗性的 Virtualization.framework 後端則額外提供
整台 VM 的暫停 / 快照 / 分叉 / 恢復能力。執行期僅依賴 Python 標準函式庫。

## 功能特性

- **兩種隔離後端**
  - `seatbelt`（預設）：透過 `sandbox-exec` 實作的 macOS 處理程序沙盒。啟動瞬間
    完成、檔案系統的寫入範圍隔離、每個沙盒獨立的網路策略
    （`outbound` / `all` / `deny`）、基於 `SIGSTOP`/`SIGCONT` 的
    暫停/恢復，以及 APFS copy-on-write clonefile 快照。
  - `vz`（實驗性）：透過內附的 Swift 輔助程式 `vzrunner` 執行
    Virtualization.framework ARM64 Linux 虛擬機。透過
    `saveMachineStateToURL` / `restoreMachineStateFromURL` 取得機器狀態快照，
    透過 APFS bundle 複製實現分叉。
- **相容 E2B 的操作介面**
  - Python SDK 的形態與 E2B SDK 一致：`Sandbox.create()`、
    `sandbox.commands.run()`、`sandbox.files.read/write/list()`、
    `sandbox.pause()`、`sandbox.fork()`、`sandbox.kill()`。
  - REST API 採用 E2B 形態的路由（`POST /sandboxes`、pause/resume/timeout），
    外加本機擴充功能（exec、files、snapshots、fork），並以
    `X-API-Key` 權杖驗證保護。
- **憑證存取排除**——`~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.config/gh`、
  鑰匙圈與瀏覽器 Cookie 存放區，在 seatbelt 沙盒內永遠無法讀取；
  拒絕規則一律優先於允許規則，與規則的順序無關。
- **Harness 整合**——`loopbox harness run <sandbox> codex|claude|dsh -- ...`
  讓整個 Agent CLI 在沙盒內執行，因此無論 harness 本身如何設定，
  loopbox 的邊界都會生效。Loopbox 絕不悄悄注入略過權限的旗標。
- **循環引擎**——`loopbox loop` 執行可持久化的「自我檢查 → 自我思考 →
  自我迭代」循環，並設有人工門禁（human-in-the-loop gates）；每一步之後
  都會寫入 JSON 檢查點，即使被終止也能恢復續跑。
- **零執行期依賴**——Python ≥ 3.10，僅使用標準函式庫。

## 運作方式

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

CLI 直接驅動註冊表與後端（完全不經過 HTTP 層）；SDK 也是如此。
HTTP 服務只是同一套註冊表／後端之上的一層 E2B 形態薄介面，
因此沙盒的三種檢視永遠一致。

## 系統需求

- macOS 13+，Apple Silicon（arm64）；`vz` 後端需要 macOS 14+。
- Python ≥ 3.10（`python3 --version`）。無任何第三方執行期依賴。
- APFS（Apple Silicon 標配），用於 copy-on-write 快照。
- 狀態檔案的 JSON schema 屬於內部細節；`~/.loopbox` 可用
  `LOOPBOX_HOME` 搬移。
- 只有要建置 `vz` 輔助程式時才需要：Xcode Command Line Tools
  （`xcode-select --install`）。

執行 `loopbox doctor` 可驗證上述所有項目（架構、macOS 版本、
`sandbox-exec`、APFS clone 支援、vzrunner 是否存在、seatbelt 煙霧測試）。

## 快速上手

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

以 CLI 建立並使用沙盒：

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

或使用 SDK：

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

啟動 HTTP 服務，並用 curl 驅動它：

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

權杖會在首次執行時產生並寫入 `~/.loopbox/auth.json`（檔案權限 0600）；
`Authorization: Bearer <token>` 也被接受為別名。`GET /health` 無需驗證。
`LOOPBOX_NO_AUTH=1` 可關閉驗證——僅限本機開發使用。背景清掃器（sweeper）
會強制執行沙盒的逾時限制。

## E2B 相容性

Loopbox 實作了 E2B 控制 API 的子集，外加本機擴充功能。E2B 的 template ID
會對應到 loopbox 的後端名稱（`seatbelt`、`vz`）。

| 端點 | 狀態 |
|---|---|
| `POST /sandboxes` | 支援（`{"templateID", "timeout", "metadata", "envVars"}` → 201） |
| `GET /sandboxes` | 支援 |
| `GET /sandboxes/{id}` | 支援（E2B 形態的記錄） |
| `DELETE /sandboxes/{id}` | 支援（kill + 註銷，204） |
| `POST /sandboxes/{id}/timeout` | 支援（設定 `timeout_deadline`；由清掃器強制執行） |
| `POST /sandboxes/{id}/pause` | 支援（204） |
| `POST /sandboxes/{id}/resume` | 支援（204） |
| `POST /sandboxes/{id}/exec` | Loopbox 擴充——取代 envd 的 `POST /process`；字串指令經由 `/bin/zsh -lc` 執行 |
| `GET /sandboxes/{id}/files?path=…` | Loopbox 擴充——列出工作區項目 |
| `PUT /sandboxes/{id}/files` | Loopbox 擴充——寫入 `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Loopbox 擴充（201 `{"snapshotID"}`） |
| `GET /sandboxes/{id}/snapshots` | Loopbox 擴充 |
| `POST /sandboxes/{id}/fork` | Loopbox 擴充（`{"snapshotID"?}` → 201 `{"sandboxID"}`） |
| `GET /health` | Loopbox 擴充，無需驗證 |
| envd process streaming / websockets | 未實作 |
| E2B template build/manage APIs | 未實作——template 就是本機後端名稱 |
| Hosted-only surface (teams, metrics, auth0) | 不在範圍內——loopbox 僅限本機 |

錯誤格式與 E2B 相同：`{"code": <int>, "message": <str>}`。

## Harness 整合

harness 的執行環境取決於它*在哪裡執行*：在主機上啟動 `codex` 或
`claude`，防護就只能交給 harness 自己的沙盒。Loopbox 則是把整個
harness CLI 啟動在沙盒之內，因此無論 harness 如何設定，loopbox 的
Seatbelt profile（或虛擬機）都是最外層的邊界：

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

`--` 之後的所有內容都會原封不動地傳給 harness。Loopbox 刻意絕不注入
harness 原生的權限旗標：`--dangerously-skip-permissions` 或
`--sandbox danger-full-access` 是*呼叫者*的選擇，只在沙盒內部才有意義，
絕不是 loopbox 會替你添加的東西（詳見 [SECURITY.md](SECURITY.md)）。

## 具人工門禁的循環工程

`loopbox loop` 是一套可持久化的循環引擎（概念來自
[LoopX](https://github.com/huangruiteng/loopx)；執行由 loopbox 沙盒
隔離）。`~/.loopbox/loops/<loop_id>/` 下的帳本（ledger）在每一步之後
都會寫入檢查點——被終止的循環只要再執行一次 `run` 就能續跑。

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

- **自我思考**：由 LLM harness CLI（PATH 上存在時使用 `codex exec` 或
  `claude -p`；可用 `LOOPBOX_HARNESS="cmd {prompt}"`、
  `LOOPBOX_HARNESS_TIMEOUT` 覆寫）提出下一個行動。思考步驟在主機上執行；
  *實際執行*發生在沙盒內。沒有 harness 時，會由確定性的規則型後備方案
  負責規劃，並把需要判斷的事項上呈給門禁。
- **自我檢查**：提出的指令會透過 SDK 在該循環的沙盒內執行；若設定了
  選用的 `verify` 指令，它也必須以 0 結束。
- **人工門禁**：`approve_plan`、`approve_step`（`rm -rf` 或 `git push`
  這類高風險指令一律會觸發門禁，除非加上 `--auto-approve`）、`on_failure`。
  可透過 TTY 提示、從另一個終端使用 CLI，或直接編輯循環目錄中的
  `gate.json` / `GATE.md` 來回應。
- `run` 的結束碼：`0` 目標達成、`1` 失敗、`2` 因配額／中斷而停止、
  `3` 因有待處理的門禁而受阻。

## 各後端的快照、分叉與恢復

| 操作 | `seatbelt` | `vz`（實驗性） |
|---|---|---|
| `pause` / `resume` | 對記錄在案的處理程序群組送 `SIGSTOP`/`SIGCONT`——瞬間完成，記憶體保持存活 | `VZVirtualMachine.pause()` / `.resume()`——整台 VM 凍結 |
| `snapshot` | 對工作區做 APFS copy-on-write clone（`cp -c`）→ `snapshots/<id>/<name>/`；未變更資料的開銷為 O(1)；**僅檔案系統，不含處理程序／記憶體狀態** | `saveMachineStateToURL` → `snapshots/<name>/machine-state`，外加 `disk.img` 的 APFS clone；**整機狀態** |
| `fork` | 將工作區（或某份快照）clone 到一個擁有自己 profile 的新沙盒；子沙盒註冊為執行中 | 在 Python 端以 `cp -Rc` clone 整個 VM bundle（磁碟 + 已儲存狀態） |
| `restore` | 以快照 clone 取代工作區 | 將快照磁碟 clone 回線上磁碟之上；下一次 `exec` 會從恢復後的狀態開機 |

`vz` 後端能捕捉真正的機器狀態，這是 Seatbelt 做不到的。它目前真正的
缺口在客體端（guest）控制：`exec` 是在全新開機時以 `init=` 核心 shim
的方式執行指令（尚未有 guest 內的 vsock agent），因此指令的 stdout
不會被收進 `ExecResult`，且暫停／恢復也無法讓執行中的 shell 跨 `exec`
呼叫延續。guest bundle 格式與目前限制的完整細節請見
[vzrunner/README.md](vzrunner/README.md)。

## 安全模型

簡短版——完整的威脅模型請閱讀 [SECURITY.md](SECURITY.md)：

- `seatbelt` 是強大的**處理程序**沙盒：寫入限制在工作區（外加暫存
  tmp）範圍內、憑證存放區拒絕讀取、每沙盒獨立的網路策略、訊號作用域
  限於沙盒。它**不是虛擬機**——核心攻擊面仍然存在，CPU／記憶體也不設
  上限。面對敵意程式請使用 `vz`。
- `vz` 提供 VM 等級的隔離，但仍屬實驗性（限制見上文）。
- HTTP 服務預設只繫結 `127.0.0.1`，除非你明確設定 `LOOPBOX_NO_AUTH=1`，
  否則一律需要權杖（權杖檔案權限 0600）。
- Loopbox 自身絕不會把 `--dangerously-skip-permissions` 傳給 harness
  CLI；循環中的高風險指令一律會進人工門禁，除非你用 `--auto-approve`
  選擇退出。

請透過 [GitHub Security Advisories](https://github.com/omnigeeker/loopbox/security/advisories/new)
回報漏洞。

## 儲存庫結構

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

## 開發

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

CLI 結束碼：`1` 執行期錯誤、`2` 用法錯誤、`124` 指令逾時；
`loop run` 的 `0..3` 如上文所述。設定 `LOOPBOX_DEBUG=1` 可輸出
traceback。

## 路線圖／已知限制

- **`vz` 的 guest exec 是 `init=` shim**：每次 `exec` 都是一次全新開機，
  stdout 不會被收進 `ExecResult`；跨 exec 邊界的機器狀態恢復需要
  guest 內的 vsock agent（屬於未來工作；bundle／socket 的管線已具備
  支援條件）。
- **無資源配額**：兩種後端都不限制沙盒工作的 CPU、記憶體或實際執行
  時間（有單一指令層級的 `--timeout`）。
- **`seatbelt` ≠ VM**：共享核心上的處理程序沙盒；詳見
  [SECURITY.md](SECURITY.md)。
- **`loopbox/server.py` 是舊版遺留**：僅保留供參考，且無法 import；
  請使用 `loopbox/service.py`（`loopbox serve`）。
- **打包發布**：PyPI 上的 `pip install loopbox` 尚未發布；請從本儲存庫
  安裝。

## 授權條款

Apache-2.0——詳見 [LICENSE](LICENSE)。© 2026 Loopbox Contributors。

## 文件

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md)——引導教學（英文）
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md)——引導教學（簡體中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)——內部實作深入解析
- [docs/loopx-integration.md](docs/loopx-integration.md)——LoopX 概念對應
- [vzrunner/README.md](vzrunner/README.md)——`vz` 輔助程式與 VM bundle 格式
- [SECURITY.md](SECURITY.md)——威脅模型與漏洞揭露政策
