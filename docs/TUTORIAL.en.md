# Loopbox Step-by-Step Tutorial: Build a Self-Iterating Local Sandbox Loop from Scratch

> Aimed at macOS on Apple Silicon (M1–M5). The whole tour takes about 15 minutes.
> What you'll end up with: a locally running E2B-compatible sandbox system, plus a
> long-running LoopX-driven loop that "self-checks → self-thinks → self-iterates"
> and waits for your confirmation at the critical points.

---

## Step 0: Prepare the environment (one-time)

You need: macOS 13+ (14+ for the VM backend), Apple Silicon, Python 3.10+, Git.

```bash
# 1. Install the GitHub CLI and sign in (used to create repositories)
brew install gh
gh auth login

# 2. Install LoopX (the control plane driving the loop's heartbeat/quota/gates)
python3 -m pip install --upgrade loopx
# If `loopx` is not on PATH after the pip install, use the official no-clone installer:
# curl -fsSL https://huangruiteng.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor   # you're good once you see ok: True
```

## Step 1: Get the Loopbox source and install it

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
python3 -m pip install -e .
loopbox doctor
```

`loopbox doctor` runs a series of self-checks and reports each one; everything
should be `[ ok ]`:

| Check | Meaning |
| --- | --- |
| arm64 architecture | Apple Silicon is required (any of M1–M5) |
| macOS version | 13+ (the `vz` backend needs 14+) |
| sandbox-exec available | macOS's built-in Seatbelt sandbox tool |
| APFS clonefile support | the copy-on-write capability snapshots/forks rely on |
| vzrunner helper | optional Swift helper for the VM backend |
| seatbelt smoke test | actually runs `echo ok` inside a sandbox |

## Step 2: Five minutes with sandboxes

```bash
# Create a sandbox (prints its id, e.g. sbx_9f2c41ab07d1)
SID=$(loopbox new)

# Run a command inside the sandbox (a string goes through /bin/zsh -lc,
# so pipes and redirection work)
loopbox exec $SID -- zsh -lc 'echo hello > note.txt && cat note.txt'

# Verify isolation: writes outside the sandbox are denied by Seatbelt
loopbox exec $SID -- zsh -lc 'echo hack > ~/escape.txt'   # fails
loopbox exec $SID -- zsh -lc 'cat ~/.ssh/id_ed25519'      # always denied

# Snapshot (APFS copy-on-write, O(1) cost)
loopbox snapshot $SID --name v1

# Fork an identical sandbox from the snapshot
TWIN=$(loopbox fork $SID --snapshot v1)

# Pause / resume (SIGSTOP/SIGCONT the whole process group)
loopbox pause $SID && loopbox resume $SID

# Clean up
loopbox rm $TWIN --purge
loopbox rm $SID --purge
```

Python SDK (same shape as the E2B SDK):

```python
from loopbox import Sandbox

sbx = Sandbox.create(template="seatbelt")        # template maps to a backend
r = sbx.commands.run("echo hello && uname -m")   # shell semantics
sbx.files.write("notes/a.txt", "hi")             # relative to the workspace root
sbx.pause()                                      # = E2B beta_pause
twin = sbx.fork()                                # fork
sbx.resume()
sbx.kill()
```

## Step 3: Start the E2B-compatible HTTP service

```bash
loopbox serve --port 31885 &
# A token is auto-generated at ~/.loopbox/auth.json (mode 0600)
KEY=$(python3 -c "import json;print(json.load(open('$HOME/.loopbox/auth.json'))['token'])")

curl -X POST localhost:31885/sandboxes -H "X-API-Key: $KEY" -d '{"templateID":"seatbelt"}'
curl -X POST localhost:31885/sandboxes/<sandboxID>/exec -H "X-API-Key: $KEY" \
     -d '{"command":"echo via-http"}'
```

The route shapes match E2B (`POST /sandboxes`, `/exec`, `/pause`, `/resume`,
`/fork`, `/snapshots`, `GET|PUT /files`), and the auth header is E2B's own
`X-API-Key` — point any existing E2B client at localhost by changing its base
URL.

## Step 4: Run Codex / Claude Code / DSH inside a sandbox

First check which harness CLIs are installed, then launch one inside a sandbox.
The invocation form is `loopbox harness run <sandbox_id> <name> -- [argv...]`;
everything after `--` is passed to the harness verbatim:

```bash
SID=$(loopbox new)

loopbox harness list                     # detection status of known CLIs
loopbox harness describe codex           # notes + launch examples for one harness
loopbox harness doctor                   # what's installed + integration guidance

loopbox harness run $SID codex -- exec --sandbox workspace-write "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
```

The harness process starts with cwd = the sandbox workspace, and the whole
process is confined by Seatbelt: it can read the toolchain and reach the
network (default `outbound`), but it **can only write inside the sandbox
workspace**, and it can never read `~/.ssh`, keychains, or browser cookies.

Add `--interactive` before the sandbox id to spawn the harness detached as a
long-running process instead of running it to completion.

**Human-in-the-loop while the agent explores**: open a second terminal —

```bash
loopbox ls                 # find the harness sandbox id
loopbox pause $SID         # pause the whole agent session (it freezes)
loopbox snapshot $SID --name before-risky-step
loopbox fork $SID --snapshot before-risky-step   # fork to explore another path
loopbox resume $SID        # the main line continues
```

This is "pause → fork → resume, any time".

## Step 5: Connect LoopX and let the loop run long-term

```bash
cd loopbox
loopx connect
loopx status
```

If it reports there is no goal state yet, create one with the guided flow:

```bash
loopx start-goal --guided --project . --goal-text \
  "Continuously harden and extend Loopbox per GOAL.md: vz backend, PTY streaming, templates, CI."
```

Then drive the loop from whichever agent you prefer:

| Host | How to drive the loop |
| --- | --- |
| Codex CLI | Start `codex` at the project root, have it run `loopx doctor` and use `$loopx <task>` or the loopx entry under `/skills`; keep the cycle going with `/goal <thin task_body>` |
| Claude Code | With the LoopX adapter installed, use `/loopx <task>` + `/loop` |
| DSH | Start `dsh` and paste it the "Connect this repo to LoopX" prompt above |
| This repo's built-in engine | `loopbox loop` runs the "self-check → plan → execute → verify" cycle directly |

With the built-in engine the concrete flow is:

```bash
loopbox loop new --goal "create hello.txt containing 'hi' and verify it" --sandbox seatbelt
loopbox loop run <loop_id>                 # runs until done, or exits 3: blocked on a gate
loopbox loop status <loop_id>              # goal, quota, todos, pending gate
loopbox loop history <loop_id>             # every executed step and its result
```

`loopbox loop run` exit codes: `0` goal met, `1` failed, `2` stopped by
budget/interrupt, `3` blocked on a pending gate (resume with `run` again after
answering the gate).

LoopX makes the loop **sustainable**: quota decides whether the next tick
should run at all, `scheduler_hint` decides the backoff, and
goals/gates/evidence persist across sessions — switching harnesses loses no
state.

## Step 6: Human-in-the-Loop (where you participate)

The loop will **not** decide on its own in the following situations. Instead it
writes `~/.loopbox/loops/<loop_id>/GATE.md` (machine-readable form:
`gate.json` in the same directory) — or a LoopX user gate — and waits for you:

1. **Plan approval** — before a big move it lays out the plan for your review;
2. **High-risk operations** — loosening the Seatbelt profile, deleting data,
   publishing to the outside;
3. **Insufficient evidence** — contradictory tests, ambiguous requirements.

You only need to answer the gate's question — edit `GATE.md`/`gate.json`, or
reply from the CLI — and the loop continues:

```bash
loopbox loop status <loop_id>                                  # see the PENDING GATE line
loopbox loop approve <loop_id> --note "plan looks good"        # approve it
loopbox loop reject <loop_id> --reason "wrong approach"        # or reject (marks the loop failed)
loopbox loop steer <loop_id> --note "run: make test"           # steer with a note; `run: <cmd>` enqueues a sandbox command
loopbox loop run <loop_id>                                     # resume
```

## Step 7: Verify the loop is running correctly

- `loopx doctor` is all green;
- `loopx status` shows the current goal, the current user gate, and the next
  agent todo;
- `python3 -m pytest tests/` and `LOOPBOX_INTEGRATION=1 python3 -m pytest tests/`
  both pass;
- `loopbox doctor` passes all checks;
- the repo contains English-only code/comments, and `.loopx/`, `.loopbox/`,
  `.local/` are not committed.

## FAQ

- **`vzrunner not built`**: optional. When you need VM-grade isolation, run
  `cd vzrunner && ./build.sh` (requires Xcode CLT and macOS 14+; the script
  ad-hoc signs the virtualization entitlement automatically).
- **Warnings from the login shell**: `zsh -l` inside the sandbox loads your
  `.zprofile`; any writes it attempts outside the sandbox are intercepted by
  Seatbelt and print warnings, without affecting the command itself. Use
  `["/bin/zsh","-c",...]` (no `-l`) to avoid this entirely.
- **HTTP 401**: make sure the request header carries the current token from
  `~/.loopbox/auth.json`.
