"""Record the 3D office Story to video/raw.webm (720p, headless) and log chapter start times."""
import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", args=["--use-angle=vulkan", "--enable-features=Vulkan", "--enable-gpu", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=str(OUT), record_video_size={"width": 1280, "height": 720})
    page = ctx.new_page()
    t0 = time.time(); caps = []
    page.on("console", lambda m: m.text.startswith("CAP ") and caps.append((round(time.time() - t0, 2), m.text[4:])))
    page.goto("http://localhost:8765/?story")
    page.wait_for_function("document.querySelector('#capTitle').textContent.startsWith('Built with')", timeout=400_000)
    time.sleep(6)
    total = time.time() - t0
    ctx.close(); b.close()
    Path(page.video.path()).rename(OUT / "raw.webm")
(OUT / "caps.json").write_text(json.dumps({"total": total, "caps": caps}, indent=1))
print(total, len(caps))
