# Loopbox

**Lokale, E2B-protokollkompatible Sandboxes für macOS auf Apple Silicon (M1–M5).**

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Français](README.fr.md)

Loopbox lässt KI-Agenten-Harnesses (Codex CLI, Claude Code,
DSH / DeepSeek Harness und eigene Runner) nicht vertrauenswürdige Arbeit in
einer echten Sandbox auf deinem eigenen Mac ausführen — ganz ohne Cloud.
Alltägliche Aufgaben nutzen die Seatbelt-Prozess-Sandbox mit sofortigem
Start; wer Isolation auf Maschinenebene braucht, bekommt mit dem
experimentellen Virtualization.framework-Backend echtes Pausieren / Forken /
Fortsetzen einer laufenden VM.

```bash
pip install -e .        # Python 3.10+, macOS 13+, Apple Silicon
loopbox doctor          # Selbsttest: Architektur, Seatbelt, APFS-Klone, Smoke-Test

SID=$(loopbox new)                        # Sandbox erstellen
loopbox exec $SID -- echo "hello sandbox" # darin ausführen
loopbox snapshot $SID --name v1           # APFS-Copy-on-Write-Snapshot
loopbox fork $SID --snapshot v1           # identischen Zwilling abzweigen
loopbox pause $SID && loopbox resume $SID # einfrieren / fortsetzen
loopbox harness codex                     # Codex CLI in einer Sandbox starten
loopbox rm $SID --purge
```

## Warum Loopbox

Gehostete Sandboxes (wie E2B) sind hervorragend, aber manchmal müssen Code,
Zugangsdaten und das Latenzbudget auf der eigenen Maschine bleiben. Loopbox
behält das E2B-**Nutzungsprotokoll** bei (SDK-Form, HTTP-API-Form,
Snapshot-Semantik) und führt alles lokal aus.

## Funktionen

- **Apple-Silicon-nativ** — läuft auf M1 bis M5; erfordert macOS 13+
  (das experimentelle `vz`-Backend benötigt macOS 14+).
- **Zwei Isolations-Engines**
  - `seatbelt` (Standard): macOS-Seatbelt-Prozess-Sandbox über
    `sandbox-exec`. Sofortiger Start, schreibbezogene Isolation;
    Anmeldeinformationsspeicher (`~/.ssh`, Schlüsselbund, Browser-Cookies,
    Cloud-CLI-Konfigurationen) sind niemals lesbar; Netzwerkrichtlinie pro
    Sandbox (`outbound` / `all` / `deny`).
  - `vz` (experimentell): Virtualization.framework-MicroVM über den
    mitgelieferten Swift-Helper `vzrunner`. Vollständiger Maschinenzustand
    ermöglicht echtes Pausieren → Snapshot → Fork → Fortsetzen.
- **E2B-kompatibles Protokoll** — das Python-SDK hat dieselbe Form wie das
  E2B-SDK (`Sandbox.create()`, `commands.run()`, `files.read/write()`,
  `pause()`, `fork()`, `kill()`); die HTTP-API verwendet E2B-artige Routen
  und denselben `X-API-Key`-Header.
- **Vollständige lokale CLI** — alle Funktionen über `loopbox ...`
  erreichbar, überall mit `--json` für Skripte.
- **Hochleistungs-Snapshots** — APFS-Copy-on-Write-Klone, O(1) bei
  unveränderten Daten.
- **Bereit für Agenten-Harnesses** — `loopbox harness codex|claude|dsh`
  startet die Agenten-CLI in einer Sandbox; `loopbox loop` bietet eine
  Loop-Engine mit menschlichen Gates (Human-in-the-Loop).
- **Keine Laufzeitabhängigkeiten** — nur die Python-Standardbibliothek.

## Agenten-Harnesses in einer Sandbox ausführen

```bash
loopbox harness codex                # Codex CLI, isoliert, Workspace = Sandbox
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness; Argumente nach --
```

Der Harness-Prozess läuft unter Seatbelt: Er darf deine Toolchain lesen und
gemäß der Standardrichtlinie aufs Netzwerk zugreifen, kann aber nur **innerhalb**
des Sandbox-Workspaces **schreiben** und niemals deine Zugangsdaten lesen.
Von einem anderen Terminal aus kannst du die gesamte Sitzung mit
`loopbox pause` anhalten, `loopbox snapshot` sichern, mit `loopbox fork` einen
Erkundungszweig abspalten und mit `loopbox resume` fortsetzen — der Mensch
bleibt in der Schleife.

## Loop-Engine und menschliche Gates

`loopbox loop` führt den Zyklus „Selbsttest → Planung → Aktion → Verifikation“
für ein Ziel aus, mit dauerhaftem Zustand in `.loopbox/`. Wenn die Schleife
menschliches Urteilsvermögen braucht (Planfreigabe, riskante Aktion,
mehrdeutige Evidenz), schreibt sie eine Frage in `GATE.md` und wartet auf die
menschliche Antwort, statt zu raten. In Kombination mit
[LoopX](https://github.com/huangruiteng/loopx) erhältst du
harness-übergreifenden Zielzustand, Kontingente und Heartbeats — siehe
[../loopx-integration.md](../loopx-integration.md).

## Sicherheitsmodell (ehrliche Version)

- `seatbelt` ist eine starke **Prozess**-Sandbox: Schreib-Containment,
  Aussparung von Zugangsdaten, optionale Netzwerksperre. Sie ist keine VM;
  Kernel-Exploits liegen außerhalb des Schutzumfangs — dafür gibt es das
  `vz`-Backend.
- Der HTTP-Dienst bindet standardmäßig nur an `127.0.0.1` und verlangt immer
  ein Token, es sei denn, `LOOPBOX_NO_AUTH=1` ist gesetzt (niemals auf
  gemeinsam genutzten Maschinen).
- Der Sandbox-Zustand liegt in `~/.loopbox` (überschreibbar mit
  `LOOPBOX_HOME`).

## Entwicklung

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # Unit-Tests
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # + echte Seatbelt-Integrationstests
```

Code und Kommentare sind auf Englisch. Die Dokumentation ist mehrsprachig.

## Lizenz

Apache-2.0
