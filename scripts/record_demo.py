"""Record the README / LinkedIn demo: the SDIS company handling the rename incident in Paperclip.

Prerequisites (all free, no accounts):
    PAPERCLIP_HOME=$(mktemp -d) npx paperclipai onboard --yes   # a throwaway Paperclip on :3100
    pip install playwright && playwright install chromium  # or set CHROME=/path/to/chrome
    ffmpeg on PATH

Run:
    python scripts/record_demo.py            # → docs/assets/sdis-demo.gif (17 s) + docs/assets/sdis-demo.mp4

What it does: re-imports the company into Paperclip, switches the agents to demo mode (SDIS_DEMO=rename,
so nothing touches Snowflake, GitHub or Jira), lands the rename scenario, triggers the Schema watch
routine and films the real heartbeats: org chart → task tree → the Diagnostician's decisions → the
approval card (clicked like a human would) → live runs → budgets. Captions are overlaid in the page.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("PAPERCLIP_URL", "http://127.0.0.1:3100").rstrip("/")
API = f"{BASE}/api"
NAME = "Schema Drift Immune System"
W, H = 1280, 800

# Each scene boundary flashes a unique colour in a 16-px corner square. The editor finds boundaries by
# scanning video frames for those colours (wall-clock marks drift from the encoder's timeline by seconds),
# then paints the square white again in the final cut.
MARKER_JS = """(rgb) => {
  let el = document.getElementById('sdis-marker');
  if (!el) {
    el = document.createElement('div'); el.id = 'sdis-marker';
    Object.assign(el.style, {position:'fixed', right:'0', top:'0', width:'16px', height:'16px',
      zIndex: 2147483647, pointerEvents:'none'});
    document.body.appendChild(el);
  }
  el.style.background = rgb;
}"""
SCENE_COLORS = {"intro": (0, 128, 0), "org": (255, 0, 0), "org_end": (0, 255, 0), "tasks": (0, 0, 255), "decisions": (255, 255, 0),
                "approve": (255, 0, 255), "live": (0, 255, 255), "resolved": (255, 128, 0), "end": (128, 0, 255)}

CAPTION_JS = """(t) => {
  let el = document.getElementById('sdis-caption');
  if (!el) {
    el = document.createElement('div'); el.id = 'sdis-caption';
    Object.assign(el.style, {position:'fixed', left:'50%', bottom:'28px', transform:'translateX(-50%)',
      zIndex: 2147483647, background:'rgba(17,24,39,0.92)', color:'#fff', padding:'12px 22px',
      borderRadius:'12px', font:'600 19px/1.35 Inter, system-ui, sans-serif', maxWidth:'1000px',
      textAlign:'center', boxShadow:'0 6px 24px rgba(0,0,0,.25)', pointerEvents:'none'});
    document.body.appendChild(el);
  }
  el.textContent = t;
}"""


# --------------------------------------------------------------------------- Paperclip setup
def _company():
    return next((c for c in requests.get(f"{API}/companies", timeout=30).json() if c["name"].startswith(NAME)), None)


def reset_company(pace: float, state: Path, cli_env: dict[str, str]) -> dict:
    if _company():
        # Paperclip (2026.824) can't delete a company that has budget policies, so record into a throwaway
        # instance instead of reusing one:  PAPERCLIP_HOME=$(mktemp -d) npx paperclipai onboard --yes
        raise SystemExit(f"'{NAME}' already exists in {BASE}; record into a fresh Paperclip instance")
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "company"
        shutil.copytree(ROOT / "company", pkg)
        subprocess.run(["npx", "--yes", "paperclipai", "company", "import", str(pkg), "--target", "new", "--yes",
                        "--paperclip-url", BASE], check=True, capture_output=True, env=cli_env)
    env = {**os.environ, "SDIS_DEMO_STATE": str(state)}
    subprocess.run(["python3", str(ROOT / "scripts" / "paperclip_setup.py"), "--url", BASE, "--activate",
                    "--demo", "rename"], check=True, capture_output=True, env=env)
    c = _company()
    for a in requests.get(f"{API}/companies/{c['id']}/agents", timeout=30).json():
        if a["adapterType"] != "process":  # the Claude manager isn't needed (and has no key) in the demo
            requests.patch(f"{API}/agents/{a['id']}", timeout=30, json={
                "runtimeConfig": {"heartbeat": {"enabled": False, "wakeOnAssignment": False}}}).raise_for_status()
            continue
        cfg = a["adapterConfig"]
        cfg["env"] = {**cfg.get("env", {}), "SDIS_DEMO_PACE": str(pace),
                      "SDIS_TRACE_PATH": str(state.parent / "trace_events.jsonl")}
        requests.patch(f"{API}/agents/{a['id']}", json={"adapterConfig": cfg, "replaceAdapterConfig": True},
                       timeout=30).raise_for_status()
    subprocess.run(["sdis", "demo", "land", "rename"], check=True, capture_output=True, env=env)
    # agents may run as Paperclip's OS user: let them read and write the demo state
    for f in [state.parent, *state.parent.rglob("*")]:
        f.chmod(0o777 if f.is_dir() else 0o666)
    return c


def run_schema_watch(c: dict) -> None:
    r = next(x for x in requests.get(f"{API}/companies/{c['id']}/routines", timeout=30).json()
             if x["title"] == "Schema watch")
    requests.post(f"{API}/routines/{r['id']}/run", json={}, timeout=30).raise_for_status()


def wait_for(pred, timeout=60, every=0.4):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError("demo did not reach the expected state")


def issues(c):
    r = requests.get(f"{API}/companies/{c['id']}/issues", timeout=30).json()
    return r.get("issues", r) if isinstance(r, dict) else r


def issue_like(c, prefix, status=None):
    return next((i for i in issues(c) if i["title"].startswith(prefix) and (status is None or i["status"] == status)),
                None)


# --------------------------------------------------------------------------- filming
async def film(c: dict, out_dir: Path, chrome: str | None) -> tuple[Path, list[tuple[str, float]]]:
    from playwright.async_api import async_playwright
    prefix = c["issuePrefix"]
    marks: list[tuple[str, float]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
        ctx = await browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(out_dir),
                                        record_video_size={"width": W, "height": H})
        page = await ctx.new_page()
        t0 = time.time()

        async def mark(name):
            marks.append((name, time.time() - t0))
            await page.evaluate(MARKER_JS, "rgb({},{},{})".format(*SCENE_COLORS[name]))
            await page.wait_for_timeout(250)

        async def go(path, caption):
            await page.goto(f"{BASE}/{prefix}/{path}", wait_until="networkidle")
            await page.evaluate(CAPTION_JS, caption)

        async def caption(text):
            await page.evaluate(CAPTION_JS, text)

        async def nav(link, wait=700):
            # in-app (client-side) navigation: no reload, no spinner, the caption overlay persists
            await page.get_by_role("link", name=link, exact=True).first.click()
            await page.wait_for_timeout(wait)

        async def open_task(title_prefix, wait=900):
            await nav("Tasks", 500)
            await page.locator("[data-issue-row-id]").filter(has_text=title_prefix).first.click()
            await page.wait_for_timeout(wait)

        # 1 · the org chart; upstream renames CUST_ID and the Sentinel wakes
        await page.goto(f"{BASE}/{prefix}/org", wait_until="networkidle")
        box = await page.locator("svg").filter(has=page.locator("g[transform]")).first.bounding_box()
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.mouse.wheel(0, -100)  # one wheel step = 1.1×: larger cards, all five reports still in frame
        await page.mouse.move(W - 40, H - 40)
        await page.wait_for_timeout(300)
        await caption("SDIS: an autonomous data-reliability company, running in Paperclip")
        await mark("intro")
        await page.wait_for_timeout(1500)
        await mark("org")
        await caption("2 a.m. — upstream renames CUST_ID → CUSTOMER_ID. The Sentinel wakes.")
        await asyncio.to_thread(run_schema_watch, c)
        await asyncio.to_thread(wait_for, lambda: issue_like(c, "Diagnose drift"))
        await caption("The Diagnostician classifies the drift with deterministic rules")
        await asyncio.to_thread(wait_for, lambda: issue_like(c, "Remediate", "blocked"), 90)
        await page.wait_for_timeout(500)
        await mark("org_end")

        # 2 · the task tree the agents built
        await nav("Tasks", 400)
        await caption("Every step is a Paperclip task: diagnose → remediate + notify")
        await page.wait_for_timeout(1800)
        await mark("tasks")

        # 3 · why: the Diagnostician's decision trail
        await page.locator("[data-issue-row-id]").filter(has_text="Diagnose drift on ORDERS").first.click()
        await caption("Rename proven by values (0.998) · 5 marts + 4 dashboards at risk → SEV1, load held")
        await page.wait_for_timeout(2600)
        await mark("decisions")

        # 4 · the human in the loop
        await open_task("Remediate INC-", 300)
        await caption("SEV1 → the Surgeon's one-line fix waits for a human")
        btn = page.get_by_role("button", name="Approve")
        await btn.scroll_into_view_if_needed()
        await page.wait_for_timeout(1500)
        await caption("You approve…")
        await btn.hover()
        await page.wait_for_timeout(500)
        await btn.click()
        await page.wait_for_timeout(500)
        await mark("approve")

        # 5 · live runs
        await nav("See all agents", 300)
        await caption("…the fix merges and the Auditor verifies — live heartbeats")
        await asyncio.to_thread(wait_for, lambda: issue_like(c, "Verify & close", "done"), 90)
        await page.wait_for_timeout(700)
        await mark("live")

        # 6 · resolved + cost
        await nav("Tasks", 400)
        await caption("Resolved: contained in seconds, fixed with one human approval")
        await page.wait_for_timeout(1700)
        await mark("resolved")
        await nav("Costs", 500)
        await caption("5 of 6 agents are plain code: $0 LLM spend against a $25 budget")
        await page.wait_for_timeout(1800)
        await mark("end")

        video = await page.video.path()
        await ctx.close()
        await browser.close()
    return Path(video), marks


# --------------------------------------------------------------------------- editing
def find_marks(video: Path) -> dict[str, float]:
    """Scene boundaries = first frame where the corner square shows that scene's colour."""
    fps = 25
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf",
                          f"fps={fps},crop=8:8:iw-12:4,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    found: dict[str, float] = {}
    for i in range(len(raw) // 3):
        px = raw[3 * i:3 * i + 3]
        for name, rgb in SCENE_COLORS.items():
            if name not in found and all(abs(a - b) < 60 for a, b in zip(px, rgb, strict=True)):
                found[name] = i / fps
    return found if len(found) == len(SCENE_COLORS) else {}


# seconds each scene gets in the final cut (sums to 17.0); waiting is fast-forwarded, reading is not
SCENES = [("org", 2.3), ("org_end", 3.4), ("tasks", 1.9), ("decisions", 2.9), ("approve", 2.3),
          ("live", 1.9), ("resolved", 1.2), ("end", 1.1)]


def edit(video: Path, marks: list[tuple[str, float]], seconds: float, out_gif: Path, out_mp4: Path,
         width: int, fps: int) -> None:
    """Cut the raw take into scenes, retime each to its share of `seconds`, then make the MP4 and GIF."""
    at = find_marks(video) or dict(marks)
    total = sum(d for _, d in SCENES)
    parts, labels, t_prev = [], [], at["intro"]  # start once the org chart and first caption are on screen
    for i, (name, dur) in enumerate(SCENES):
        t_end, target = at[name], dur * seconds / total
        speed = (t_end - t_prev) / target
        parts.append(f"[0:v]trim={t_prev:.3f}:{t_end:.3f},setpts=(PTS-STARTPTS)/{speed:.4f},fps={fps}[s{i}]")
        labels.append(f"[s{i}]")
        t_prev = t_end
    graph = (";".join(parts) + f";{''.join(labels)}concat=n={len(SCENES)}:v=1:a=0,"
             f"drawbox=x=iw-18:y=0:w=18:h=18:color=white:t=fill,scale={width}:-2:flags=lanczos[v]")
    tmp = out_mp4.with_suffix(".tmp.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-filter_complex", graph, "-map", "[v]",
                    "-t", f"{seconds:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "slow", "-crf", "20",
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(out_mp4)
    pal = out_gif.with_suffix(".palette.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_mp4), "-vf",
                    "palettegen=max_colors=128:stats_mode=diff", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_mp4), "-i", str(pal), "-lavfi",
                    "paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", "-loop", "0", str(out_gif)],
                   check=True)
    pal.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=17.0)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--pace", type=float, default=2.5, help="seconds each agent's live run stays visible")
    ap.add_argument("--state", default=str(ROOT / ".sdis-demo" / "record" / "state.pkl"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "assets"))
    a = ap.parse_args()
    state = Path(a.state)
    state.parent.mkdir(parents=True, exist_ok=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    c = reset_company(a.pace, state, dict(os.environ))
    with tempfile.TemporaryDirectory() as tmp:
        video, marks = asyncio.run(film(c, Path(tmp), os.environ.get("CHROME")))
        print(json.dumps({k: round(v, 1) for k, v in marks}))
        raw = state.parent / "sdis-demo.raw.webm"
        shutil.copy(video, raw)
        (state.parent / "marks.json").write_text(json.dumps(marks))
    edit(raw, marks, a.seconds, out / "sdis-demo.gif", out / "sdis-demo.mp4", a.width, a.fps)
    for f in ("sdis-demo.gif", "sdis-demo.mp4"):
        print(f"{out / f}  {(out / f).stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
