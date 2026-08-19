# Loopbox

Local-First-, E2B-protokollkompatible Sandboxes für macOS auf Apple Silicon (M1–M5).

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox lässt KI-Agenten-Harnesses (Codex CLI, Claude Code, DSH / DeepSeek Harness
oder eigene Runner) nicht vertrauenswürdige Arbeit in einer echten Sandbox auf
deinem eigenen Mac ausführen — ohne Cloud, ohne Linux-VM-Overhead für alltägliche
Aufgaben — und behält dabei das E2B-*Nutzungsprotokoll* bei: SDK-Form,
HTTP-API-Form und Snapshot-Semantik. Das Standard-Backend nutzt die
macOS-Seatbelt-Prozess-Sandbox; ein experimentelles
Virtualization.framework-Backend ergänzt Pausieren / Snapshot / Fork /
Fortsetzen auf Ebene der gesamten VM. Zur Laufzeit wird ausschließlich die
Python-Standardbibliothek verwendet.

## Funktionen

- **Zwei Isolations-Backends**
  - `seatbelt` (Standard): macOS-Prozess-Sandbox über `sandbox-exec`. Sofortiger
    Start, schreibseitig begrenzte Dateisystem-Isolation, Netzwerkrichtlinie pro
    Sandbox (`outbound` / `all` / `deny`), Pause/Fortsetzen auf Basis von
    `SIGSTOP`/`SIGCONT`, APFS-Copy-on-Write-Clonefile-Snapshots.
  - `vz` (experimentell): Virtualization.framework-ARM64-Linux-VMs über den
    mitgelieferten Swift-Helfer `vzrunner`. Maschinenzustands-Snapshots über
    `saveMachineStateToURL` / `restoreMachineStateFromURL`, Fork über
    APFS-Bundle-Klon.
- **E2B-kompatible Oberfläche**
  - Python-SDK wie das E2B-SDK geformt: `Sandbox.create()`,
    `sandbox.commands.run()`, `sandbox.files.read/write/list()`,
    `sandbox.pause()`, `sandbox.fork()`, `sandbox.kill()`.
  - REST-API mit E2B-förmigen Routen (`POST /sandboxes`, pause/resume/timeout)
    plus lokale Erweiterungen (exec, files, snapshots, fork), abgesichert mit
    `X-API-Key`-Token-Authentifizierung.
- **Credential-Aussparungen** — `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`,
  Schlüsselbünde und Browser-Cookie-Speicher sind innerhalb einer
  Seatbelt-Sandbox niemals lesbar; Deny-Regeln gewinnen unabhängig von der
  Reihenfolge immer gegenüber Allow-Regeln.
- **Harness-Integration** — `loopbox harness run <sandbox> codex|claude|dsh -- ...`
  führt die gesamte Agenten-CLI innerhalb einer Sandbox aus, sodass die
  Loopbox-Grenze gilt, egal wie der Harness sich selbst konfiguriert. Loopbox
  injiziert niemals stillschweigend Flags zur Umgehung von Berechtigungen.
- **Loop-Engine** — `loopbox loop` führt einen dauerhaft persistierten
  Self-Check- → Self-Think- → Self-Iterate-Zyklus mit
  Human-in-the-Loop-Gates aus, der nach jedem Schritt nach JSON checkpointet
  wird und nach jedem Kill fortgesetzt werden kann.
- **Keine Laufzeitabhängigkeiten** — Python ≥ 3.10, nur Standardbibliothek.

## Funktionsweise

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

Die CLI steuert die Registry und die Backends direkt an (sie geht niemals über
die HTTP-Schicht); das SDK macht dasselbe. Der Dienst ist eine dünne,
E2B-förmige Oberfläche über derselben Registry und denselben Backends, sodass
alle drei Ansichten einer Sandbox übereinstimmen.

## Voraussetzungen

- macOS 13+ auf Apple Silicon (arm64); das `vz`-Backend benötigt macOS 14+.
- Python ≥ 3.10 (`python3 --version`). Keine Laufzeitabhängigkeiten von
  Drittanbietern.
- APFS (Standard auf Apple Silicon) für Copy-on-Write-Snapshots.
- Das JSON-Schema der Zustandsdateien ist intern; `~/.loopbox` kann mit
  `LOOPBOX_HOME` verschoben werden.
- Nur zum Bauen des `vz`-Helfers: Xcode Command Line Tools
  (`xcode-select --install`).

Führe `loopbox doctor` aus, um all das zu prüfen (Architektur, macOS-Version,
`sandbox-exec`, APFS-Klon-Unterstützung, Vorhandensein von vzrunner,
Seatbelt-Smoke-Test).

## Schnellstart

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

Sandbox erstellen und verwenden (CLI):

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

Oder das SDK:

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

Den HTTP-Dienst starten und mit curl ansprechen:

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

Das Token wird beim ersten Start in `~/.loopbox/auth.json` erzeugt (Modus
0600); `Authorization: Bearer <token>` wird als Alias akzeptiert. `GET /health`
ist unauthentifiziert. `LOOPBOX_NO_AUTH=1` deaktiviert die Authentifizierung —
nur für lokale Entwicklung verwenden. Ein Sweeper im Hintergrund setzt
Sandbox-Timeouts durch.

## E2B-Kompatibilität

Loopbox spricht eine Teilmenge der E2B-Control-API plus lokale Erweiterungen.
E2B-Template-IDs werden auf Loopbox-Backend-Namen abgebildet (`seatbelt`,
`vz`).

| Endpunkt | Status |
|---|---|
| `POST /sandboxes` | Unterstützt (`{"templateID", "timeout", "metadata", "envVars"}` → 201) |
| `GET /sandboxes` | Unterstützt |
| `GET /sandboxes/{id}` | Unterstützt (E2B-förmiger Datensatz) |
| `DELETE /sandboxes/{id}` | Unterstützt (Kill + Entfernen aus der Registry, 204) |
| `POST /sandboxes/{id}/timeout` | Unterstützt (setzt `timeout_deadline`; Durchsetzung durch den Sweeper) |
| `POST /sandboxes/{id}/pause` | Unterstützt (204) |
| `POST /sandboxes/{id}/resume` | Unterstützt (204) |
| `POST /sandboxes/{id}/exec` | Loopbox-Erweiterung — ersetzt envd `POST /process`; String-Kommandos laufen über `/bin/zsh -lc` |
| `GET /sandboxes/{id}/files?path=…` | Loopbox-Erweiterung — listet die Workspace-Einträge |
| `PUT /sandboxes/{id}/files` | Loopbox-Erweiterung — schreibt `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Loopbox-Erweiterung (201 `{"snapshotID"}`) |
| `GET /sandboxes/{id}/snapshots` | Loopbox-Erweiterung |
| `POST /sandboxes/{id}/fork` | Loopbox-Erweiterung (`{"snapshotID"?}` → 201 `{"sandboxID"}`) |
| `GET /health` | Loopbox-Erweiterung, unauthentifiziert |
| envd-Prozess-Streaming / Websockets | Nicht implementiert |
| E2B-APIs zum Bauen/Verwalten von Templates | Nicht implementiert — Templates sind lokale Backend-Namen |
| Nur gehostete Oberfläche (Teams, Metriken, auth0) | Außerhalb des Umfangs — Loopbox ist rein lokal |

Fehler sind E2B-förmig: `{"code": <int>, "message": <str>}`.

## Harness-Integration

Ein Harness wählt seine Laufzeitumgebung danach, *wo er ausgeführt wird*: Wer
`codex` oder `claude` auf dem Host startet, überlässt den Schutz der eigenen
Sandbox des Harness. Loopbox startet die gesamte Harness-CLI innerhalb einer
Sandbox, sodass das Seatbelt-Profil (oder die VM) von Loopbox unabhängig von
der Harness-Konfiguration die äußere Begrenzung ist:

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

Alles nach `--` wird unverändert an den Harness weitergereicht. Loopbox
injiziert bewusst niemals harness-eigene Berechtigungs-Flags:
`--dangerously-skip-permissions` oder `--sandbox danger-full-access` sind die
Entscheidung des *Aufrufers*, nur innerhalb der Sandbox sinnvoll und niemals
etwas, das Loopbox für dich hinzufügt (siehe [SECURITY.md](SECURITY.md)).

## Loop-Engineering mit menschlichen Gates

`loopbox loop` ist eine dauerhaft persistierte Loop-Engine (Konzepte aus
[LoopX](https://github.com/huangruiteng/loopx); Ausführung isoliert durch
Loopbox-Sandboxes). Das Ledger unter `~/.loopbox/loops/<loop_id>/` wird nach
jedem Schritt checkpointet — eine abgebrochene Loop wird einfach mit `run`
fortgesetzt.

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

- **Self-Think**: Eine LLM-Harness-CLI (`codex exec` oder `claude -p`, sofern
  im PATH verfügbar; überschreibbar mit `LOOPBOX_HARNESS="cmd {prompt}"`,
  `LOOPBOX_HARNESS_TIMEOUT`) schlägt die nächste Aktion vor. Der Denkschritt
  läuft auf dem Host; die *Ausführung* erfolgt innerhalb der Sandbox. Ohne
  Harness plant ein deterministisches, regelbasiertes Fallback und eskaliert
  Urteilsfragen an die Gates.
- **Self-Check**: Das vorgeschlagene Kommando läuft über das SDK innerhalb der
  Sandbox der Loop; ein optionales `verify`-Kommando muss ebenfalls mit 0
  enden.
- **Menschliche Gates**: `approve_plan`, `approve_step` (riskante Kommandos
  wie `rm -rf` oder `git push` landen immer an einem Gate, sofern nicht
  `--auto-approve` gesetzt ist), `on_failure`. Beantwortung über den
  TTY-Prompt, die CLI aus einem anderen Terminal oder durch Bearbeiten von
  `gate.json` / `GATE.md` im Loop-Verzeichnis.
- `run`-Exit-Codes: `0` Ziel erreicht, `1` fehlgeschlagen, `2` durch
  Budget/Unterbrechung gestoppt, `3` an einem ausstehenden Gate blockiert.

## Snapshots, Fork und Resume pro Backend

| Operation | `seatbelt` | `vz` (experimentell) |
|---|---|---|
| `pause` / `resume` | `SIGSTOP`/`SIGCONT` auf aufgezeichnete Prozessgruppen — sofortig, Speicher bleibt erhalten | `VZVirtualMachine.pause()` / `.resume()` — gesamte VM eingefroren |
| `snapshot` | APFS-Copy-on-Write-Klon (`cp -c`) des Workspace → `snapshots/<id>/<name>/`; O(1) bei unveränderten Daten; **nur Dateisystem, kein Prozess-/Speicherzustand** | `saveMachineStateToURL` → `snapshots/<name>/machine-state` plus ein APFS-Klon von `disk.img`; **vollständiger Maschinenzustand** |
| `fork` | Workspace (oder ein Snapshot) wird in eine neue Sandbox mit eigenem Profil geklont; das Kind wird als laufend registriert | Python-seitiger `cp -Rc`-Klon des gesamten VM-Bundles (Disk + gespeicherte Zustände) |
| `restore` | Der Workspace wird durch den Snapshot-Klon ersetzt | Die Snapshot-Disk wird über die Live-Disk zurückgeklont; das nächste `exec` bootet aus dem wiederhergestellten Zustand |

Das `vz`-Backend erfasst den echten Maschinenzustand, was Seatbelt nicht kann.
Seine eine echte Lücke ist heute die gastseitige Kontrolle: `exec` führt das
Kommando als `init=`-Kernel-Shim bei einem frischen Boot aus (noch kein
In-Guest-vsock-Agent), sodass die stdout-Ausgabe des Kommandos nicht in
`ExecResult` erfasst wird und Pause/Restore keine laufende Shell über
`exec`-Aufrufe hinweg mitnehmen kann. Siehe
[vzrunner/README.md](vzrunner/README.md) für das Gast-Bundle-Format und die
aktuelle Einschränkung im Detail.

## Sicherheitsmodell

Kurzfassung — lies [SECURITY.md](SECURITY.md) für das vollständige
Bedrohungsmodell:

- `seatbelt` ist eine starke **Prozess**-Sandbox: Schreib-Containment auf den
  Workspace (+ Scratch-Tmp), Lesesperre für Credential-Speicher,
  Netzwerkrichtlinie pro Sandbox, Signale auf die Sandbox begrenzt. Sie ist
  **keine VM** — die Angriffsfläche des Kernels bleibt bestehen, und CPU/RAM
  werden nicht begrenzt. Für feindlichen Code `vz` verwenden.
- `vz` bietet Isolation auf VM-Niveau, ist aber experimentell (siehe die
  Einschränkungen oben).
- Der HTTP-Dienst bindet standardmäßig an `127.0.0.1` und verlangt ein Token
  (Token-Datei mit Modus 0600), sofern du nicht ausdrücklich
  `LOOPBOX_NO_AUTH=1` setzt.
- Loopbox reicht `--dangerously-skip-permissions` niemals selbst an
  Harness-CLIs weiter; riskante Kommandos in Loops treffen immer auf ein
  menschliches Gate, sofern du dich nicht mit `--auto-approve` dagegen
  entscheidest.

Sicherheitslücken bitte über
[GitHub Security Advisories](https://github.com/omnigeeker/loopbox/security/advisories/new)
melden.

## Repository-Struktur

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

## Entwicklung

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

CLI-Exit-Codes: `1` Laufzeitfehler, `2` Nutzungsfehler, `124`
Kommando-Timeout; `loop run` verwendet `0..3` wie oben dokumentiert. Setze
`LOOPBOX_DEBUG=1` für Tracebacks.

## Roadmap / bekannte Einschränkungen

- **`vz`-Gast-exec ist ein `init=`-Shim**: Jedes `exec` ist ein frischer Boot,
  stdout wird nicht in `ExecResult` erfasst, und das Fortsetzen des
  Maschinenzustands über Exec-Grenzen hinweg benötigt den
  In-Guest-vsock-Agenten (zukünftige Arbeit; die Bundle-/Socket-Verdrahtung
  unterstützt das bereits).
- **Keine Ressourcenquoten**: Keines der Backends begrenzt CPU, RAM oder
  Laufzeit der in der Sandbox ausgeführten Arbeit (pro Kommando existiert
  `--timeout`).
- **`seatbelt` ≠ VM**: Prozess-Sandbox auf einem gemeinsam genutzten Kernel;
  siehe [SECURITY.md](SECURITY.md).
- **`loopbox/server.py` ist Altbestand**: Nur zur Referenz aufbewahrt und
  nicht importierbar; verwende `loopbox/service.py` (`loopbox serve`).
- **Paketierung**: `pip install loopbox` von PyPI ist noch nicht
  veröffentlicht; aus dem Repo installieren.

## Lizenz

Apache-2.0 — siehe [LICENSE](LICENSE). © 2026 Loopbox Contributors.

## Dokumentation

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) — geführte Anleitung (Englisch)
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) — geführte Anleitung（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — tiefer Einblick in die Interna
- [docs/loopx-integration.md](docs/loopx-integration.md) — LoopX-Konzept-Zuordnung
- [vzrunner/README.md](vzrunner/README.md) — `vz`-Helfer und VM-Bundle-Format
- [SECURITY.md](SECURITY.md) — Bedrohungsmodell und Offenlegungsrichtlinie
