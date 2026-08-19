# Loopbox

**Bacs à sable locaux compatibles avec le protocole E2B pour macOS sur Apple Silicon (M1–M5).**

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md)

Loopbox permet aux harness d'agents IA (Codex CLI, Claude Code,
DSH / DeepSeek Harness et vos propres runners) d'exécuter du travail non fiable
dans un vrai bac à sable sur votre propre Mac — sans cloud. Les tâches
quotidiennes utilisent le bac à sable de processus Seatbelt avec un démarrage
instantané ; pour une isolation au niveau machine, le backend expérimental
Virtualization.framework offre une vraie pause / duplication / reprise d'une
VM en cours d'exécution.

```bash
pip install -e .        # Python 3.10+, macOS 13+, Apple Silicon
loopbox doctor          # autovérification : architecture, Seatbelt, clones APFS, smoke test

SID=$(loopbox new)                        # créer un bac à sable
loopbox exec $SID -- echo "hello sandbox" # exécuter à l'intérieur
loopbox snapshot $SID --name v1           # instantané APFS copy-on-write
loopbox fork $SID --snapshot v1           # dupliquer un jumeau identique
loopbox pause $SID && loopbox resume $SID # figer / reprendre
loopbox harness codex                     # lancer Codex CLI dans un bac à sable
loopbox rm $SID --purge
```

## Pourquoi Loopbox

Les bacs à sable hébergés (comme E2B) sont excellents, mais parfois le code,
les identifiants et le budget de latence doivent rester sur votre propre
machine. Loopbox conserve le **protocole d'utilisation** d'E2B (forme du SDK,
forme de l'API HTTP, sémantique des instantanés) tout en exécutant tout
localement.

## Fonctionnalités

- **Natif Apple Silicon** — fonctionne du M1 au M5 ; nécessite macOS 13+
  (le backend expérimental `vz` requiert macOS 14+).
- **Deux moteurs d'isolation**
  - `seatbelt` (par défaut) : bac à sable de processus macOS Seatbelt via
    `sandbox-exec`. Démarrage instantané, isolation des écritures ; les
    magasins d'identifiants (`~/.ssh`, trousseaux, cookies de navigateur,
    configurations des CLI cloud) ne sont jamais lisibles ; politique réseau
    par bac à sable (`outbound` / `all` / `deny`).
  - `vz` (expérimental) : microVM Virtualization.framework via l'assistant
    Swift `vzrunner` inclus. L'état complet de la machine permet une vraie
    pause → instantané → fork → reprise.
- **Protocole compatible E2B** — le SDK Python a la même forme que le SDK E2B
  (`Sandbox.create()`, `commands.run()`, `files.read/write()`, `pause()`,
  `fork()`, `kill()`) ; l'API HTTP utilise des routes de style E2B et le même
  en-tête `X-API-Key`.
- **CLI locale complète** — toutes les fonctionnalités via `loopbox ...`,
  avec `--json` partout pour le scripting.
- **Instantanés haute performance** — clones APFS copy-on-write, O(1) pour
  les données inchangées.
- **Prêt pour les harness d'agents** — `loopbox harness codex|claude|dsh`
  démarre le CLI de l'agent dans un bac à sable ; `loopbox loop` fournit un
  moteur de boucle avec des portes humaines (human-in-the-loop).
- **Zéro dépendance d'exécution** — bibliothèque standard Python uniquement.

## Exécuter des harness d'agents dans un bac à sable

```bash
loopbox harness codex                # Codex CLI, isolé, workspace = bac à sable
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness ; arguments après --
```

Le processus du harness s'exécute sous Seatbelt : il peut lire votre
toolchain et accéder au réseau selon la politique par défaut, mais ne peut
**écrire** que dans le workspace du bac à sable et ne peut jamais lire vos
identifiants. Depuis un autre terminal, vous pouvez `loopbox pause` toute la
session, `loopbox snapshot`, `loopbox fork` une branche exploratoire et
`loopbox resume` — l'humain reste dans la boucle.

## Moteur de boucle et portes humaines

`loopbox loop` exécute le cycle « autovérification → planification → action →
vérification » sur un objectif, avec un état durable dans `.loopbox/`. Quand la
boucle a besoin d'un jugement humain (approbation du plan, action risquée,
preuve ambiguë), elle écrit une question dans `GATE.md` et attend la réponse
humaine au lieu de deviner. Combinez-le avec
[LoopX](https://github.com/huangruiteng/loopx) pour l'état des objectifs entre
harness, les quotas et les heartbeats — voir
[../loopx-integration.md](../loopx-integration.md).

## Modèle de sécurité (version honnête)

- `seatbelt` est un bac à sable de **processus** robuste : confinement des
  écritures, exclusion des identifiants, blocage réseau optionnel. Ce n'est
  pas une VM ; un exploit du noyau est hors de portée — utilisez alors le
  backend `vz`.
- Le service HTTP ne se lie qu'à `127.0.0.1` par défaut et exige toujours un
  jeton, sauf si `LOOPBOX_NO_AUTH=1` est défini (à ne jamais faire sur une
  machine partagée).
- L'état des bacs à sable vit dans `~/.loopbox` (modifiable via
  `LOOPBOX_HOME`).

## Développement

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # tests unitaires
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # + tests d'intégration Seatbelt réels
```

Le code et les commentaires sont en anglais. La documentation est multilingue.

## Licence

Apache-2.0
