# Loopbox

**Sandboxes locais compatíveis com o protocolo E2B para macOS em Apple Silicon (M1–M5).**

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Français](README.fr.md)

O Loopbox permite que harness de agentes de IA (Codex CLI, Claude Code,
DSH / DeepSeek Harness e seus próprios runners) executem trabalho não
confiável dentro de um sandbox real no seu próprio Mac — sem nuvem. Tarefas
do dia a dia usam o sandbox de processos Seatbelt com inicialização
instantânea; quando você precisa de isolamento de máquina inteira, o backend
experimental Virtualization.framework oferece pausa / bifurcação / retomada
de uma VM em execução.

```bash
pip install -e .        # Python 3.10+, macOS 13+, Apple Silicon
loopbox doctor          # autoverificação: arquitetura, Seatbelt, clones APFS, smoke test

SID=$(loopbox new)                        # criar um sandbox
loopbox exec $SID -- echo "hello sandbox" # executar dentro dele
loopbox snapshot $SID --name v1           # snapshot APFS copy-on-write
loopbox fork $SID --snapshot v1           # bifurcar um gêmeo idêntico
loopbox pause $SID && loopbox resume $SID # congelar / continuar
loopbox harness codex                     # iniciar o Codex CLI dentro de um sandbox
loopbox rm $SID --purge
```

## Por que Loopbox

Sandboxes hospedados (como o E2B) são excelentes, mas às vezes o código, as
credenciais e o orçamento de latência precisam ficar na sua própria máquina.
O Loopbox mantém o **protocolo de uso** do E2B (formato do SDK, formato da
API HTTP, semântica de snapshots) executando tudo localmente.

## Recursos

- **Nativo em Apple Silicon** — funciona do M1 ao M5; requer macOS 13+
  (o backend experimental `vz` requer macOS 14+).
- **Dois motores de isolamento**
  - `seatbelt` (padrão): sandbox de processos macOS Seatbelt via
    `sandbox-exec`. Inicialização instantânea, isolamento de escrita por
    escopo; armazenamentos de credenciais (`~/.ssh`, chaveiros, cookies de
    navegador, configurações de CLIs de nuvem) nunca são legíveis; política
    de rede por sandbox (`outbound` / `all` / `deny`).
  - `vz` (experimental): microVM Virtualization.framework via o helper Swift
    `vzrunner` incluído. O estado completo da máquina permite verdadeira
    pausa → snapshot → fork → retomada.
- **Protocolo compatível com E2B** — o SDK Python tem a mesma forma do SDK do
  E2B (`Sandbox.create()`, `commands.run()`, `files.read/write()`,
  `pause()`, `fork()`, `kill()`); a API HTTP usa rotas no estilo E2B e o
  mesmo cabeçalho `X-API-Key`.
- **CLI local completa** — tudo disponível via `loopbox ...`, com `--json`
  em todo lugar para automação.
- **Snapshots de alto desempenho** — clones APFS copy-on-write, O(1) em dados
  inalterados.
- **Pronto para harness de agentes** — `loopbox harness codex|claude|dsh`
  inicia o CLI do agente dentro de um sandbox; `loopbox loop` oferece um
  motor de loop com portões humanos (human-in-the-loop).
- **Zero dependências de runtime** — apenas a biblioteca padrão do Python.

## Executando harness de agentes dentro de um sandbox

```bash
loopbox harness codex                # Codex CLI, isolado, workspace = sandbox
loopbox harness claude               # Claude Code
loopbox harness dsh -- --profile web # DSH / DeepSeek Harness; argumentos após --
```

O processo do harness roda sob o Seatbelt: pode ler seu toolchain e acessar a
rede conforme a política padrão, mas só pode **escrever** dentro do workspace
do sandbox e nunca ler suas credenciais. De outro terminal você pode
`loopbox pause` a sessão inteira, `loopbox snapshot`, `loopbox fork` um ramo
exploratório e `loopbox resume` — o humano permanece no loop.

## Motor de loop e portões humanos

`loopbox loop` executa o ciclo «autoverificação → planejamento → ação →
verificação» sobre um objetivo, com estado durável em `.loopbox/`. Quando o
loop precisa de julgamento humano (aprovar o plano, ação arriscada, evidência
ambígua), ele escreve uma pergunta em `GATE.md` e aguarda a resposta humana
em vez de adivinhar. Combine com o [LoopX](https://github.com/huangruiteng/loopx)
para estado de objetivos entre harness, cotas e heartbeats; veja
[../loopx-integration.md](../loopx-integration.md).

## Modelo de segurança (versão honesta)

- `seatbelt` é um sandbox de **processos** forte: contenção de escrita,
  exclusão de credenciais, bloqueio opcional de rede. Não é uma VM; exploits
  de kernel estão fora do escopo — para isso use o backend `vz`.
- O serviço HTTP vincula-se apenas a `127.0.0.1` por padrão e sempre exige um
  token, a menos que `LOOPBOX_NO_AUTH=1` seja definido (nunca faça isso em
  uma máquina compartilhada).
- O estado dos sandboxes fica em `~/.loopbox` (pode ser alterado com
  `LOOPBOX_HOME`).

## Desenvolvimento

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # testes unitários
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # + testes de integração Seatbelt reais
```

Código e comentários em inglês. A documentação é multilíngue.

## Licença

Apache-2.0
