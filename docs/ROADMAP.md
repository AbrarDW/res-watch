# res-watch roadmap: pending work

> **Progress (on PR #2):**
> - Done: 1.1 CI · 2.1 Group by · 2.2 no 3D on small phones · 2.3 Windows product names · 3.1 history backfill · 3.2 keep the 3D camera · 3.3 fairer totals (PSS option; the overlap note stays) · 3.4 design images and real screenshots
> - Left: 1.2 manual hardware checks on the owner's machines

Status as of PR #1 (merged into `master`): the app, the setup wizard and the login are built and covered by 64 tests, but several things have only been tested on Linux in a cloud container without a GPU or a Docker registry. This file lists what is left, in the order we plan to do it. Each item says why it matters, how to do it, which files it touches and when it counts as done.

The original design is in [`PLAN.md`](PLAN.md).

Workflow: every item goes on its own branch and is published through a pull request into `master`.

---

## Phase 1: verification (do first)

These turn "written but never run" into "known to work". CI (1.1) covers most of the others automatically, so it comes first.

### 1.1 GitHub Actions CI on every pull request
**Why:** with PR-based publishing, nothing currently checks a PR before merge. CI is also the cheapest way to cover macOS, Windows and Docker, none of which this project has been tested on.

**How:** add `.github/workflows/ci.yml` with these jobs:
- `test`: matrix `ubuntu-latest`, `macos-latest`, `windows-latest` × Python 3.10 and 3.13; `pip install -r requirements-dev.txt`, then `pytest`. Linux-only tests already skip themselves elsewhere (`percore.SUPPORTED`). Expect to fix some Windows path and permission assumptions in `tests/test_auth.py` (the 0600 check is already POSIX-only) and `tests/test_setup.py`.
- `setup-scripts`:
  - On Linux and macOS, run `./setup.sh --yes --where local --os <os> --start manual --config-dir $RUNNER_TEMP/cfg` and check that it exits 0 and prints `[5/5]`.
  - On Windows, run `setup.ps1` the same way under `pwsh` and `powershell`. **This is the first real run of `setup.ps1`.**
- `docker`: generate the files (`python -m reswatch setup --yes --where local --start docker`), `docker build`, `docker run -d --pid=host -p 127.0.0.1:8000:8000 -e RESWATCH_AUTH=off`, then `curl /healthz` and check that `/api/processes` lists a host process (for example the runner's own `python`).
- `proxy-configs`: generate each proxy config with the wizard and syntax-check it in the official container. This catches template mistakes that string-matching tests miss.
  - `nginx -t` with a dummy certificate
  - `caddy validate`
  - `apachectl configtest`
  - `traefik` loading the dynamic file
- `frontend`: `node --check static/*.js`, plus a Playwright smoke test. Start the server, select a process, and on every tab assert that there are no console errors and that the charts have one series per selection. Base it on the throwaway scripts used during development.

**Files:** `.github/workflows/ci.yml`, `tests/e2e/smoke.spec.js` (new), small test fixes.

**Done when:** all jobs are green on a PR, and branch protection on `master` requires them (the repo owner turns that on in GitHub settings).

### 1.2 Real-hardware checks (manual, needs the owner's machines)
| Check | How | Expected |
|---|---|---|
| NVIDIA GPU | `./setup.sh` on a machine with a driver (installs `nvidia-ml-py`), run a CUDA workload, select it | GPU tab shows per-device gauges and engine lanes; the process row and charts show GPU % and VRAM |
| AMD GPU | ROCm 6+ with the `amdsmi` Python package | Device utilisation and VRAM appear; per-process figures if the driver exposes them. Fix `reswatch/gpu.py` `AmdGpu` against the real API; it was written from documentation only |
| macOS autostart | `./setup.sh` → local → autostart → enable | LaunchAgent loads, app reachable after logout/login |
| Windows autostart | `setup.ps1` → local → autostart | Task "res-watch" exists and starts at logon; `pythonw` means no console window |
| VPS + proxy | real domain, `--where vps --proxy caddy` (and one of nginx/Apache) | HTTPS works, login works, live data streams through the proxy (WebSocket upgrade), sub-path `--base-path /reswatch` works |

**Done when:** each row is ticked off in the PR that fixes whatever it finds.

---

## Phase 2: features from the plan or mockups that aren't built

### 2.1 "Group by" in the filters popover
**Why:** the mockup (`design/ref-filters.png`) shows it, but the app always groups by the automatic app detection.

**How:**
- Add `group_by` to the filters, with three options:
  - `app` (default): today's behaviour
  - `exe`: normalised executable name only, no cgroup or tree merging
  - `user`
- The grouping happens client-side in `aggregateApps()` (`static/app.js`). The backend already sends what's needed, except a separate "exe" key: add `exe_key` (`apps.base_key`) to `Collector.process_list()`.
- Selecting a group must still stream correctly. The backend resolves `app:` subscriptions by `p["app"]`, so either:
  - (a) add `kind: "exe"` and `kind: "user"` subscriptions in `Collector.resolve_pids`, or
  - (b) send the explicit PID list.
  Prefer (a), so the group follows processes that start and exit.

**Files:** `static/app.js`, `reswatch/monitor.py`, `reswatch/main.py` (accept the new kinds), `tests/test_monitor.py`.

**Done when:** switching "Group by" changes the Apps list immediately and persists across reloads, and selecting a user or exe group charts its combined usage.

### 2.2 Hide 3D on small phones
**Why:** the plan said to turn off the 3D view below 480px wide, because WebGL on small phones is slow and hard to use.

**How:** in `renderMain()`, when `innerWidth < 480`, hide the 3D button and fall back from `cpuView === '3d'` to the heatmap (and from GPU 3D to 2D). Re-check on `resize`.

**Done when:** the Playwright test at 390px wide shows no 3D option.

### 2.3 Friendly app names on Windows
**Why:** apps are named after their executable (`msedge`, `code`). The plan wanted the product name from the file's version info ("Microsoft Edge").

**How:** on Windows only, read `FileDescription`/`ProductName` with `ctypes` (`GetFileVersionInfoW` + `VerQueryValueW`, so no `pywin32` dependency). Cache it per exe path, and use it as the display name while keeping the normalised exe as the key.

**Files:** `reswatch/apps.py` (`resolve_app` / a new `windows_product_name()`), test with a mocked reader.

**Done when:** on Windows the Apps list shows product names. CI on `windows-latest` covers the reader against a system exe such as `notepad.exe`.

---

## Phase 3: polish and rough edges

### 3.1 Keep chart history across reloads and new selections
**Why:** history lives only in the browser, so a reload or a newly ticked item starts with empty charts.

**How:**
- Keep a server-side ring buffer: the last 60 samples per process, holding only the compact fields `cpu`, `rss`, `ior`, `iow`, `gpu`, `gmem` (around 300 processes × 60 × ~6 numbers is small).
- Per-core history is only kept for watched PIDs, so a new selection's per-core history still starts empty unless we keep `PerCoreTracker` running for the top N CPU users. Decide during implementation; the simple version is to backfill everything except per-core.
- On `subscribe`, send a `backfill` message for the new entities. App history is the sum of the members' buffers.

**Files:** `reswatch/monitor.py` (buffer), `reswatch/main.py` (backfill message), `static/app.js` (`addSel` merges the backfill), tests.

**Done when:** after ticking a process that has been running for a while, the CPU and memory charts show the last 60 samples straight away.

### 3.2 Keep the 3D camera when the chart updates
**Why:** the whole view is rebuilt when an app gains or loses a process (to update the chip labels), and that resets the 3D camera.

**How:** split `renderMain()` so the label-only update re-renders just the selection bar and legends, not the chart containers. Or read `grid3D.viewControl` (alpha, beta, distance) before rebuilding and apply it again afterwards.

**Done when:** rotating the 3D view and then launching a new browser tab (which adds a process to the `chrome` app) keeps the camera angle.

### 3.3 Fairer totals
**Why:** an app's memory is the sum of its members' RSS (shared pages counted more than once), and a process selected together with its own app is counted twice in the stacked CPU charts.

**How:**
- Memory: on Linux, offer PSS (`memory_full_info().pss`, which splits shared pages fairly) as an "accurate memory" option, sampled only for watched processes because it's expensive. Label clearly which one is shown.
- Double counting: when a process and its app are both selected, subtract the process from the app's stack segment and show it as a sub-segment. Or keep the current note. Decide when doing it; the note may be enough.

**Done when:** the Details tab can show PSS for an app on Linux, and the overlap case is either fixed or still clearly explained.

### 3.4 Refresh the design images and the README screenshot
**Why:**
- `design/ref-*.png` still show the old "Focus"/"Compare" controls, and the mockup's filter popover shows "Group by" before it exists.
- The README's main image is the mockup with made-up data.

**How:**
- Update `design/mockup.html` to match the current UI.
- Re-render the images with the Playwright script, which belongs in the repo as `design/render.js`.
- Add real screenshots of the running app (overview, CPU per core with two selections, the Apps sidebar) under `docs/screenshots/` and use them in the README.
- Do this after 2.1, so "Group by" exists.

**Done when:** the README shows real screenshots and the design images match the app.

---

## Suggested order

1. **1.1 CI.** It unblocks confidence for everything else, and it's the first run of `setup.ps1` and the Docker image.
2. **1.2 hardware checks.** Done in parallel by the owner as machines are available.
3. **3.4 + 2.2.** Small and visible.
4. **2.1 Group by**, then **3.4 screenshots** again if needed.
5. **3.1 history**, then **3.2 camera**.
6. **2.3 Windows names**, **3.3 fairer totals**.
