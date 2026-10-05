"""Capture blog/LinkedIn material from a running Paperclip — screenshots, a time-lapse GIF and a data export.

Read-only: it only GETs from the Paperclip API and looks at pages. It never imports, resets or edits anything,
so it is safe to point at the Paperclip instance you actually run SDIS in. (`record_demo.py` is the opposite:
it builds a throwaway company to film the scripted 17-second README GIF.)

    pip install playwright pillow && python -m playwright install chromium

    python scripts/capture_blog.py                          # screenshots + data of the current state
    python scripts/capture_blog.py --timelapse 180 --run-schema-watch
        # press Schema watch for you, then grab a frame every 2s for 3 min → GIF of the agents working
    python scripts/capture_blog.py --approve                # also click Approve on a waiting SEV1/SEV2 fix

Output (git-ignored): blog-assets/<timestamp>/
    01-dashboard.png 02-org.png 03-agents.png … task-*.png   1440×900 @2x, ready for a blog
    timelapse.gif / timelapse.mp4 (if ffmpeg is installed)
    paperclip-export.json   company, agents (no env/secrets), routines, tasks with comments, costs
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
NAME = "Schema Drift Immune System"
W, H = 1440, 900
TASK_PREFIXES = ["Diagnose drift", "Remediate", "Notify", "Verify & close", "Schema watch"]
SIDEBAR = [("01-dashboard", "Dashboard"), ("03-agents", "Agents"), ("04-tasks", "Tasks"),
           ("05-routines", "Routines"), ("06-inbox", "Inbox"), ("07-costs", "Costs"), ("08-audit", "Audit")]


# --------------------------------------------------------------------------- API (read-only)
class Api:
    def __init__(self, base: str, token: str | None):
        self.base, self.s = base.rstrip("/"), requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str):
        r = self.s.get(f"{self.base}/api{path}", timeout=30)
        r.raise_for_status()
        return r.json()

    def try_get(self, path: str):
        try:
            return self.get(path)
        except (requests.RequestException, ValueError):
            return None

    def post(self, path: str, body: dict | None = None):
        r = self.s.post(f"{self.base}/api{path}", json=body or {}, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}


def _items(v, key):
    return v.get(key, v) if isinstance(v, dict) else (v or [])


def company(api: Api) -> dict:
    c = next((c for c in _items(api.get("/companies"), "companies") if c["name"].startswith(NAME)), None)
    if not c:
        raise SystemExit(f"No '{NAME}' company in Paperclip — import it first (see docs/GUIDE.md)")
    return c


def issues(api: Api, cid: str) -> list[dict]:
    return _items(api.get(f"/companies/{cid}/issues"), "issues")


def _scrub(agent: dict) -> dict:
    """Agents carry env (paths, emails) in adapterConfig — never export it."""
    a = {k: v for k, v in agent.items() if k not in ("adapterConfig", "apiKey", "secrets")}
    cfg = agent.get("adapterConfig") or {}
    a["adapterConfig"] = {k: v for k, v in cfg.items() if k in ("command", "args", "model")}
    return a


def export(api: Api, c: dict, out: Path) -> dict:
    cid = c["id"]
    tasks = []
    for i in issues(api, cid):
        full = api.try_get(f"/issues/{i['id']}") or i  # list responses truncate descriptions
        comments = _items(api.try_get(f"/issues/{i['id']}/comments"), "comments")
        tasks.append({**full, "comments": comments})
    data = {
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "company": {k: c.get(k) for k in ("id", "name", "issuePrefix", "budgetMonthlyCents", "spentMonthlyCents")},
        "agents": [_scrub(a) for a in _items(api.get(f"/companies/{cid}/agents"), "agents")],
        "routines": _items(api.try_get(f"/companies/{cid}/routines"), "routines"),
        "tasks": tasks,
        "costs": api.try_get(f"/companies/{cid}/costs/summary") or api.try_get(f"/companies/{cid}/costs"),
    }
    (out / "paperclip-export.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return data


def run_schema_watch(api: Api, cid: str) -> None:
    r = next((x for x in _items(api.get(f"/companies/{cid}/routines"), "routines") if x["title"] == "Schema watch"),
             None)
    if not r:
        raise SystemExit("Routine 'Schema watch' not found — run scripts/paperclip_setup.py first")
    api.post(f"/routines/{r['id']}/run")


# --------------------------------------------------------------------------- browser
async def shoot(base: str, c: dict, out: Path, timelapse: int, every: float, page_for_lapse: str,
                approve: bool, chrome: str | None, api: Api) -> list[Path]:
    from playwright.async_api import async_playwright
    prefix, shots = c["issuePrefix"], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chrome) if chrome else await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2,
                                        color_scheme="light")
        page = await ctx.new_page()

        async def save(name: str, full: bool = False):
            await page.wait_for_timeout(600)
            path = out / f"{name}.png"
            await page.screenshot(path=str(path), full_page=full)
            shots.append(path)
            print(f"  📸 {path.name}")

        async def sidebar(label: str) -> bool:
            link = page.get_by_role("link", name=label, exact=True)
            if not await link.count():
                return False
            await link.first.click()
            await page.wait_for_load_state("networkidle")
            return True

        await page.goto(f"{base}/{prefix}/dashboard", wait_until="networkidle")
        # ---- the org chart (hierarchy, live status dots, budget badges)
        await page.goto(f"{base}/{prefix}/org", wait_until="networkidle")
        await save("02-org")
        # ---- every top-level page
        for name, label in SIDEBAR:
            if await sidebar(label):
                await save(name)
        # ---- each SDIS task: description, decision trail, comments (full page)
        if await sidebar("Tasks"):
            rows = page.locator("[data-issue-row-id]")
            titles = [t.strip() for t in await rows.all_inner_texts()]
            n = 0
            for tp in TASK_PREFIXES:
                for idx, t in enumerate(titles):
                    if tp in t and n < 12:
                        n += 1
                        await rows.nth(idx).click()
                        await page.wait_for_load_state("networkidle")
                        slug = re.sub(r"[^a-z0-9]+", "-", tp.lower()).strip("-")
                        if approve and tp == "Remediate":
                            btn = page.get_by_role("button", name="Approve")
                            if await btn.count():
                                await btn.first.scroll_into_view_if_needed()
                                await save(f"task-{n:02d}-{slug}-awaiting-approval")
                                await btn.first.click()
                                print("  ✅ approved the waiting fix")
                        await save(f"task-{n:02d}-{slug}", full=True)
                        await sidebar("Tasks")
                        rows = page.locator("[data-issue-row-id]")
        # ---- time-lapse of the agents working
        if timelapse:
            frames = out / "frames"
            frames.mkdir(exist_ok=True)
            await page.goto(f"{base}/{prefix}/{page_for_lapse}", wait_until="networkidle")
            end, k = time.time() + timelapse, 0
            print(f"  🎞️  time-lapse: one frame every {every:g}s for {timelapse}s on /{page_for_lapse}")
            while time.time() < end:
                await page.screenshot(path=str(frames / f"f{k:04d}.png"))
                k += 1
                await asyncio.sleep(every)
                if k % 10 == 0:  # some views don't live-update every widget; refresh gently
                    await page.reload(wait_until="networkidle")
        await ctx.close()
        await browser.close()
    return shots


def make_gif(frames: Path, out: Path, fps: float) -> None:
    from PIL import Image
    files = sorted(frames.glob("f*.png"))
    if not files:
        return
    imgs = [Image.open(f).convert("RGB").resize((960, 600), Image.LANCZOS) for f in files]
    pal = [i.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for i in imgs]
    pal[0].save(out / "timelapse.gif", save_all=True, append_images=pal[1:], duration=int(1000 / fps), loop=0,
                optimize=True)
    print(f"  🎞️  {out / 'timelapse.gif'}  ({len(files)} frames)")
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames / "f%04d.png"),
                        "-vf", "scale=1440:-2", "-pix_fmt", "yuv420p", str(out / "timelapse.mp4")], check=False)
        print(f"  🎬 {out / 'timelapse.mp4'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("PAPERCLIP_URL", "http://localhost:3100"))
    ap.add_argument("--token", default=os.environ.get("PAPERCLIP_TOKEN"))
    ap.add_argument("--out", default=str(ROOT / "blog-assets"))
    ap.add_argument("--timelapse", type=int, default=0, metavar="SECONDS", help="also record a time-lapse GIF")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between time-lapse frames")
    ap.add_argument("--fps", type=float, default=4.0, help="playback speed of the GIF")
    ap.add_argument("--page", default="org", help="page to time-lapse: org | dashboard | issues | agents")
    ap.add_argument("--run-schema-watch", action="store_true", help="press Run on the Schema watch routine first")
    ap.add_argument("--approve", action="store_true", help="click Approve on a waiting remediation")
    ap.add_argument("--chrome", default=os.environ.get("CHROME_PATH"), help="use an installed Chrome/Edge binary")
    ap.add_argument("--no-browser", action="store_true", help="data export only")
    a = ap.parse_args()

    api = Api(a.url, a.token)
    c = company(api)
    out = Path(a.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"Capturing '{c['name']}' from {a.url} → {out}")
    if a.run_schema_watch:
        run_schema_watch(api, c["id"])
        print("  ▶️  Schema watch started")
    if not a.no_browser:
        asyncio.run(shoot(a.url, c, out, a.timelapse, a.every, a.page, a.approve, a.chrome, api))
        if a.timelapse:
            make_gif(out / "frames", out, a.fps)
    data = export(api, c, out)
    print(f"  🗂️  paperclip-export.json  ({len(data['agents'])} agents, {len(data['tasks'])} tasks)")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
