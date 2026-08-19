# Loopbox

Bacs à sable local-first, compatibles avec le protocole E2B, pour macOS sur Apple Silicon (M1–M5).

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox permet aux harness d'agents IA (Codex CLI, Claude Code, DSH / DeepSeek Harness,
ou vos propres runners) d'exécuter du travail non fiable dans un véritable bac à sable
sur votre propre Mac — sans cloud, sans le coût d'une VM Linux pour les tâches
quotidiennes — tout en conservant le *protocole d'utilisation* d'E2B : la forme du
SDK, la forme de l'API HTTP et la sémantique des instantanés. Le backend par défaut
utilise le bac à sable de processus Seatbelt de macOS ; un backend expérimental
Virtualization.framework ajoute la pause / l'instantané / le fork / la reprise d'une
VM entière. L'exécution ne repose que sur la bibliothèque standard de Python.

## Fonctionnalités

- **Deux backends d'isolation**
  - `seatbelt` (par défaut) : bac à sable de processus macOS via `sandbox-exec`.
    Démarrage instantané, isolation du système de fichiers limitée aux écritures,
    politique réseau par bac à sable (`outbound` / `all` / `deny`), pause/reprise
    basés sur `SIGSTOP`/`SIGCONT`, instantanés APFS copy-on-write par clonefile.
  - `vz` (expérimental) : VM Linux ARM64 Virtualization.framework via l'assistant
    Swift fourni `vzrunner`. Instantanés de l'état machine via
    `saveMachineStateToURL` / `restoreMachineStateFromURL`, fork par clone APFS
    du bundle.
- **Surface compatible E2B**
  - SDK Python de même forme que le SDK E2B : `Sandbox.create()`,
    `sandbox.commands.run()`, `sandbox.files.read/write/list()`,
    `sandbox.pause()`, `sandbox.fork()`, `sandbox.kill()`.
  - API REST avec des routes de forme E2B (`POST /sandboxes`, pause/resume/timeout)
    plus des extensions locales (exec, files, snapshots, fork), sécurisée par
    authentification par jeton `X-API-Key`.
- **Exclusions des identifiants** — `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`,
  les trousseaux et les magasins de cookies des navigateurs ne sont jamais
  lisibles à l'intérieur d'un bac à sable seatbelt ; les règles de refus
  l'emportent sur les règles d'autorisation, quel que soit leur ordre.
- **Intégration des harness** — `loopbox harness run <sandbox> codex|claude|dsh -- ...`
  exécute l'intégralité du CLI de l'agent dans un bac à sable, de sorte que la
  frontière loopbox s'applique quelle que soit la manière dont le harness se
  configure. Loopbox n'injecte jamais silencieusement de flags de contournement
  des permissions.
- **Moteur de boucle** — `loopbox loop` exécute un cycle durable auto-vérification →
  auto-réflexion → auto-itération avec des portes human-in-the-loop, sauvegardé en
  JSON après chaque étape et reprenable après n'importe quel kill.
- **Zéro dépendance d'exécution** — Python ≥ 3.10, bibliothèque standard uniquement.

## Fonctionnement

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

La CLI pilote directement le registre et les backends (elle ne passe jamais par la
couche HTTP) ; le SDK fait de même. Le service est une fine surface de forme E2B
au-dessus des mêmes registre et backends, de sorte que les trois vues d'un bac à
sable concordent toujours.

## Prérequis

- macOS 13+ sur Apple Silicon (arm64) ; le backend `vz` requiert macOS 14+.
- Python ≥ 3.10 (`python3 --version`). Aucune dépendance d'exécution tierce.
- APFS (standard sur Apple Silicon) pour les instantanés copy-on-write.
- Le schéma JSON des fichiers d'état est interne ; `~/.loopbox` peut être déplacé
  avec `LOOPBOX_HOME`.
- Uniquement pour compiler l'assistant `vz` : Xcode Command Line Tools
  (`xcode-select --install`).

Exécutez `loopbox doctor` pour vérifier tout ce qui précède (architecture, version
de macOS, `sandbox-exec`, prise en charge des clones APFS, présence de vzrunner,
test de fumée seatbelt).

## Démarrage rapide

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

Créer et utiliser un bac à sable (CLI) :

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

Ou le SDK :

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

Lancez le service HTTP et pilotez-le avec curl :

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

Le jeton est généré au premier lancement dans `~/.loopbox/auth.json` (mode 0600) ;
`Authorization: Bearer <token>` est accepté comme alias. `GET /health` n'est pas
authentifié. `LOOPBOX_NO_AUTH=1` désactive l'authentification — à n'utiliser que
pour le développement local. Un sweeper en arrière-plan applique les délais
d'expiration des bacs à sable.

## Compatibilité E2B

Loopbox parle un sous-ensemble de l'API de contrôle E2B, plus des extensions
locales. Les ID de template E2B correspondent aux noms de backends loopbox
(`seatbelt`, `vz`).

| Endpoint | Statut |
|---|---|
| `POST /sandboxes` | Pris en charge (`{"templateID", "timeout", "metadata", "envVars"}` → 201) |
| `GET /sandboxes` | Pris en charge |
| `GET /sandboxes/{id}` | Pris en charge (enregistrement de forme E2B) |
| `DELETE /sandboxes/{id}` | Pris en charge (kill + désinscription, 204) |
| `POST /sandboxes/{id}/timeout` | Pris en charge (définit `timeout_deadline` ; le sweeper l'applique) |
| `POST /sandboxes/{id}/pause` | Pris en charge (204) |
| `POST /sandboxes/{id}/resume` | Pris en charge (204) |
| `POST /sandboxes/{id}/exec` | Extension Loopbox — remplace envd `POST /process` ; les commandes sous forme de chaîne s'exécutent via `/bin/zsh -lc` |
| `GET /sandboxes/{id}/files?path=…` | Extension Loopbox — liste les entrées du workspace |
| `PUT /sandboxes/{id}/files` | Extension Loopbox — écrit `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Extension Loopbox (201 `{"snapshotID"}`) |
| `GET /sandboxes/{id}/snapshots` | Extension Loopbox |
| `POST /sandboxes/{id}/fork` | Extension Loopbox (`{"snapshotID"?}` → 201 `{"sandboxID"}`) |
| `GET /health` | Extension Loopbox, non authentifié |
| envd process streaming / websockets | Non implémenté |
| E2B template build/manage APIs | Non implémenté — les templates sont des noms de backends locaux |
| Hosted-only surface (teams, metrics, auth0) | Hors périmètre — loopbox est exclusivement local |

Les erreurs ont la forme E2B : `{"code": <int>, "message": <str>}`.

## Intégration des harness

Un harness choisit son environnement d'exécution par *l'endroit où il s'exécute* :
lancer `codex` ou `claude` sur l'hôte laisse la protection au propre bac à sable
du harness. Loopbox lance l'intégralité du CLI du harness dans un bac à sable, de
sorte que le profil Seatbelt (ou la VM) de loopbox constitue la frontière
extérieure, quelle que soit la configuration du harness :

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

Tout ce qui suit `--` est transmis au harness tel quel. Loopbox n'injecte
délibérément jamais de flags de permission natifs du harness :
`--dangerously-skip-permissions` ou `--sandbox danger-full-access` relèvent du
choix de *l'appelant*, n'ont de sens qu'à l'intérieur du bac à sable, et ne sont
jamais quelque chose que loopbox ajoute pour vous
(voir [SECURITY.md](SECURITY.md)).

## Ingénierie de boucle avec portes humaines

`loopbox loop` est un moteur de boucle durable (concepts issus de
[LoopX](https://github.com/huangruiteng/loopx) ; exécution isolée par les bacs à
sable loopbox). Le registre sous `~/.loopbox/loops/<loop_id>/` est sauvegardé
après chaque étape — une boucle tuée reprend simplement avec `run`.

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

- **Auto-réflexion** : un CLI de harness LLM (`codex exec` ou `claude -p`
  lorsqu'il est sur le PATH ; surcharge possible avec
  `LOOPBOX_HARNESS="cmd {prompt}"`, `LOOPBOX_HARNESS_TIMEOUT`) propose l'action
  suivante. L'étape de réflexion s'exécute sur l'hôte ; l'*exécution* a lieu à
  l'intérieur du bac à sable. Sans harness, un repli déterministe à base de
  règles planifie et délègue le jugement aux portes.
- **Auto-vérification** : la commande proposée s'exécute via le SDK dans le bac
  à sable de la boucle ; une commande `verify` optionnelle doit également
  retourner 0.
- **Portes humaines** : `approve_plan`, `approve_step` (les commandes risquées
  comme `rm -rf` ou `git push` passent toujours par une porte sauf avec
  `--auto-approve`), `on_failure`. Répondez via l'invite du TTY, la CLI depuis un
  autre terminal, ou en éditant `gate.json` / `GATE.md` dans le répertoire de la
  boucle.
- Codes de sortie de `run` : `0` objectif atteint, `1` échec, `2` arrêté par le
  budget/une interruption, `3` bloqué sur une porte en attente.

## Instantanés, fork et reprise par backend

| Opération | `seatbelt` | `vz` (expérimental) |
|---|---|---|
| `pause` / `resume` | `SIGSTOP`/`SIGCONT` sur les groupes de processus enregistrés — instantané, conserve la mémoire vive | `VZVirtualMachine.pause()` / `.resume()` — VM entière figée |
| `snapshot` | Clone APFS copy-on-write (`cp -c`) du workspace → `snapshots/<id>/<name>/` ; O(1) pour les données inchangées ; **système de fichiers uniquement, pas d'état processus/mémoire** | `saveMachineStateToURL` → `snapshots/<name>/machine-state` plus un clone APFS de `disk.img` ; **état complet de la machine** |
| `fork` | Clone le workspace (ou un instantané) dans un nouveau bac à sable avec son propre profil ; l'enfant est enregistré à l'état running | Clone `cp -Rc` côté Python de tout le bundle VM (disque + états sauvegardés) |
| `restore` | Le workspace est remplacé par le clone de l'instantané | Le disque de l'instantané est re-cloné par-dessus le disque actif ; le prochain `exec` démarre depuis l'état restauré |

Le backend `vz` capture le véritable état de la machine, ce que Seatbelt ne peut
pas faire. Sa seule vraie lacune aujourd'hui est le contrôle côté invité : `exec`
exécute la commande comme un shim noyau `init=` sur un démarrage à froid (pas
encore d'agent vsock dans l'invité), donc la sortie standard de la commande n'est
pas capturée dans `ExecResult`, et pause/restore ne peuvent pas transporter un
shell en cours d'exécution à travers les appels `exec`. Voir
[vzrunner/README.md](vzrunner/README.md) pour le format du bundle invité et la
limitation actuelle en détail.

## Modèle de sécurité

Version courte — lisez [SECURITY.md](SECURITY.md) pour le modèle de menace
complet :

- `seatbelt` est un robuste bac à sable de **processus** : confinement des
  écritures dans le workspace (+ tmp temporaire), refus de lecture des magasins
  d'identifiants, politique réseau par bac à sable, signaux limités au bac à
  sable. Ce n'est **pas une VM** — la surface d'attaque du noyau demeure, et
  CPU/RAM ne sont pas bornés. Pour du code hostile, utilisez `vz`.
- `vz` offre une isolation de niveau VM, mais reste expérimental (voir les
  limitations ci-dessus).
- Le service HTTP se lie à `127.0.0.1` par défaut et exige un jeton (fichier de
  jeton en 0600), sauf si vous définissez explicitement `LOOPBOX_NO_AUTH=1`.
- Loopbox ne passe jamais lui-même `--dangerously-skip-permissions` aux CLI des
  harness ; les commandes risquées dans les boucles rencontrent toujours une
  porte humaine, sauf si vous y renoncez avec `--auto-approve`.

Signalez les vulnérabilités via les
[avis de sécurité GitHub](https://github.com/omnigeeker/loopbox/security/advisories/new).

## Structure du dépôt

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

## Développement

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

Codes de sortie de la CLI : `1` erreur d'exécution, `2` erreur d'usage, `124`
dépassement de délai de la commande ; `loop run` utilise `0..3` comme documenté
ci-dessus. Définissez `LOOPBOX_DEBUG=1` pour afficher les tracebacks.

## Feuille de route / limitations connues

- **L'exec invité de `vz` est un shim `init=`** : chaque `exec` est un démarrage
  à froid, la sortie standard n'est pas capturée dans `ExecResult`, et la
  reprise de l'état machine à travers les frontières d'exec nécessite l'agent
  vsock dans l'invité (travail futur ; la plomberie bundle/socket le prend déjà
  en charge).
- **Pas de quotas de ressources** : aucun des deux backends ne borne le CPU, la
  RAM ou le temps réel du travail dans le bac à sable (un `--timeout` par
  commande existe).
- **`seatbelt` ≠ VM** : bac à sable de processus sur un noyau partagé ; voir
  [SECURITY.md](SECURITY.md).
- **`loopbox/server.py` est legacy** : conservé pour référence uniquement et non
  importable ; utilisez `loopbox/service.py` (`loopbox serve`).
- **Empaquetage** : `pip install loopbox` depuis PyPI n'est pas encore publié ;
  installez depuis le dépôt.

## Licence

Apache-2.0 — voir [LICENSE](LICENSE). © 2026 Loopbox Contributors.

## Documentation

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) — tutoriel guidé (anglais)
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) — tutoriel guidé（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — plongée dans les mécanismes internes
- [docs/loopx-integration.md](docs/loopx-integration.md) — correspondance des concepts LoopX
- [vzrunner/README.md](vzrunner/README.md) — assistant `vz` et format des bundles VM
- [SECURITY.md](SECURITY.md) — modèle de menace et politique de divulgation
