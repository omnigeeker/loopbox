# Loopbox

Sandboxes locales (local-first), compatibles con el protocolo E2B, para macOS en Apple Silicon (M1–M5).

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox permite que los harness de agentes de IA (Codex CLI, Claude Code,
DSH / DeepSeek Harness, o tus propios runners) ejecuten trabajo no confiable
dentro de un sandbox real en tu propia Mac — sin nube, sin el costo de una VM
Linux para las tareas cotidianas — manteniendo al mismo tiempo el *protocolo
de uso* de E2B: la forma del SDK, la forma de la API HTTP y la semántica de
snapshots. El backend predeterminado usa el sandbox de procesos Seatbelt de
macOS; un backend experimental de Virtualization.framework añade pausa /
snapshot / fork / reanudación de una VM completa. El runtime es solo la
biblioteca estándar de Python.

## Características

- **Dos backends de aislamiento**
  - `seatbelt` (predeterminado): sandbox de procesos de macOS mediante
    `sandbox-exec`. Arranque instantáneo, aislamiento de escritura del sistema
    de archivos, política de red por sandbox (`outbound` / `all` / `deny`),
    pausa/reanudación basada en `SIGSTOP`/`SIGCONT`, snapshots clonefile APFS
    copy-on-write.
  - `vz` (experimental): VMs Linux ARM64 de Virtualization.framework mediante
    el helper Swift incluido `vzrunner`. Snapshots de estado de máquina
    mediante `saveMachineStateToURL` / `restoreMachineStateFromURL`, fork
    mediante clonado APFS del bundle.
- **Superficie compatible con E2B**
  - SDK de Python con la misma forma que el SDK de E2B: `Sandbox.create()`,
    `sandbox.commands.run()`, `sandbox.files.read/write/list()`,
    `sandbox.pause()`, `sandbox.fork()`, `sandbox.kill()`.
  - API REST con rutas al estilo E2B (`POST /sandboxes`, pause/resume/timeout)
    más extensiones locales (exec, files, snapshots, fork), protegida con
    autenticación por token `X-API-Key`.
- **Exclusiones de credenciales** — `~/.ssh`, `~/.gnupg`, `~/.aws`,
  `~/.config/gh`, los llaveros y los almacenes de cookies del navegador nunca
  son legibles dentro de un sandbox seatbelt; las reglas de denegación
  prevalecen sobre las de permiso sin importar el orden.
- **Integración con harnesses** — `loopbox harness run <sandbox> codex|claude|dsh -- ...`
  ejecuta todo el CLI del agente dentro de un sandbox, de modo que el límite
  de loopbox se aplica sin importar cómo se configure el harness. Loopbox
  nunca inyecta silenciosamente flags que omitan permisos.
- **Motor de bucles** — `loopbox loop` ejecuta un ciclo durable de
  autocomprobación → autorreflexión → autoiteración con puertas
  human-in-the-loop, con checkpoint a JSON después de cada paso y reanudable
  tras cualquier kill.
- **Cero dependencias en tiempo de ejecución** — Python ≥ 3.10, solo la
  biblioteca estándar.

## Cómo funciona

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

La CLI maneja el registro y los backends directamente (nunca pasa por la capa
HTTP); el SDK hace lo mismo. El servicio es una superficie delgada al estilo
E2B sobre el mismo registro/backends, así que las tres vistas de un sandbox
coinciden.

## Requisitos

- macOS 13+ en Apple Silicon (arm64); el backend `vz` requiere macOS 14+.
- Python ≥ 3.10 (`python3 --version`). Sin dependencias de terceros en tiempo
  de ejecución.
- APFS (estándar en Apple Silicon) para los snapshots copy-on-write.
- El esquema JSON de los archivos de estado es interno; `~/.loopbox` puede
  moverse con `LOOPBOX_HOME`.
- Solo para compilar el helper `vz`: Xcode Command Line Tools
  (`xcode-select --install`).

Ejecuta `loopbox doctor` para verificar todo lo anterior (arquitectura,
versión de macOS, `sandbox-exec`, soporte de clonado APFS, presencia de
vzrunner, smoke test de seatbelt).

## Inicio rápido

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

Crear y usar un sandbox (CLI):

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

O con el SDK:

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

Ejecuta el servicio HTTP y contrólalo con curl:

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

El token se genera en la primera ejecución en `~/.loopbox/auth.json` (modo
0600); `Authorization: Bearer <token>` se acepta como alias. `GET /health` no
requiere autenticación. `LOOPBOX_NO_AUTH=1` desactiva la autenticación —
úsalo solo para desarrollo local. Un sweeper en segundo plano hace cumplir
los timeouts de los sandboxes.

## Compatibilidad con E2B

Loopbox habla un subconjunto de la API de control de E2B más extensiones
locales. Los template IDs de E2B se mapean a nombres de backend de loopbox
(`seatbelt`, `vz`).

| Endpoint | Estado |
|---|---|
| `POST /sandboxes` | Soportado (`{"templateID", "timeout", "metadata", "envVars"}` → 201) |
| `GET /sandboxes` | Soportado |
| `GET /sandboxes/{id}` | Soportado (registro al estilo E2B) |
| `DELETE /sandboxes/{id}` | Soportado (kill + eliminación del registro, 204) |
| `POST /sandboxes/{id}/timeout` | Soportado (establece `timeout_deadline`; el sweeper lo hace cumplir) |
| `POST /sandboxes/{id}/pause` | Soportado (204) |
| `POST /sandboxes/{id}/resume` | Soportado (204) |
| `POST /sandboxes/{id}/exec` | Extensión de Loopbox — reemplaza el `POST /process` de envd; los comandos en cadena se ejecutan vía `/bin/zsh -lc` |
| `GET /sandboxes/{id}/files?path=…` | Extensión de Loopbox — lista las entradas del workspace |
| `PUT /sandboxes/{id}/files` | Extensión de Loopbox — escribe `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Extensión de Loopbox (201 `{"snapshotID"}`) |
| `GET /sandboxes/{id}/snapshots` | Extensión de Loopbox |
| `POST /sandboxes/{id}/fork` | Extensión de Loopbox (`{"snapshotID"?}` → 201 `{"sandboxID"}`) |
| `GET /health` | Extensión de Loopbox, sin autenticación |
| envd process streaming / websockets | No implementado |
| E2B template build/manage APIs | No implementado — los templates son nombres de backends locales |
| Hosted-only surface (teams, metrics, auth0) | Fuera de alcance — loopbox es solo local |

Los errores tienen la forma de E2B: `{"code": <int>, "message": <str>}`.

## Integración con harnesses

Un harness elige su runtime según *dónde se ejecuta*: lanzar `codex` o
`claude` en el host deja la protección en manos del sandbox propio del
harness. Loopbox lanza todo el CLI del harness dentro de un sandbox, de modo
que el perfil Seatbelt (o la VM) de loopbox es el límite exterior sin importar
la configuración del harness:

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

Todo lo que va después de `--` se pasa al harness literalmente. Loopbox
deliberadamente nunca inyecta flags de permisos nativos del harness:
`--dangerously-skip-permissions` o `--sandbox danger-full-access` son
elección del *llamante*, con sentido solo dentro del sandbox, nunca algo que
loopbox añada por ti (consulta [SECURITY.md](SECURITY.md)).

## Ingeniería de bucles con puertas humanas

`loopbox loop` es un motor de bucles durable (conceptos de
[LoopX](https://github.com/huangruiteng/loopx); ejecución aislada por
sandboxes de loopbox). El ledger bajo `~/.loopbox/loops/<loop_id>/` se guarda
en checkpoint después de cada paso — un bucle terminado simplemente se
reanuda con `run` otra vez.

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

- **Autorreflexión** (*self-think*): un CLI de harness LLM (`codex exec` o
  `claude -p` cuando está en PATH; se sobrescribe con
  `LOOPBOX_HARNESS="cmd {prompt}"`, `LOOPBOX_HARNESS_TIMEOUT`) propone la
  siguiente acción. El paso de reflexión se ejecuta en el host; la
  *ejecución* ocurre dentro del sandbox. Sin un harness, un fallback
  determinista basado en reglas planifica y escala el juicio a las puertas.
- **Autocomprobación** (*self-check*): el comando propuesto se ejecuta vía el
  SDK dentro del sandbox del bucle; un comando `verify` opcional también debe
  salir con 0.
- **Puertas humanas**: `approve_plan`, `approve_step` (los comandos riesgosos
  como `rm -rf` o `git push` siempre pasan por una puerta salvo
  `--auto-approve`), `on_failure`. Responde mediante el prompt del TTY, la
  CLI desde otra terminal, o editando `gate.json` / `GATE.md` en el
  directorio del bucle.
- Códigos de salida de `run`: `0` objetivo cumplido, `1` falló, `2` detenido
  por presupuesto/interrupción, `3` bloqueado en una puerta pendiente.

## Snapshots, fork y reanudación por backend

| Operación | `seatbelt` | `vz` (experimental) |
|---|---|---|
| `pause` / `resume` | `SIGSTOP`/`SIGCONT` sobre los grupos de procesos registrados — instantáneo, mantiene la memoria viva | `VZVirtualMachine.pause()` / `.resume()` — VM completa congelada |
| `snapshot` | Clon APFS copy-on-write (`cp -c`) del workspace → `snapshots/<id>/<name>/`; O(1) en datos sin cambios; **solo sistema de archivos, sin estado de procesos/memoria** | `saveMachineStateToURL` → `snapshots/<name>/machine-state` más un clon APFS de `disk.img`; **estado completo de la máquina** |
| `fork` | Clona el workspace (o un snapshot) en un nuevo sandbox con su propio perfil; el hijo queda registrado como en ejecución | Clon `cp -Rc` del bundle completo de la VM desde Python (disco + estados guardados) |
| `restore` | El workspace se reemplaza con el clon del snapshot | El disco del snapshot se clona de vuelta sobre el disco activo; el siguiente `exec` arranca desde el estado restaurado |

El backend `vz` captura el verdadero estado de la máquina, algo que Seatbelt
no puede hacer. Su única carencia real hoy es el control del lado del guest:
`exec` ejecuta el comando como un shim de kernel `init=` en un arranque nuevo
(aún no hay agente vsock dentro del guest), así que el stdout del comando no
se captura en `ExecResult` y pause/restore no pueden transportar un shell en
ejecución entre llamadas `exec`. Consulta
[vzrunner/README.md](vzrunner/README.md) para el formato del bundle guest y la
limitación actual en detalle.

## Modelo de seguridad

Versión corta — lee [SECURITY.md](SECURITY.md) para el modelo de amenazas
completo:

- `seatbelt` es un sandbox de **procesos** fuerte: contención de escritura al
  workspace (+ tmp temporal), denegación de lectura de los almacenes de
  credenciales, política de red por sandbox, señales limitadas al sandbox.
  **No es una VM** — la superficie de ataque del kernel permanece, y CPU/RAM
  no están limitados. Para código hostil, usa `vz`.
- `vz` ofrece aislamiento de nivel VM, pero es experimental (consulta las
  limitaciones más arriba).
- El servicio HTTP se vincula a `127.0.0.1` por defecto y exige un token
  (archivo de token 0600) salvo que definas explícitamente
  `LOOPBOX_NO_AUTH=1`.
- Loopbox nunca pasa `--dangerously-skip-permissions` a los CLI de harness
  por sí mismo; los comandos riesgosos en los bucles siempre chocan con una
  puerta humana salvo que lo desactives con `--auto-approve`.

Informa vulnerabilidades a través de
[GitHub security advisories](https://github.com/omnigeeker/loopbox/security/advisories/new).

## Estructura del repositorio

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

## Desarrollo

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

Códigos de salida de la CLI: `1` error en tiempo de ejecución, `2` error de
uso, `124` timeout de comando; `loop run` usa `0..3` como se documentó
arriba. Define `LOOPBOX_DEBUG=1` para ver los tracebacks.

## Hoja de ruta / limitaciones conocidas

- **El exec del guest `vz` es un shim `init=`**: cada `exec` es un arranque
  nuevo, el stdout no se captura en `ExecResult`, y la reanudación del estado
  de máquina a través de los límites de exec necesita el agente vsock dentro
  del guest (trabajo futuro; la infraestructura de bundle/socket ya lo
  admite).
- **Sin cuotas de recursos**: ningún backend limita CPU, RAM ni tiempo de
  reloj del trabajo en el sandbox (existe `--timeout` por comando).
- **`seatbelt` ≠ VM**: sandbox de procesos sobre un kernel compartido;
  consulta [SECURITY.md](SECURITY.md).
- **`loopbox/server.py` es legacy**: se conserva solo como referencia y no es
  importable; usa `loopbox/service.py` (`loopbox serve`).
- **Empaquetado**: `pip install loopbox` desde PyPI aún no está publicado;
  instala desde el repositorio.

## Licencia

Apache-2.0 — consulta [LICENSE](LICENSE). © 2026 Loopbox Contributors.

## Documentación

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) — tutorial guiado (inglés)
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) — tutorial guiado（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — inmersión profunda en los internos
- [docs/loopx-integration.md](docs/loopx-integration.md) — mapeo de conceptos de LoopX
- [vzrunner/README.md](vzrunner/README.md) — helper `vz` y formato del bundle de la VM
- [SECURITY.md](SECURITY.md) — modelo de amenazas y política de divulgación
