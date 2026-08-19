# Loopbox

ローカルファーストで E2B プロトコル互換のサンドボックス — Apple Silicon（M1–M5）上の macOS 向け。

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox は、AI エージェントハーネス（Codex CLI、Claude Code、DSH / DeepSeek Harness、
あるいは独自のランナー）が、信頼できない処理をあなた自身の Mac 上の本物の
サンドボックス内で実行できるようにする。クラウドは不要で、日常のタスクに Linux VM の
オーバーヘッドもかからない。その一方で E2B の*利用プロトコル*——SDK の形、
HTTP API の形、スナップショットのセマンティクス——は維持される。デフォルトの
バックエンドは macOS Seatbelt プロセスサンドボックスを使い、実験的な
Virtualization.framework バックエンドは VM 全体の一時停止／スナップショット／
フォーク／再開を追加する。ランタイムは Python 標準ライブラリのみ。

## 特徴

- **2 つの隔離バックエンド**
  - `seatbelt`（デフォルト）：`sandbox-exec` による macOS プロセスサンドボックス。
    即時起動、書き込み範囲を限定したファイルシステム隔離、サンドボックスごとの
    ネットワークポリシー（`outbound` / `all` / `deny`）、`SIGSTOP`/`SIGCONT`
    ベースの一時停止／再開、APFS コピーオンライトの clonefile スナップショット。
  - `vz`（実験的）：同梱の Swift ヘルパー `vzrunner` による
    Virtualization.framework ARM64 Linux VM。`saveMachineStateToURL` /
    `restoreMachineStateFromURL` によるマシン状態のスナップショット、
    APFS バンドルクローンによるフォーク。
- **E2B 互換のインターフェース**
  - E2B SDK と同形の Python SDK：`Sandbox.create()`、
    `sandbox.commands.run()`、`sandbox.files.read/write/list()`、
    `sandbox.pause()`、`sandbox.fork()`、`sandbox.kill()`。
  - E2B 形式のルート（`POST /sandboxes`、pause/resume/timeout）にローカル拡張
    （exec、files、snapshots、fork）を加えた REST API。`X-API-Key` トークン認証で保護。
- **認証情報の隔離**——`~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.config/gh`、
  キーチェーン、ブラウザの Cookie ストアは、seatbelt サンドボックス内からは
  決して読み取れない。ルールの順序に関係なく、deny ルールは allow ルールに優先する。
- **ハーネス統合**——`loopbox harness run <sandbox> codex|claude|dsh -- ...`
  はエージェント CLI 全体をサンドボックス内で実行するため、ハーネスがどのように
  設定されていても loopbox の境界が適用される。Loopbox が権限バイパスフラグを
  暗黙に注入することは決してない。
- **ループエンジン**——`loopbox loop` は、ヒューマン・イン・ザ・ループのゲートを
  備えた、永続化されたセルフチェック → セルフシンク → セルフイテレートのサイクルを
  実行する。各ステップの後に JSON へチェックポイントされ、kill 後も再開できる。
- **ランタイム依存ゼロ**——Python ≥ 3.10、標準ライブラリのみ。

## 動作の仕組み

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

CLI はレジストリとバックエンドを直接駆動し（HTTP 層は一切経由しない）、SDK も同様
である。サービスは同じレジストリ／バックエンドの上に載った薄い E2B 形式の
インターフェースなので、サンドボックスの 3 つのビューは常に一致する。

## 動作要件

- Apple Silicon（arm64）上の macOS 13+。`vz` バックエンドには macOS 14+ が必要。
- Python ≥ 3.10（`python3 --version`）。サードパーティのランタイム依存はなし。
- コピーオンライトスナップショットには APFS（Apple Silicon では標準）。
- 状態ファイルの JSON スキーマは内部仕様。`~/.loopbox` は `LOOPBOX_HOME` で移動できる。
- `vz` ヘルパーをビルドする場合のみ：Xcode Command Line Tools
  （`xcode-select --install`）。

上記のすべて（アーキテクチャ、macOS バージョン、`sandbox-exec`、APFS クローン対応、
vzrunner の有無、seatbelt のスモークテスト）は `loopbox doctor` で確認できる。

## クイックスタート

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

サンドボックスを作成して使う（CLI）：

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

SDK を使う場合：

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

HTTP サービスを起動し、curl で操作する：

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

トークンは初回実行時に `~/.loopbox/auth.json`（モード 0600）に生成される。
`Authorization: Bearer <token>` もエイリアスとして受け付ける。`GET /health` は
認証不要。`LOOPBOX_NO_AUTH=1` は認証を無効にする——ローカル開発にのみ使うこと。
バックグラウンドのスイーパーがサンドボックスのタイムアウトを適用する。

## E2B 互換性

Loopbox は E2B コントロール API のサブセットに加え、ローカル拡張をサポートする。
E2B のテンプレート ID は loopbox のバックエンド名（`seatbelt`、`vz`）に対応する。

| エンドポイント | ステータス |
|---|---|
| `POST /sandboxes` | 対応（`{"templateID", "timeout", "metadata", "envVars"}` → 201） |
| `GET /sandboxes` | 対応 |
| `GET /sandboxes/{id}` | 対応（E2B 形式のレコード） |
| `DELETE /sandboxes/{id}` | 対応（kill + 登録解除、204） |
| `POST /sandboxes/{id}/timeout` | 対応（`timeout_deadline` を設定。スイーパーが適用） |
| `POST /sandboxes/{id}/pause` | 対応（204） |
| `POST /sandboxes/{id}/resume` | 対応（204） |
| `POST /sandboxes/{id}/exec` | Loopbox 拡張 — envd の `POST /process` を置き換える。文字列コマンドは `/bin/zsh -lc` 経由で実行 |
| `GET /sandboxes/{id}/files?path=…` | Loopbox 拡張 — ワークスペースのエントリを一覧表示 |
| `PUT /sandboxes/{id}/files` | Loopbox 拡張 — `{"path", "content"}` を書き込む |
| `POST /sandboxes/{id}/snapshots` | Loopbox 拡張（201 で `{"snapshotID"}` を返す） |
| `GET /sandboxes/{id}/snapshots` | Loopbox 拡張 |
| `POST /sandboxes/{id}/fork` | Loopbox 拡張（`{"snapshotID"?}` → 201 で `{"sandboxID"}`） |
| `GET /health` | Loopbox 拡張。認証不要 |
| envd process streaming / websockets | 未実装 |
| E2B template build/manage APIs | 未実装 — テンプレートはローカルのバックエンド名 |
| Hosted-only surface (teams, metrics, auth0) | 対象外 — loopbox はローカル専用 |

エラーは E2B 形式：`{"code": <int>, "message": <str>}`。

## ハーネス統合

ハーネスは*どこで実行されるか*によってその実行環境が決まる。ホスト上で `codex` や
`claude` を起動すると、保護はハーネス自身のサンドボックス任せになる。Loopbox は
ハーネス CLI 全体をサンドボックス内で起動するため、ハーネスの設定に関係なく、
loopbox の Seatbelt プロファイル（または VM）が外側の境界となる：

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

`--` 以降はすべてハーネスにそのまま渡される。Loopbox はハーネス固有の権限フラグを
意図的に一切注入しない：`--dangerously-skip-permissions` や
`--sandbox danger-full-access` は*呼び出し側*の選択であり、サンドボックス内でのみ
意味を持つもので、loopbox が自動で追加するものではない
（[SECURITY.md](SECURITY.md) を参照）。

## ヒューマンゲート付きのループエンジニアリング

`loopbox loop` は永続化されたループエンジンである（概念は
[LoopX](https://github.com/huangruiteng/loopx) より。実行は loopbox サンドボックスで
隔離される）。`~/.loopbox/loops/<loop_id>/` 以下のレジャーは各ステップの後に
チェックポイントされる——kill されたループも、再度 `run` するだけで再開できる。

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

- **セルフシンク**：LLM ハーネス CLI（PATH 上にあれば `codex exec` または
  `claude -p`。`LOOPBOX_HARNESS="cmd {prompt}"`、`LOOPBOX_HARNESS_TIMEOUT` で
  上書き可能）が次のアクションを提案する。シンクのステップはホスト上で実行され、
  *実行*はサンドボックス内で行われる。ハーネスがない場合は、決定論的なルールベースの
  フォールバックが計画を立て、判断をゲートにエスカレートする。
- **セルフチェック**：提案されたコマンドは SDK 経由でループのサンドボックス内で
  実行される。オプションの `verify` コマンドも終了コード 0 でなければならない。
- **ヒューマンゲート**：`approve_plan`、`approve_step`（`rm -rf` や `git push` の
  ようなリスクの高いコマンドは `--auto-approve` を付けない限り常にゲートにかかる）、
  `on_failure`。回答は TTY プロンプト、別ターミナルからの CLI、またはループ
  ディレクトリ内の `gate.json` / `GATE.md` の編集で行う。
- `run` の終了コード：`0` ゴール達成、`1` 失敗、`2` 予算／割り込みによる停止、
  `3` 保留中のゲートでブロック。

## バックエンド別のスナップショット／フォーク／再開

| 操作 | `seatbelt` | `vz`（実験的） |
|---|---|---|
| `pause` / `resume` | 記録されたプロセスグループへの `SIGSTOP`/`SIGCONT` — 即時で、メモリは生きたまま保持 | `VZVirtualMachine.pause()` / `.resume()` — VM 全体を凍結 |
| `snapshot` | ワークスペースの APFS コピーオンライトクローン（`cp -c`）→ `snapshots/<id>/<name>/`。未変更データは O(1)。**ファイルシステムのみ。プロセス／メモリの状態は含まない** | `saveMachineStateToURL` → `snapshots/<name>/machine-state` に加え、`disk.img` の APFS クローン。**マシン全体の状態** |
| `fork` | ワークスペース（またはスナップショット）を、独自のプロファイルを持つ新しいサンドボックスへクローン。子は実行中として登録される | Python 側で VM バンドル全体（ディスク + 保存済み状態）を `cp -Rc` でクローン |
| `restore` | ワークスペースがスナップショットのクローンで置き換えられる | スナップショットのディスクを稼働中のディスクにクローンで戻す。次の `exec` は復元された状態から起動 |

`vz` バックエンドは Seatbelt では得られない真のマシン状態を捕捉する。現時点での唯一の
実質的な欠落はゲスト側の制御だ：`exec` は新規ブートごとにコマンドを `init=` カーネル
シムとして実行する（ゲスト内の vsock エージェントはまだない）ため、コマンドの stdout
は `ExecResult` に捕捉されず、pause/restore で実行中のシェルを `exec` 呼び出しを
またいで持ち越すこともできない。ゲストバンドル形式と現在の制限の詳細は
[vzrunner/README.md](vzrunner/README.md) を参照。

## セキュリティモデル

要約版——完全な脅威モデルは [SECURITY.md](SECURITY.md) を読むこと：

- `seatbelt` は強力な**プロセス**サンドボックスである：ワークスペースへの書き込み
  封じ込め（+ スクラッチ tmp）、認証情報ストアの読み取り拒否、サンドボックスごとの
  ネットワークポリシー、サンドボックスにスコープされたシグナル。ただし**VM では
  ない**——カーネルの攻撃対象領域は残り、CPU／RAM も制限されない。敵対的な
  コードには `vz` を使うこと。
- `vz` は VM グレードの隔離を提供するが、実験的である（上記の制限を参照）。
- HTTP サービスはデフォルトで `127.0.0.1` にバインドし、明示的に
  `LOOPBOX_NO_AUTH=1` を設定しない限りトークン（0600 のトークンファイル）を要求する。
- Loopbox はハーネス CLI に `--dangerously-skip-permissions` を自分から渡すことは
  決してない。ループ内のリスクの高いコマンドは、`--auto-approve` でオプトアウト
  しない限り、常にヒューマンゲートにかかる。

脆弱性は
[GitHub セキュリティアドバイザリ](https://github.com/omnigeeker/loopbox/security/advisories/new)
から報告されたい。

## リポジトリ構成

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

## 開発

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

CLI の終了コード：`1` ランタイムエラー、`2` 使用法エラー、`124` コマンド
タイムアウト。`loop run` は上記の通り `0..3` を使う。トレースバックには
`LOOPBOX_DEBUG=1` を設定する。

## ロードマップ／既知の制限

- **`vz` のゲスト exec は `init=` シム**：各 `exec` は毎回新規ブートで、stdout は
  `ExecResult` に捕捉されない。exec の境界をまたいだマシン状態のレジュームには
  ゲスト内の vsock エージェントが必要（将来の作業。バンドル／ソケットの配管は
  すでに対応済み）。
- **リソースクォータなし**：どちらのバックエンドも、サンドボックス内の処理の
  CPU、RAM、経過時間を制限しない（コマンドごとの `--timeout` はある）。
- **`seatbelt` ≠ VM**：共有カーネル上のプロセスサンドボックス。
  [SECURITY.md](SECURITY.md) を参照。
- **`loopbox/server.py` はレガシー**：参照用に残されているだけで、インポートは
  できない。`loopbox/service.py`（`loopbox serve`）を使うこと。
- **パッケージング**：PyPI からの `pip install loopbox` はまだ公開されていない。
  リポジトリからインストールすること。

## ライセンス

Apache-2.0——[LICENSE](LICENSE) を参照。© 2026 Loopbox Contributors.

## ドキュメント

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) — ガイド付きチュートリアル（英語）
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) — ガイド付きチュートリアル（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 内部構造の掘り下げ
- [docs/loopx-integration.md](docs/loopx-integration.md) — LoopX の概念マッピング
- [vzrunner/README.md](vzrunner/README.md) — `vz` ヘルパーと VM バンドル形式
- [SECURITY.md](SECURITY.md) — 脅威モデルと開示ポリシー
