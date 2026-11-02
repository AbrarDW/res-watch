# res-watch — per-process resource monitor (Python backend + Web UI)

## Context
The repo `res-watch` is empty (no commits on `claude/happy-feynman-eet6hn`). The user wants an app that lists the processes running on the machine, lets them select one or more, and shows live visual charts of the resources each selected process uses — including **CPU usage per core** and **GPU usage** — with 2D and 3D visualisations. Backend must be Python; frontend a web UI.

## Step 0 — Reference images (first, before any backend code)
Plan mode blocks file creation, so the reference images are the first thing done after approval:
1. Build `design/mockup.html` — a static, high-fidelity version of the real UI using the same ECharts / ECharts-GL libraries and `style.css` tokens, filled with realistic fake data (16 cores, 1 GPU, processes `python`, `chrome`, `code`, …).
2. Render it with the pre-installed headless Chromium (Playwright) at 1440×900 and 390×844 into PNGs:
   - `design/ref-overview.png` — Overview tab, light theme
   - `design/ref-cpu-bars.png` — CPU per core, bars + ghost system bars
   - `design/ref-cpu-heatmap.png` — core × time heatmap
   - `design/ref-cpu-3d.png` — ECharts-GL 3D bar chart
   - `design/ref-gpu.png` — GPU tab (gauges, engine lanes, per-process memory)
   - `design/ref-overview-dark.png` — Overview, dark theme
   - `design/ref-mobile.png` — mobile layout
   - `design/ref-empty.png` — empty state
3. Send the PNGs to you for review and **pause for feedback**; adjust the mockup until approved, then build the real app to match (mockup HTML/CSS becomes the starting point for `static/`).

**Status:** Step 0 done. Mockup and 8 reference PNGs are pushed to `design/`. The user asked for an **app-wise view and filters** next (section below). **First action after approval (Step 0b):** update `design/mockup.html` with the Apps/Processes/Tree toggle, the filter button and chips, app focus chips (`▣ chrome ×23`), and the app Details tab. Then re-render all references, since the sidebar changes in every image, and add four new ones:
- `design/ref-apps.png`: Apps view with `chrome` expanded and selected
- `design/ref-filters.png`: filter popover open
- `design/ref-tree.png`: Tree view
- `design/ref-app-details.png`: Details tab for `chrome` (member breakdown bars and memory treemap)

Then send the images and pause for feedback before writing the backend.

## App-wise view & filters (new)

### What an "app" is (`reswatch/apps.py`)
Processes are grouped into apps by a key resolved in this order (first hit wins):
1. **Linux cgroup / systemd scope.** `/proc/<pid>/cgroup` → `app-gnome-firefox-1234.scope` / `snap.slack.slack…` / `flatpak-org.x.y-…` → `firefox`, `slack`, `org.x.y`. This is how GNOME/KDE group apps.
2. **macOS app bundle.** `exe` contains `/X.app/` → `X` (so all `Google Chrome Helper (Renderer)` processes → `Google Chrome`).
3. **Windows.** exe basename without `.exe`, with the product name from the version info when available.
4. **Process-tree fallback.** Walk up the parents while they have the same exe basename, and use the top-most one's name. This merges `chrome --type=renderer` children under `chrome`.
5. **Name normalisation.** Lower-case the name, then strip `-helper`, ` helper (...)`, version suffixes (`python3.12` → `python`), and `kworker/3:1` → `kworker`. Kernel threads go into a `kernel` group.

The function is `group_processes(procs) -> {app_key: {name, icon_hint, pids[], user(s)}}`. It is cached per PID; a new PID gets resolved once.

### Aggregation
- `sample_app(app_key)` is the sum over the member PIDs of CPU %, the per-core array, RSS (unique), I/O bps, GPU util and GPU mem. It also returns `process_count` and a per-member breakdown (top 10 by CPU).
- The member set is recomputed every tick, so newly spawned helpers are included automatically. The app is "exited" only when it has 0 members.
- The WS subscribe message accepts `{"subscribe": [{"kind":"pid","id":9134}, {"kind":"app","id":"chrome"}], ...}`, so apps and single processes can be mixed in the same charts.
- New endpoint: `GET /api/apps` returns `[{key, name, process_count, cpu_percent, memory_rss, gpu_util, gpu_mem, users}]`.

### Filters (applied on the frontend to both views, persisted in `localStorage`)
- Search: name, PID, app, or command line.
- **User:** All / Mine / root & system. The default is "Mine".
- **Hide kernel threads and system services:** on by default.
- **Uses GPU only.**
- **Min CPU %:** slider, 0–50.
- **Status:** running / sleeping / zombie.
- An active-filter chip row under the search box, with ✕ on each chip and a "Clear all" link.

### UI changes
- A segmented toggle at the top of the sidebar: **[Apps | Processes | Tree]**.
  - *Apps:* one row per app: icon letter-avatar, name, process count badge, total CPU %, mem, GPU %. Click ▸ to expand the member processes indented beneath it. Each member can be selected individually, or the app checkbox selects the whole app.
  - *Processes:* the current flat table.
  - *Tree:* parent/child indentation (useful for seeing which helpers belong to what).
- Selected apps appear as focus chips with a stacked-squares icon and an `×N` process count, e.g. `▣ chrome ×23`.
- In the charts, an app series is drawn as one line, the same as a process. The Details tab for an app shows a **member breakdown**: a horizontal bar of CPU/mem per member process, and a treemap of memory by member.
- The CPU-per-core tab for an app shows the summed per-core usage, with an optional "split by member" stacked-bar mode.

### Mockup — Apps view (sidebar)
```
┌ PROCESSES ─────────────────────────────┐
│ [▣ Apps] [ Processes ] [ Tree ]        │
│ 🔍 [search name, PID, app, cmd…]  [⚙ Filters 2] │
│ (User: Mine ✕) (Hide system ✕)  Clear all │
│                                        │
│ ☐ APP              #   CPU%  Mem  GPU% │
│ ☑ ▾ (C) chrome     23  34.2 2.8G   4   │
│ │  ☑ 4821 renderer      18.1 410M   –  │
│ │  ☑ 4790 gpu-process    9.4 220M   4  │
│ │  ☑ 4702 chrome (main)  4.2 380M   –  │
│ │  … 20 more                           │
│ ☑ ▸ (P) python      2 121.0 3.1G  68   │
│ ☐ ▸ (V) code       14   8.7 1.6G   0   │
│ ☐ ▸ (S) slack       6   1.2 540M   0   │
│ ☐ ▸ (D) docker      4   0.4 140M   –   │
└────────────────────────────────────────┘
```

### Mockup — Filters popover
```
┌ Filters ─────────────────────────┐
│ User     (●Mine) (○All) (○System)│
│ ☑ Hide kernel threads & services │
│ ☐ Only processes using the GPU   │
│ Min CPU  [━━●──────────] 2%      │
│ Status   ☑ running ☑ sleeping ☐ zombie │
│                [Reset] [Done]    │
└──────────────────────────────────┘
```

### Mockup — Details tab for an app
```
│ ▣ chrome ×23   CPU 34.2%   Mem 2.8 GB   GPU 4%   Threads 312        │
│ ┌ CPU by member process ───────────┐ ┌ Memory by member (treemap) ─┐│
│ │ renderer 4821  ███████████ 18.1% │ │┌────────┬──────┬───┐        ││
│ │ gpu-proc 4790  ██████ 9.4%       │ ││renderer│ main │gpu│        ││
│ │ main 4702      ███ 4.2%          │ ││ 410M   │ 380M │220│        ││
│ │ 20 others      █ 2.5%            │ │└────────┴──────┴───┘        ││
│ └──────────────────────────────────┘ └─────────────────────────────┘│
```

### Extra files
- `reswatch/apps.py`: grouping + aggregation.
- `tests/test_apps.py`: name normalisation cases (chrome helpers, `python3.12`, `kworker/3:1`), cgroup parsing samples, and tree fallback using a fake parent map, plus a live check that spawning 3 children of the test process puts them in one group.

## Deployment & setup wizard (new)

**Goal:** one generic project that runs on a laptop or a VPS. The repo ships templates for every supported setup. An interactive **setup script** asks the user the questions below *at install time* and generates only the files that match their answers. No setup is assumed when building the app.

### Entry points
- `./setup.sh` (Linux/macOS) and `setup.ps1` (Windows) are thin wrappers. Each one:
  1. checks for Python ≥ 3.10,
  2. creates `.venv`,
  3. runs `pip install -r requirements.txt` (and adds `requirements-gpu.txt` when `nvidia-smi` is found),
  4. runs `python -m reswatch setup`.
- `python -m reswatch setup` is the wizard itself: pure Python stdlib (`input`, `getpass`, `platform`), so it behaves the same on every OS.
- **Non-interactive mode** for automation and CI: every question has a flag, e.g. `python -m reswatch setup --where vps --run compose --proxy caddy --https have --auth builtin --yes`. Answers can also come from `--answers answers.toml`. Unanswered questions fall back to prompts, or to errors when `--yes` is set.
- Re-running the wizard shows the previous answers (saved in the config file) as defaults. Before overwriting a generated file it shows a diff and asks.

### Wizard flow (exactly as specified)
**Q0. Where will you run it?** Local machine only / VPS or remote server / Both.

**If Local only**, it asks nothing else about deployment:
- It keeps `127.0.0.1:8000` and no login. No auth setup, no proxy configs, no `deploy/` folder.
- **Q-L1. OS:** Linux / macOS / Windows. This is auto-detected and offered as the default.
- **Q-L2. How should it start?**
  - *Run manually*: prints `python -m reswatch`.
  - *Start automatically at login*: writes a systemd user unit, a macOS LaunchAgent, or a Windows Task Scheduler task, depending on the OS.
  - *Docker*: writes a Dockerfile and the `docker run` command.

**If VPS or Both:**
- **Q-V1. How will it run on the server?** plain systemd service / Docker (`docker run`) / Docker Compose / just a terminal or tmux for now
- **Q-V2. What sits in front of it?** nothing (SSH tunnel only) / Caddy / nginx / Traefik / Apache / Cloudflare Tunnel or Access / other
  - "other" prints the generic requirements: proxy HTTP and WebSocket upgrade for `/ws`, and set `X-Forwarded-*`.
- **Q-V3. HTTPS and a domain:** already set up / cover it in the generated docs.
  - If a proxy was chosen, it also asks for the domain and an optional sub-path, which becomes `--base-path`.
- **Q-V4. Login:** built-in login on / my proxy or SSO handles it.
  - The second option sets `--no-auth`, shows the warning, and adds `--trusted-proxy`.
- If the built-in login is on, the wizard runs `set-password` inline.
- **Both** additionally asks the Local questions (Q-L1, Q-L2) for the laptop side.

### What the wizard writes
| Answer | Output |
|---|---|
| always | `~/.config/reswatch/config.toml` (host, port, base_path, trusted_proxy, auth on/off, interval), plus a printed "next steps" summary |
| Local + autostart + Linux | `~/.config/systemd/user/reswatch.service` + `systemctl --user enable --now` (after asking) |
| Local + autostart + macOS | `~/Library/LaunchAgents/dev.reswatch.plist` + `launchctl load` (after asking) |
| Local + autostart + Windows | `schtasks /Create … /SC ONLOGON`, and also writes `deploy/reswatch-task.xml` for manual import |
| Docker | `Dockerfile` (+ `docker-compose.yml` if Compose), with `--pid=host` / `pid: host` and the NVIDIA runtime lines only if a GPU was detected |
| VPS + systemd | `deploy/reswatch.service` (dedicated `reswatch` user, `ProtectSystem=strict`, `Restart=on-failure`), plus install commands |
| VPS + tmux | no files; prints the command |
| Caddy / nginx / Traefik / Apache | `deploy/<proxy>.conf` (or Traefik labels in compose): HTTPS, WebSocket upgrade for `/ws`, forwarded headers, sub-path if set |
| Cloudflare | `deploy/cloudflared.yml` + an Access note |
| SSH tunnel only | no files; prints `ssh -L 8000:127.0.0.1:8000 user@server` |
| HTTPS "cover it" | adds a certificate/DNS section to the printed summary (Caddy auto-TLS / certbot for nginx/Apache / Traefik ACME) |

- Templates live in `reswatch/templates/` and use `string.Template`, so there are no extra dependencies.
- Every generated file starts with a `# generated by reswatch setup — re-run to change` header.

### Server options (always)
- `--host` / `--port` flags, defaulting to `127.0.0.1:8000`, with matching env vars `RESWATCH_HOST` / `RESWATCH_PORT`.
- Priority order: CLI flags > env vars > config.toml > defaults.
- `--base-path` (env var `RESWATCH_BASE_PATH`) serves the app under a sub-path. The frontend builds all URLs relative to it.
- The GPU tab shows "GPU metrics unavailable" cleanly when no GPU or driver is found.

### Login (`reswatch/auth.py`), used when the login is on (VPS/Both)
- **Account:** a single admin. Login is off by default when bound to `127.0.0.1`.
- **Safety check at startup:** on a non-localhost host with no password configured, the server refuses to start with a clear error. `--no-auth` overrides this and prints a warning.
- **Login page and sessions:**
  - `/login` is styled with `static/style.css`.
  - A signed session cookie (HMAC-SHA256 with `secret_key`) is set HttpOnly, SameSite=Strict, and Secure over HTTPS.
  - A logout button sits in the top bar.
  - Sessions last 12 h, configurable with `--session-hours`.
- **Protected routes:** every `/api/*` route and the `/ws` WebSocket handshake reject requests without a valid session (401 / close 4401).
- **Brute-force protection:** failed logins are rate-limited per IP. After 5 failures the lockout grows: 30 s, then 1 m, 2 m, 4 m, up to a 15 m cap.
- **Proxy headers:** `X-Forwarded-For` / `-Proto` are honoured only with `--trusted-proxy`.
- **Password storage:** salted `hashlib.scrypt` (n=2¹⁴, r=8, p=1), never the plain password. Credentials come from:
  - `~/.config/reswatch/auth.json`, written with mode 0600 and containing username, hash, salt/params and secret_key, **or**
  - the env vars `RESWATCH_USERNAME`, `RESWATCH_PASSWORD_HASH`, `RESWATCH_SECRET_KEY`.
- **CLI:**
  - `python -m reswatch set-password` prompts with getpass and writes the file.
  - `python -m reswatch hash-password` prints a hash for use in env vars.

### README
- "Quick start" is always present: `./setup.sh` or `setup.ps1` → wizard.
- "Running locally" is always present.
- "Running on a VPS" covers:
  - SSH tunnel
  - systemd / Docker / Compose
  - each proxy
  - setting the password
  - the firewall (`ufw allow 443`, never expose 8000)
- The README documents all options generically. The wizard output points to the relevant section.

### Extra files
- `reswatch/setup_wizard.py`
- `reswatch/config.py`: layered config loading
- `reswatch/auth.py`
- `reswatch/templates/*`
- `setup.sh`, `setup.ps1`
- `tests/test_auth.py`
- `tests/test_setup.py`

### Tests
- **`test_auth.py`:**
  - hash/verify round-trip; a wrong password is rejected
  - `/api` and `/ws` return 401 without a session, and work with one
  - the rate limiter kicks in after 5 failures
  - the server refuses to start on `0.0.0.0` with no password unless `--no-auth` is given
  - forwarded headers are ignored without `--trusted-proxy`
- **`test_setup.py`:** for each answer set (local+manual, local+autostart per OS, vps+systemd+caddy, vps+compose+traefik, vps+tmux+ssh), it runs the wizard non-interactively in a temp dir and asserts that **only** the expected files exist, contain the right host/port/base-path, and that compose/proxy files include the WebSocket and `--pid=host` lines.

## Stack
- **Backend:** Python 3.10+, **FastAPI** + **uvicorn**, **psutil** (CPU/mem/IO), **nvidia-ml-py** (`pynvml`, NVIDIA GPUs, optional), **amdsmi**/`rocm-smi` fallback for AMD (optional, best-effort).
- **Live updates:** WebSocket (`/ws`) pushing samples every 1 s (configurable) for the selected PIDs.
- **Frontend:** plain HTML/CSS/JS served by FastAPI (no build step).
  - **Apache ECharts 5** (cdnjs, pinned) — 2D: line charts, per-core heatmap, per-core bar grid, gauges.
  - **ECharts-GL 2** (jsdelivr, pinned) — 3D: rotatable 3D bar chart "core × time × usage" and 3D GPU surface.
  - One library family for both 2D and 3D keeps theming/colour consistent. Light/dark via `prefers-color-scheme`.

## Files to create
```
res-watch/
├── README.md                 # install, run, GPU notes, permissions
├── requirements.txt          # fastapi, uvicorn[standard], psutil
├── requirements-gpu.txt      # nvidia-ml-py (optional)
├── .gitignore
├── reswatch/
│   ├── __init__.py
│   ├── __main__.py           # `python -m reswatch` → uvicorn, --host/--port/--interval
│   ├── main.py               # FastAPI app, REST routes, websocket, static mount
│   ├── monitor.py            # process list + per-process sampling (incl. per-core)
│   ├── percore.py            # per-process per-core CPU attribution (Linux /proc threads)
│   └── gpu.py                # GPU backend abstraction: NVML → AMD → none
├── static/
│   ├── index.html
│   ├── app.js                # state, WS client, process table, selection
│   ├── charts.js             # ECharts/ECharts-GL chart builders (2D + 3D)
│   └── style.css
└── tests/
    ├── test_monitor.py       # own PID listed, sample fields, gone PID handling
    ├── test_percore.py       # attribution sums ≈ process cpu %, burn-loop lands on a core
    └── test_gpu.py           # no-GPU path returns {"available": false} without raising
```

## Backend design

### `monitor.py`
- `list_processes()` → `[{pid, name, username, cpu_percent, memory_rss, memory_percent, gpu_util?, gpu_mem?, status}]` via `psutil.process_iter`; `AccessDenied` → fields `null`.
- `ProcessSampler` caches `psutil.Process` objects per PID so `cpu_percent(interval=None)` deltas are correct.
- `sample(pid, include_children)` → `{pid, ts, cpu_percent, per_core: [..N floats], memory_rss, memory_percent, num_threads, io_read_bps, io_write_bps, gpu: [{index, util, mem_used}], status}` or `{pid, gone: true}`.
- `system_summary()` → `{cpu_total, per_core: psutil.cpu_percent(percpu=True), freq_per_core, ram_used, ram_total, cores_logical, cores_physical, gpus: [...]}`.

### `percore.py` — per-process CPU per core
- psutil has no per-process per-core usage, so on **Linux** read `/proc/<pid>/task/<tid>/stat` for each thread: `utime+stime` (fields 14–15) and `processor` (field 39, last CPU it ran on).
- Each tick: Δcpu-time of each thread is attributed to its `processor` core → `per_core[i] = Σ Δticks / (CLK_TCK × Δwall) × 100`. Sum equals the process's total CPU % (approximation: a thread migrating mid-interval is credited to its last core — documented in UI tooltip).
- **Windows/macOS:** fall back to `None` for process-per-core; UI shows system per-core plus process total, with a note.

### `gpu.py` — GPU usage
- `GpuBackend.detect()` tries NVML, then AMD SMI, else `NullBackend`.
- System: per-GPU `{index, name, util_gpu, util_mem, mem_used, mem_total, temp, power}`.
- Per process (NVML): `nvmlDeviceGetComputeRunningProcesses`/`GraphicsRunningProcesses` → GPU memory per PID; `nvmlDeviceGetProcessUtilization` → per-PID SM % (since last timestamp). AMD: memory/util per PID where `amdsmi` exposes it.
- GPUs expose no "cores" in a per-core sense via public APIs; the GPU view therefore shows **per-GPU device** (multi-GPU = one lane per device) plus SM / memory-controller / encoder / decoder utilisation lanes (NVML `nvmlDeviceGetUtilizationRates`, `EncoderUtilization`, `DecoderUtilization`).
- No GPU / no driver → `{"available": false, "reason": "..."}`; UI hides GPU panels with a friendly note.

### `main.py` endpoints
- `GET /api/processes` — list (frontend refreshes every 3 s).
- `GET /api/system` — summary incl. core count and GPU inventory.
- `GET /api/process/{pid}` — static details (cmdline, exe, create_time, user).
- `WS /ws` — client sends `{"subscribe":[pids], "children":bool, "interval":1}`; server sends `{"system":{...}, "samples":[...]}` each tick.
- Binds `127.0.0.1:8000` by default (process info is sensitive).

## Frontend design
- **Top bar:** total CPU, RAM, and each GPU as mini gauges; a compact **per-core strip** (one tiny bar per logical core) for the whole system.
- **Left panel:** searchable/sortable process table (CPU, Mem, GPU %, GPU mem), multi-select (≤ 8).
- **Right panel tabs:**
  1. **Overview (2D):** CPU % / Memory / Disk I/O / GPU % line charts, 60 s window, one coloured series per process.
  2. **CPU per core:**
     - *Bars* — current usage per core for the focused process, overlaid on system usage per core (ghost bar).
     - *Heatmap* — x = time (last 60 s), y = core, colour = usage (sequential palette).
     - *3D* — ECharts-GL `bar3D`: x = core, y = time, z = usage; drag to rotate, scroll to zoom, auto-rotate toggle.
  3. **GPU:** per-device gauges, utilisation lanes (SM / Mem / Enc / Dec) over time, per-process GPU memory stacked area, and a 3D surface (time × device-lane × util).
  4. **Details:** stat cards (threads, status, user, uptime, cmdline), exited badge.
- A "focused process" selector (chip row) chooses which process the per-core / GPU views drill into; "Compare" mode shows small multiples side-by-side.
- Controls: include children, normalise CPU by cores, interval (1/2/5 s), pause.

## UI mockup

### Overview tab (desktop, Apps view: 1 app + 1 process selected)
```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ◉ res-watch   CPU ◔ 58%   RAM ◑ 11.2/16G   GPU0 RTX 4070 ◕ 72% 6.1/12G   [1s▾] [⏸] [☾ Theme]      │
│ Cores ▁▃▇▅▂▁█▆▃▂▁▁▄▆▂▁  (16 logical)                                                             │
├────────────────────────────────────┬─────────────────────────────────────────────────────────────┤
│ [▣ Apps] [Processes] [Tree]   48 apps │ Focus: [▣ chrome ×23] [● python 9134]   [Compare] [Clear] │
│ 🔍 [name, PID, app, cmd…] [⚙ Filters 2]│ [Overview] [CPU per core] [GPU] [Details]                 │
│ (User: Mine ✕) (Hide system ✕) Clear all│ ☑ children  ☐ normalise by cores   ■ chrome ■ python      │
│                                    │ ┌─ CPU % ─────────────────────┐ ┌─ GPU % ─────────────────┐ │
│ ☐ APP           #   CPU%  Mem GPU% │ │150┤    ╭──╮   ╭─────╮ ■python│ │100┤ ╭────────╮ ■python  │ │
│ ☑ ▾ (C) chrome  23  34.2 2.8G   4  │ │ 75┤ ╭─╯  ╰───╯     ╰──     │ │ 50┤╭╯        ╰───        │ │
│ │  ☑ 4821 renderer   18.1 410M  –  │ │  0┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ ■chrome │ │  0┼┄┄┄┄┄┄┄┄┄┄┄┄┄ ■chrome │ │
│ │  ☑ 4790 gpu-process 9.4 220M  4  │ └─────────────────────────────┘ └─────────────────────────┘ │
│ │  ☑ 4702 chrome      4.2 380M  –  │ ┌─ Memory (RSS) ──────────────┐ ┌─ Disk I/O KB/s ─────────┐ │
│ │  … 20 more                       │ │3.1G┤━━━━━━━━━━━━━━━━━━━━━   │ │  ┤ ▁▃▅▂▁▂▇▃  read ──     │ │
│ ◐ ▸ (P) python   2 121.0 3.1G  68  │ │2.8G┤┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄   │ │  ┤▁▂ ▁ ▃▁▂▁  write ┄┄    │ │
│ ☐ ▸ (V) code    14   8.7 1.6G   0  │ └─────────────────────────────┘ └─────────────────────────┘ │
│ ☐ ▸ (S) slack    6   1.2 540M   0  │ ┌─ ▣ chrome ×23 ─────────────┐ ┌─ ● python 9134 ──────────┐ │
│ ☐ ▸ (D) docker   4   0.4 140M   –  │ │ CPU 34.2% Mem 2.8G GPU 4%  │ │ CPU 121% Mem 3.1G GPU 68% │ │
│ Sort [CPU▾]   ⟳ 3s                 │ └────────────────────────────┘ └───────────────────────────┘ │
└────────────────────────────────────┴─────────────────────────────────────────────────────────────┘
 ◐ = some members selected (python 9134 picked on its own; its sibling is not)
```

### Processes view (flat; same filters apply)
```
│ [Apps] [▣ Processes] [Tree]  187 shown / 312 │
│ 🔍 [search…]               [⚙ Filters 2]     │
│ (User: Mine ✕) (Hide system ✕)  Clear all    │
│ ☐ PID   Name      App     CPU%  Mem  GPU%    │
│ ☑ 9134 python    python  121.0 3.1G   68     │
│ ☑ 4821 renderer  chrome   18.1 410M    –     │
│ ☐ 1022 code      code      8.7 860M    0     │
```

### Tree view
```
│ [Apps] [Processes] [▣ Tree]                  │
│ ☐ 1     systemd                    0.0  12M  │
│ ☐ ├─ 990  gnome-shell              3.4 310M  │
│ ☑ │  └─ 4702 chrome                4.2 380M  │
│ ☑ │     ├─ 4790 chrome gpu-process 9.4 220M  │
│ ☑ │     └─ 4821 chrome renderer   18.1 410M  │
│ ☐ └─ 612  dockerd                  0.4  88M  │
```

### CPU per core tab (focused: python 9134)
```
│ [Overview] [CPU per core] [GPU] [Details]          View: (●Bars) (○Heatmap) (○3D)           │
│ ┌─ python 9134 — usage per logical core (now) ──────────────────────────────────────────┐   │
│ │100┤          ██                         ██                                             │   │
│ │ 75┤          ██          ░░             ██            ██ = this process                │   │
│ │ 50┤    ░░    ██    ░░    ░░       ░░    ██    ░░      ░░ = whole system (ghost)        │   │
│ │ 25┤ ▄▄ ░░ ▂▂ ██ ░░ ░░ ▂▂ ░░ ░░ ▂▂ ░░ ░░ ██ ░░ ░░ ▂▂                                   │   │
│ │  0┼─C0─C1─C2─C3─C4─C5─C6─C7─C8─C9─C10─C11─C12─C13─C14─C15─                            │   │
│ └───────────────────────────────────────────────────────────────────────────────────────┘   │
│ ┌─ Heatmap: core × last 60 s ───────────────────────────────────────────────────────────┐   │
│ │ C0  ░░▒▒░░░░░░░░▒▒░░░░░░░░░░░░                                                        │   │
│ │ C3  ████████▓▓████████████▓▓██   ← hot core                     □0%  ░  ▒  ▓  █100%   │   │
│ │ C11 ▒▒▓▓████████▓▓▓▓████████▓▓                                                        │   │
│ │ C15 ░░░░░░▒▒░░░░░░░░░░░░▒▒░░░░                                                        │   │
│ │     -60s                    now                                                       │   │
│ └───────────────────────────────────────────────────────────────────────────────────────┘   │
```

### CPU per core — 3D view
```
│ ┌─ 3D: core × time × usage   [⟲ auto-rotate] [reset view]  drag=rotate scroll=zoom ────┐   │
│ │                         z(%)                                                         │   │
│ │                          │      ▌▌                                                   │   │
│ │                          │   ▌▌ ██ ▌         ▌▌                                      │   │
│ │                          │ ▌ ██ ██ █▌   ▌   ███▌                                     │   │
│ │                          │▄█▄██▄██▄██▄▄█▄▄▄████▄▄▄  ╱ time (-60s → now)              │   │
│ │                          └─────────────────────────╱                                 │   │
│ │                           C0 C1 C2 ...      C15  (core)                              │   │
│ └──────────────────────────────────────────────────────────────────────────────────────┘   │
```

### GPU tab
```
│ [Overview] [CPU per core] [GPU] [Details]                         View: (●2D) (○3D)        │
│ ┌─ GPU0 RTX 4070 ────────┐ ┌─ Engine utilisation over time ────────────────────────────┐   │
│ │   ◕ 72% util           │ │ SM   ▃▅▇██▇▇█████▇▆   72%                                  │   │
│ │ Mem 6.1 / 12 GB        │ │ MEM  ▂▃▄▅▅▄▅▅▆▆▅▅▄    41%                                  │   │
│ │ 64°C   142 W           │ │ ENC  ▁▁▁▁▁▁▁▁▁▁▁▁▁     0%                                  │   │
│ └────────────────────────┘ │ DEC  ▁▁▂▂▁▁▁▁▁▁▁▁▁     3%                                  │   │
│                            └────────────────────────────────────────────────────────────┘   │
│ ┌─ Per-process GPU memory (stacked) ───────────┐ ┌─ Per-process GPU util % ─────────────┐   │
│ │6G┤▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ python 4.8G              │ │ python  ████████████████▌   68%      │   │
│ │  ┤▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ chrome 0.3G              │ │ chrome  █▌                   4%      │   │
│ │0 ┼──────────────────                         │ │ other   ███                 10%      │   │
│ └──────────────────────────────────────────────┘ └──────────────────────────────────────┘   │
│  No GPU detected?  →  "GPU metrics unavailable (no NVIDIA/AMD driver found)" card instead  │
```

### Empty / exited / mobile
```
Empty:   ┌──────────────────────────────────────────────┐
         │  📊 No process selected                      │
         │  Tick processes on the left to see live CPU, │
         │  per-core, GPU, memory and disk charts.      │
         └──────────────────────────────────────────────┘
Exited:  ┌─ ● python PID 9134 ─── [ EXITED 12:41:07 ] [Remove] ┐  series stops, card greyed
Mobile:  ┌──────────────────────────────┐
         │ ◉ res-watch  CPU58% GPU72%   │
         │ [Processes] [Charts]         │ ← tabs swap the two panels;
         │ ☑ 9134 python 121%  68%      │   chart tabs become a scrollable chip row,
         │ ☑ 4821 chrome  34%   4%      │   3D view disabled < 480px (2D heatmap shown)
         └──────────────────────────────┘
```

### Visual style
- Clean dashboard: neutral background, bordered cards, 8px radius, system font stack.
- Colour tokens on `:root` with dark-mode overrides; fixed 8-colour categorical palette per selected process (table dot = chart series = card).
- Sequential single-hue palette for heatmap/3D usage (light → saturated); gauges amber > 70 %, red > 90 %.
- 🔒 + "n/a" for processes without permission; "–" for no GPU usage.

## Notes / edge cases
- `AccessDenied` (other users' processes) → "n/a"; README explains running with sudo/admin for full visibility.
- Per-process per-core is Linux-only (thread `processor` field); other OSes show system per-core + process total.
- Per-process GPU util needs NVIDIA driver with NVML; inside containers GPU PIDs may not map to host PIDs — documented.
- Per-process disk I/O unavailable on macOS → chart hidden when null.
- CPU % can exceed 100 % on multi-core; "normalise by cores" toggle divides by logical core count.
- Heavy 3D chart: throttle redraw to the sample interval and keep ≤ 60 time slices.

## Verification
1. `pip install -r requirements.txt` (+ `requirements-gpu.txt` on NVIDIA machines), `python -m reswatch`, open `http://127.0.0.1:8000`.
2. `pytest` — monitor, per-core attribution, no-GPU path.
3. Run a single-thread burn loop (`python -c "while True: pass"`), select it: total CPU ≈ 100 %, one core lit in bars/heatmap/3D; `taskset -c 3 …` pins it and core C3 should be the hot one.
3b. App view: start 3 burn-loop children from one parent script. They should appear as one app row with `3` processes and ~300% CPU, and selecting the app should light 3 cores in the per-core views. Kill one child and the count should drop to 2 while the series continues.
3c. Filters: "Mine" hides root processes, "Hide system" hides `kworker`/`systemd`, and the min-CPU slider hides idle rows. The filter state should survive a reload.
4. Multi-thread burn (e.g. `stress --cpu 4` if available) → 4 cores lit.
5. GPU: in this cloud container there is no GPU, so verify the "unavailable" card renders; on an NVIDIA machine, run a CUDA workload and check per-process GPU util/memory.
6. Headless Playwright screenshots of each tab (Overview, per-core Bars/Heatmap/3D, GPU) to confirm rendering and no console errors.
7. Setup wizard:
   - run `python -m reswatch setup` non-interactively for each answer set and check the generated files (`pytest tests/test_setup.py`)
   - run `./setup.sh` once interactively in a clean clone
8. Local: run `python -m reswatch`, open http://localhost:8000, and confirm live data streams with no login prompt.
9. VPS mode:
   - run `set-password`, then `python -m reswatch --host 0.0.0.0`
   - log in with Playwright and confirm the WebSocket streams data only after login
   - log out and confirm `/api` and `/ws` return 401 again
   - check that starting on 0.0.0.0 without a password exits with the error
10. Docker: build the image if Docker is available here and confirm it sees host processes with `--pid=host`. If Docker isn't available here, say so and leave it for you to run.
11. Commit and push to `claude/happy-feynman-eet6hn`.
