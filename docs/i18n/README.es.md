# Loopbox

**Sandboxes locales compatibles con el protocolo E2B para macOS en Apple Silicon (M1–M5).**

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

Loopbox permite que los harness de agentes de IA (Codex CLI, Claude Code,
DSH / DeepSeek Harness y tus propios runners) ejecuten trabajo no confiable
dentro de un sandbox real en tu propia Mac, sin nube. Las tareas cotidianas
usan el sandbox de procesos Seatbelt con arranque instantáneo; cuando
necesitas aislamiento de máquina completa, el backend experimental
Virtualization.framework ofrece pausa / bifurcación / reanudación de una VM
en ejecución.

```bash
pip install -e .        # Python 3.10+, macOS 13+, Apple Silicon
loopbox doctor          # autocomprobación: arquitectura, Seatbelt, clones APFS, smoke test

SID=$(loopbox new)                        # crear un sandbox
loopbox exec $SID -- echo "hello sandbox" # ejecutar dentro
loopbox snapshot $SID --name v1           # snapshot APFS copy-on-write
loopbox fork $SID --snapshot v1           # bifurcar un gemelo idéntico
loopbox pause $SID && loopbox resume $SID # congelar / continuar
loopbox harness codex                     # iniciar Codex CLI dentro de un sandbox
loopbox rm $SID --purge
```

## Por qué Loopbox

Los sandboxes alojados (como E2B) son excelentes, pero a veces el código, las
credenciales y el presupuesto de latencia deben permanecer en tu propia
máquina. Loopbox conserva el **protocolo de uso** de E2B (forma del SDK, forma
de la API HTTP, semántica de snapshots) mientras ejecuta todo localmente.

## Características

- **Nativo de Apple Silicon** — funciona de M1 a M5; requiere macOS 13+
  (el backend experimental `vz` requiere macOS 14+).
- **Dos motores de aislamiento**
  - `seatbelt` (predeterminado): sandbox de procesos macOS Seatbelt mediante
    `sandbox-exec`. Arranque instantáneo, aislamiento de escritura por ámbito;
    los almacenes de credenciales (`~/.ssh`, llaveros, cookies del navegador,
    configuraciones de CLI de nube) nunca son legibles; política de red por
    sandbox (`outbound` / `all` / `deny`).
  - `vz` (experimental): microVM de Virtualization.framework mediante el
    helper Swift `vzrunner` incluido. El estado de máquina completo permite
    verdadera pausa → snapshot → fork → reanudación.
- **Protocolo compatible con E2B** — el SDK de Python tiene la misma forma que
  el SDK de E2B (`Sandbox.create()`, `commands.run()`, `files.read/write()`,
  `pause()`, `fork()`, `kill()`); la API HTTP usa rutas al estilo E2B y el
  mismo encabezado `X-API-Key`.
- **CLI local completa** — todo está disponible vía `loopbox ...`, con
  `--json` en todas partes para scripting.
- **Snapshots de alto rendimiento** — clones APFS copy-on-write, O(1) en
  datos sin cambios.
- **Listo para harness de agentes** — `loopbox harness codex|claude|dsh`
  inicia el CLI del agente dentro de un sandbox; `loopbox loop` ofrece un
  motor de bucle con puertas humanas (human-in-the-loop).
- **Cero dependencias en tiempo de ejecución** — solo la biblioteca estándar de Python.

## Ejecutar harness de agentes dentro de un sandbox

```bash
loopbox harness codex                # Codex CLI, aislado, workspace = sandbox
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness; argumentos tras --
```

El proceso del harness corre bajo Seatbelt: puede leer tu toolchain y acceder
a la red según la política predeterminada, pero solo puede **escribir** dentro
del workspace del sandbox y jamás leer tus credenciales. Desde otra terminal
puedes `loopbox pause` toda la sesión, `loopbox snapshot`, `loopbox fork` una
rama exploratoria y `loopbox resume`: el humano permanece en el bucle.

## Motor de bucle y puertas humanas

`loopbox loop` ejecuta el ciclo «autocomprobación → planificación → acción →
verificación» sobre un objetivo, con estado durable en `.loopbox/`. Cuando el
bucle necesita juicio humano (aprobar el plan, acción arriesgada, evidencia
ambigua) escribe una pregunta en `GATE.md` y espera la respuesta humana en
lugar de adivinar. Combínalo con [LoopX](https://github.com/huangruiteng/loopx)
para estado de objetivos entre harness, cuotas y heartbeats; consulta
[../loopx-integration.md](../loopx-integration.md).

## Modelo de seguridad (versión honesta)

- `seatbelt` es un sandbox de **procesos** fuerte: contención de escritura,
  exclusión de credenciales, denegación de red opcional. No es una VM; un
  exploit de kernel está fuera de alcance — para eso usa el backend `vz`.
- El servicio HTTP se vincula solo a `127.0.0.1` de forma predeterminada y
  siempre exige un token salvo que se defina `LOOPBOX_NO_AUTH=1` (nunca lo
  hagas en una máquina compartida).
- El estado del sandbox vive en `~/.loopbox` (se puede sobrescribir con
  `LOOPBOX_HOME`).

## Desarrollo

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # pruebas unitarias
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # + pruebas de integración Seatbelt reales
```

El código y los comentarios están en inglés. La documentación es multilingüe.

## Licencia

Apache-2.0
