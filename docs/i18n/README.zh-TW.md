# Loopbox

**為 macOS Apple 晶片（M1–M5）打造的本機優先、相容 E2B 協定的沙箱。**

[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox 讓 AI Agent harness（Codex CLI、Claude Code、DSH / DeepSeek Harness 以及你自建的
runner）在你自己的 Mac 上真正的沙箱中執行不可信的工作——不依賴雲端。日常任務使用
Seatbelt 行程沙箱實現秒級啟動；需要整機級隔離時，可啟用實驗性的
Virtualization.framework 後端，獲得執行中虛擬機的暫停 / 分叉 / 恢復能力。

```bash
pip install -e .        # Python 3.10+，macOS 13+，Apple Silicon
loopbox doctor          # 自我檢查：架構、Seatbelt、APFS 複製、煙霧測試

SID=$(loopbox new)                        # 建立沙箱
loopbox exec $SID -- echo "hello sandbox" # 在沙箱內執行
loopbox snapshot $SID --name v1           # APFS 寫時複製快照
loopbox fork $SID --snapshot v1           # 分叉出一個完全相同的副本
loopbox pause $SID && loopbox resume $SID # 暫停 / 恢復
loopbox harness codex                     # 在沙箱中啟動 Codex CLI
loopbox rm $SID --purge
```

## 為什麼選擇 Loopbox

託管沙箱（如 E2B）很優秀，但有時程式碼、憑證與延遲預算必須留在你自己的機器上。
Loopbox 保留 E2B 的**使用協定**（SDK 形態、HTTP API 形態、快照語義），執行完全在本機。

## 特性

- **Apple Silicon 原生** —— 支援 M1 至 M5，需 macOS 13+（實驗性 `vz` 虛擬機後端需 macOS 14+）。
- **兩種隔離引擎**
  - `seatbelt`（預設）：基於 `sandbox-exec` 的 macOS Seatbelt 行程沙箱。秒級啟動、
    寫入範圍隔離；憑證目錄（`~/.ssh`、鑰匙圈、瀏覽器 Cookie、雲端 CLI 設定）永不可讀；
    每個沙箱獨立的網路策略（`outbound` / `all` / `deny`）。
  - `vz`（實驗性）：透過內建 Swift `vzrunner` 使用 Virtualization.framework 微型虛擬機。
    整機狀態帶來真正的暫停 → 快照 → 分叉 → 恢復。
- **相容 E2B 協定** —— Python SDK 與 E2B SDK 同形（`Sandbox.create()`、
  `commands.run()`、`files.read/write()`、`pause()`、`fork()`、`kill()`）；
  HTTP API 採用 E2B 風格路由，並使用與 E2B 相同的 `X-API-Key` 權杖驗證。
- **完整本機 CLI** —— 所有功能皆可透過 `loopbox ...` 使用，全面支援 `--json` 便於腳本化。
- **高效能快照** —— APFS 寫時複製克隆，未變更資料的開銷為 O(1)。
- **為 Agent harness 而生** —— `loopbox harness codex|claude|dsh` 在沙箱內啟動 Agent CLI；
  `loopbox loop` 提供含人工門禁（Human-in-the-Loop）的循環引擎。
- **零執行期依賴** —— 僅使用 Python 標準函式庫。

## 在沙箱中執行 Agent harness

```bash
loopbox harness codex                # Codex CLI，隔離執行，工作區即沙箱
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness，-- 後為透傳參數
```

harness 行程執行於 Seatbelt 之下：可讀取工具鏈、依預設策略存取外網，但只能**寫入**
沙箱工作區，且永遠無法讀取你的憑證。你可以在另一個終端 `loopbox pause` 整個工作階段、
`loopbox snapshot` 快照、`loopbox fork` 出探索分支、再 `loopbox resume`——人始終在環中。

## 循環引擎與人工門禁

`loopbox loop` 圍繞一個目標執行「自檢 → 規劃 → 執行 → 驗證」循環，狀態持久化於
`.loopbox/`。當循環需要人類判斷（批准計畫、高風險操作、證據不足）時，它會寫出
`GATE.md` 問題並等待人工回答，而不是自行猜測。搭配
[LoopX](https://github.com/huangruiteng/loopx) 可獲得跨 harness 的目標狀態、配額與心跳，
詳見 [../loopx-integration.md](../loopx-integration.md)。

## 安全模型（如實說明）

- `seatbelt` 是強力的**行程級**沙箱：寫入限制、憑證隔離、可選網路封禁。它不是虛擬機；
  核心級攻擊不在防護範圍內——有此需求請使用 `vz` 後端。
- HTTP 服務預設僅綁定 `127.0.0.1`，除設定 `LOOPBOX_NO_AUTH=1` 外一律要求權杖
  （請勿在共用機器上這樣做）。
- 沙箱狀態存放於 `~/.loopbox`（可用 `LOOPBOX_HOME` 覆寫）。

## 開發

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # 單元測試
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # 含真實 Seatbelt 整合測試
```

程式碼與註解使用英文；文件提供多語言版本。

## 授權

Apache-2.0
