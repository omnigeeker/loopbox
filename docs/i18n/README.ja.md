# Loopbox

**macOS Apple Silicon（M1–M5）向けの、ローカルファーストで E2B プロトコル互換のサンドボックス。**

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox を使うと、AI エージェントハーネス（Codex CLI、Claude Code、DSH / DeepSeek Harness、
独自ランナー）が、あなた自身の Mac 上の本物のサンドボックス内で信頼できない作業を
実行できます。クラウドは不要です。日常のタスクは Seatbelt プロセスサンドボックスで
瞬時に起動し、マシン全体の隔離が必要な場合は実験的な Virtualization.framework
バックエンドにより、稼働中 VM の一時停止 / フォーク / 再開が可能です。

```bash
pip install -e .        # Python 3.10+、macOS 13+、Apple Silicon
loopbox doctor          # セルフチェック：アーキテクチャ、Seatbelt、APFS クローン、スモークテスト

SID=$(loopbox new)                        # サンドボックスを作成
loopbox exec $SID -- echo "hello sandbox" # サンドボックス内で実行
loopbox snapshot $SID --name v1           # APFS コピーオンライトのスナップショット
loopbox fork $SID --snapshot v1           # 同一の複製をフォーク
loopbox pause $SID && loopbox resume $SID # 一時停止 / 再開
loopbox harness codex                     # サンドボックス内で Codex CLI を起動
loopbox rm $SID --purge
```

## なぜ Loopbox か

ホスト型サンドボックス（E2B など）は優れていますが、コード・認証情報・レイテンシを
自分のマシンに留めたい場合があります。Loopbox は E2B の**利用プロトコル**
（SDK の形、HTTP API の形、スナップショットの意味論）を保ちながら、実行は完全にローカルです。

## 特徴

- **Apple Silicon ネイティブ** — M1 から M5 まで対応。macOS 13+ が必要
  （実験的 `vz` VM バックエンドは macOS 14+）。
- **2 つの隔離エンジン**
  - `seatbelt`（デフォルト）：`sandbox-exec` による macOS Seatbelt プロセスサンドボックス。
    瞬時起動、書き込み範囲の隔離。認証情報（`~/.ssh`、キーチェーン、ブラウザ Cookie、
    クラウド CLI 設定）は一切読み取り不可。サンドボックスごとのネットワークポリシー
    （`outbound` / `all` / `deny`）。
  - `vz`（実験的）：同梱の Swift `vzrunner` による Virtualization.framework マイクロ VM。
    マシン全体の状態により、真の一時停止 → スナップショット → フォーク → 再開が可能。
- **E2B プロトコル互換** — Python SDK は E2B SDK と同形（`Sandbox.create()`、
  `commands.run()`、`files.read/write()`、`pause()`、`fork()`、`kill()`）。
  HTTP API は E2B 形式のルートで、E2B と同じ `X-API-Key` トークン認証を使用。
- **完全なローカル CLI** — すべての機能が `loopbox ...` で利用可能。`--json` を全面サポート。
- **高性能スナップショット** — APFS コピーオンライトクローンにより、未変更データは O(1)。
- **エージェントハーネス対応** — `loopbox harness codex|claude|dsh` でエージェント CLI を
  サンドボックス内で起動。`loopbox loop` はヒューマン・イン・ザ・ループのゲート付き
  ループエンジンを提供。
- **ランタイム依存ゼロ** — Python 標準ライブラリのみ。

## サンドボックス内でエージェントハーネスを実行

```bash
loopbox harness codex                # Codex CLI を隔離実行、ワークスペースはサンドボックス
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness。-- 以降はそのまま渡されます
```

ハーネスプロセスは Seatbelt 配下で動作します：ツールチェーンの読み取りと
デフォルトポリシーでの外部ネットワークアクセスは可能ですが、**書き込み**は
サンドボックスのワークスペース内のみで、認証情報は一切読めません。別ターミナルから
`loopbox pause` でセッション全体を一時停止し、`loopbox snapshot` でスナップショット、
`loopbox fork` で探索ブランチを作成し、`loopbox resume` で再開できます。

## ループエンジンとヒューマンゲート

`loopbox loop` は「セルフチェック → 計画 → 実行 → 検証」のサイクルをゴールに対して
回し、状態を `.loopbox/` に永続化します。人間の判断（計画の承認、リスクのある操作、
曖昧な証拠）が必要な場合は `GATE.md` に質問を書き出し、推測せず人の回答を待ちます。
[LoopX](https://github.com/huangruiteng/loopx) と組み合わせると、ハーネス横断の
ゴール状態・クォータ・ハートビートが得られます。詳細は
[../loopx-integration.md](../loopx-integration.md)。

## セキュリティモデル（正直な版）

- `seatbelt` は強力な**プロセス**サンドボックスです：書き込み封じ込め、認証情報の遮断、
  オプションのネットワーク拒否。VM ではなく、カーネル攻撃は範囲外です——
  その場合は `vz` バックエンドを使ってください。
- HTTP サービスはデフォルトで `127.0.0.1` のみにバインドし、`LOOPBOX_NO_AUTH=1` を
  設定しない限り常にトークンを要求します（共有マシンでは絶対に設定しないでください）。
- サンドボックスの状態は `~/.loopbox` に保存されます（`LOOPBOX_HOME` で上書き可能）。

## 開発

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # ユニットテスト
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # 実際の Seatbelt 統合テストを含む
```

コードとコメントは英語です。ドキュメントは多言語で提供しています。

## ライセンス

Apache-2.0
