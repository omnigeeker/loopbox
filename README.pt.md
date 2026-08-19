# Loopbox

Sandboxes locais (local-first), compatíveis com o protocolo E2B, para macOS em Apple Silicon (M1–M5).

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

O Loopbox permite que harnesses de agentes de IA (Codex CLI, Claude Code,
DSH / DeepSeek Harness ou seus próprios runners) executem trabalho não
confiável dentro de um sandbox real no seu próprio Mac — sem nuvem, sem o
custo de uma VM Linux para as tarefas do dia a dia — mantendo ao mesmo tempo
o *protocolo de uso* do E2B: formato do SDK, formato da API HTTP e semântica
de snapshots. O backend padrão usa o sandbox de processos Seatbelt do macOS;
um backend experimental baseado em Virtualization.framework adiciona
pausa / snapshot / fork / retomada de uma VM inteira. O runtime usa apenas a
biblioteca padrão do Python.

## Recursos

- **Dois backends de isolamento**
  - `seatbelt` (padrão): sandbox de processos do macOS via `sandbox-exec`.
    Inicialização instantânea, isolamento do sistema de arquivos com escopo
    de escrita, política de rede por sandbox (`outbound` / `all` / `deny`),
    pausa/retomada baseadas em `SIGSTOP`/`SIGCONT`, snapshots APFS
    copy-on-write por clonefile.
  - `vz` (experimental): VMs Linux ARM64 via Virtualization.framework, por
    meio do helper Swift incluído `vzrunner`. Snapshots do estado da máquina
    via `saveMachineStateToURL` / `restoreMachineStateFromURL`, fork via
    clone APFS do bundle.
- **Superfície compatível com E2B**
  - SDK Python com o mesmo formato do SDK do E2B: `Sandbox.create()`,
    `sandbox.commands.run()`, `sandbox.files.read/write/list()`,
    `sandbox.pause()`, `sandbox.fork()`, `sandbox.kill()`.
  - API REST com rotas no formato E2B (`POST /sandboxes`, pause/resume/timeout)
    mais extensões locais (exec, files, snapshots, fork), protegida com
    autenticação por token `X-API-Key`.
- **Exclusões de credenciais** — `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`,
  chaveiros e armazenamentos de cookies de navegador nunca são legíveis dentro
  de um sandbox seatbelt; as regras de negação prevalecem sobre as de
  permissão, independentemente da ordem.
- **Integração com harnesses** — `loopbox harness run <sandbox> codex|claude|dsh -- ...`
  executa todo o CLI do agente dentro de um sandbox, de modo que a fronteira
  do loopbox se aplica independentemente de como o harness se configura. O
  Loopbox nunca injeta silenciosamente flags de bypass de permissão.
- **Motor de loop** — `loopbox loop` executa um ciclo durável de
  auto-verificação → auto-reflexão → auto-iteração com portões de
  humano-no-loop, com checkpoint em JSON após cada passo e retomável após
  qualquer interrupção (kill).
- **Zero dependências de runtime** — Python ≥ 3.10, apenas biblioteca padrão.

## Como funciona

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

A CLI controla o registro e os backends diretamente (nunca passa pela camada
HTTP); o SDK faz o mesmo. O serviço é uma superfície fina no formato E2B sobre
o mesmo registro e backends, de modo que as três visões de um sandbox estão
sempre de acordo.

## Requisitos

- macOS 13+ em Apple Silicon (arm64); o backend `vz` precisa de macOS 14+.
- Python ≥ 3.10 (`python3 --version`). Sem dependências de runtime de terceiros.
- APFS (padrão no Apple Silicon) para snapshots copy-on-write.
- O esquema JSON dos arquivos de estado é interno; `~/.loopbox` pode ser
  movido com `LOOPBOX_HOME`.
- Apenas para compilar o helper `vz`: Xcode Command Line Tools
  (`xcode-select --install`).

Execute `loopbox doctor` para verificar tudo o que está acima (arquitetura,
versão do macOS, `sandbox-exec`, suporte a clone APFS, presença do vzrunner,
teste de fumaça do seatbelt).

## Início rápido

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

Criar e usar um sandbox (CLI):

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

Ou o SDK:

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

Execute o serviço HTTP e controle-o com curl:

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

O token é gerado na primeira execução em `~/.loopbox/auth.json` (modo 0600);
`Authorization: Bearer <token>` também é aceito como alias. `GET /health` não
requer autenticação. `LOOPBOX_NO_AUTH=1` desativa a autenticação — use apenas
para desenvolvimento local. Um sweeper em segundo plano aplica os timeouts dos
sandboxes.

## Compatibilidade com E2B

O Loopbox fala um subconjunto da API de controle do E2B mais extensões locais.
Os IDs de template do E2B correspondem aos nomes de backend do loopbox
(`seatbelt`, `vz`).

| Endpoint | Status |
|---|---|
| `POST /sandboxes` | Suportado (`{"templateID", "timeout", "metadata", "envVars"}` → 201) |
| `GET /sandboxes` | Suportado |
| `GET /sandboxes/{id}` | Suportado (registro no formato E2B) |
| `DELETE /sandboxes/{id}` | Suportado (kill + desregistro, 204) |
| `POST /sandboxes/{id}/timeout` | Suportado (define `timeout_deadline`; o sweeper aplica) |
| `POST /sandboxes/{id}/pause` | Suportado (204) |
| `POST /sandboxes/{id}/resume` | Suportado (204) |
| `POST /sandboxes/{id}/exec` | Extensão do Loopbox — substitui o `POST /process` do envd; comandos em string são executados via `/bin/zsh -lc` |
| `GET /sandboxes/{id}/files?path=…` | Extensão do Loopbox — lista as entradas do workspace |
| `PUT /sandboxes/{id}/files` | Extensão do Loopbox — escreve `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Extensão do Loopbox (201 `{"snapshotID"}`) |
| `GET /sandboxes/{id}/snapshots` | Extensão do Loopbox |
| `POST /sandboxes/{id}/fork` | Extensão do Loopbox (`{"snapshotID"?}` → 201 `{"sandboxID"}`) |
| `GET /health` | Extensão do Loopbox, sem autenticação |
| streaming de processos / websockets do envd | Não implementado |
| APIs de build/gerenciamento de templates do E2B | Não implementado — templates são nomes de backend locais |
| Superfície exclusiva do hosted (teams, metrics, auth0) | Fora de escopo — o loopbox é apenas local |

Os erros seguem o formato do E2B: `{"code": <int>, "message": <str>}`.

## Integração com harnesses

Um harness escolhe seu runtime por *onde ele executa*: iniciar o `codex` ou o
`claude` no host deixa a proteção a cargo do próprio sandbox do harness. O
Loopbox lança todo o CLI do harness dentro de um sandbox, de modo que o perfil
Seatbelt do loopbox (ou a VM) é a fronteira externa, independentemente da
configuração do harness:

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

Tudo após `--` é passado ao harness verbatim. O Loopbox deliberadamente nunca
injeta flags nativas de permissão do harness: `--dangerously-skip-permissions`
ou `--sandbox danger-full-access` são escolha do *chamador*, significativas
apenas dentro do sandbox, nunca algo que o loopbox adiciona por você
(veja [SECURITY.md](SECURITY.md)).

## Engenharia de loops com portões humanos

`loopbox loop` é um motor de loop durável (conceitos do
[LoopX](https://github.com/huangruiteng/loopx); execução isolada pelos
sandboxes do loopbox). O ledger em `~/.loopbox/loops/<loop_id>/` recebe
checkpoint após cada passo — um loop interrompido (kill) simplesmente é
retomado com `run` novamente.

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

- **Auto-reflexão (self-think)**: um CLI de harness LLM (`codex exec` ou
  `claude -p` quando presente no PATH; substitua com
  `LOOPBOX_HARNESS="cmd {prompt}"`, `LOOPBOX_HARNESS_TIMEOUT`) propõe a
  próxima ação. A etapa de reflexão roda no host; a *execução* acontece dentro
  do sandbox. Sem um harness, um fallback determinístico baseado em regras
  planeja e encaminha julgamentos aos portões.
- **Auto-verificação (self-check)**: o comando proposto é executado via SDK
  dentro do sandbox do loop; um comando `verify` opcional também deve sair
  com 0.
- **Portões humanos**: `approve_plan`, `approve_step` (comandos arriscados
  como `rm -rf` ou `git push` sempre passam por um portão, a menos que
  `--auto-approve`), `on_failure`. Responda pelo prompt do TTY, pela CLI de
  outro terminal, ou editando `gate.json` / `GATE.md` no diretório do loop.
- Códigos de saída de `run`: `0` objetivo atingido, `1` falhou, `2` interrompido
  por orçamento/interrupção, `3` bloqueado em um portão pendente.

## Snapshots, fork e retomada por backend

| Operação | `seatbelt` | `vz` (experimental) |
|---|---|---|
| `pause` / `resume` | `SIGSTOP`/`SIGCONT` nos grupos de processos registrados — instantâneo, mantém a memória ativa | `VZVirtualMachine.pause()` / `.resume()` — VM inteira congelada |
| `snapshot` | Clone APFS copy-on-write (`cp -c`) do workspace → `snapshots/<id>/<name>/`; O(1) em dados inalterados; **somente sistema de arquivos, sem estado de processo/memória** | `saveMachineStateToURL` → `snapshots/<name>/machine-state` mais um clone APFS de `disk.img`; **estado completo da máquina** |
| `fork` | Clona o workspace (ou um snapshot) em um novo sandbox com seu próprio perfil; o filho é registrado como em execução | Clone `cp -Rc` pelo lado Python de todo o bundle da VM (disco + estados salvos) |
| `restore` | O workspace é substituído pelo clone do snapshot | O disco do snapshot é clonado de volta sobre o disco ativo; o próximo `exec` inicializa a partir do estado restaurado |

O backend `vz` captura o verdadeiro estado da máquina, o que o Seatbelt não
consegue. A sua única lacuna real hoje é o controle do lado do convidado: o
`exec` executa o comando como um shim `init=` de kernel em uma inicialização
nova (ainda não há um agente vsock no convidado), portanto o stdout do comando
não é capturado no `ExecResult` e pause/restore não conseguem transportar um
shell em execução entre chamadas `exec`. Veja
[vzrunner/README.md](vzrunner/README.md) para o formato do bundle do convidado
e a limitação atual em detalhes.

## Modelo de segurança

Versão curta — leia [SECURITY.md](SECURITY.md) para o modelo de ameaças completo:

- `seatbelt` é um sandbox de **processos** forte: contenção de escrita no
  workspace (+ tmp de raspagem), negação de leitura dos armazenamentos de
  credenciais, política de rede por sandbox, sinais com escopo ao sandbox. Ele
  **não é uma VM** — a superfície de ataque do kernel permanece, e CPU/RAM não
  são limitados. Para código hostil, use `vz`.
- `vz` oferece isolamento de nível de VM, mas é experimental (veja as
  limitações acima).
- O serviço HTTP vincula-se a `127.0.0.1` por padrão e exige um token (arquivo
  de token 0600), a menos que você defina explicitamente `LOOPBOX_NO_AUTH=1`.
- O Loopbox nunca passa `--dangerously-skip-permissions` para os CLIs de
  harness por conta própria; comandos arriscados em loops sempre passam por um
  portão humano, a menos que você opte por `--auto-approve`.

Relate vulnerabilidades via
[avisos de segurança do GitHub](https://github.com/omnigeeker/loopbox/security/advisories/new).

## Estrutura do repositório

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

## Desenvolvimento

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

Códigos de saída da CLI: `1` erro de runtime, `2` erro de uso, `124` timeout de
comando; `loop run` usa `0..3` conforme documentado acima. Defina
`LOOPBOX_DEBUG=1` para tracebacks.

## Roadmap / limitações conhecidas

- **O `exec` do convidado no `vz` é um shim `init=`**: cada `exec` é uma
  inicialização nova, o stdout não é capturado no `ExecResult`, e a retomada do
  estado da máquina entre limites de `exec` precisa do agente vsock no
  convidado (trabalho futuro; a infraestrutura de bundle/socket já o suporta).
- **Sem cotas de recursos**: nenhum backend limita CPU, RAM ou tempo de relógio
  do trabalho no sandbox (existe `--timeout` por comando).
- **`seatbelt` ≠ VM**: sandbox de processos em um kernel compartilhado; veja
  [SECURITY.md](SECURITY.md).
- **`loopbox/server.py` é legado**: mantido apenas como referência e não
  importável; use `loopbox/service.py` (`loopbox serve`).
- **Empacotamento**: `pip install loopbox` ainda não está publicado no PyPI;
  instale a partir do repositório.

## Licença

Apache-2.0 — veja [LICENSE](LICENSE). © 2026 Loopbox Contributors.

## Documentação

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) — tutorial guiado (inglês)
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) — tutorial guiado（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — mergulho profundo nos internos
- [docs/loopx-integration.md](docs/loopx-integration.md) — mapeamento de conceitos do LoopX
- [vzrunner/README.md](vzrunner/README.md) — helper `vz` e formato do bundle da VM
- [SECURITY.md](SECURITY.md) — modelo de ameaças e política de divulgação
