"""Screenshots of the real running dashboard for the write-up (script text blurred, so no screenplay content is shown).

    python app.py --scripts "<folder>"      (another terminal)
    python eval/make_screenshots.py --run out

Writes doc/shots/dashboard.png, preview.png and evaluation.png."""
import argparse
import os

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--run", default="out")
ap.add_argument("--browser", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
a = ap.parse_args()
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "doc", "shots")
os.makedirs(OUT, exist_ok=True)
Q = f"run={a.run}&redact=1"
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=a.browser, headless=True)
    ctx = br.new_context(viewport={"width": 1366, "height": 1000}, reduced_motion="reduce", color_scheme="dark")
    page = ctx.new_page()
    page.goto(f"{a.base}/?{Q}#dash")
    page.wait_for_selector("#slate .reel")
    page.wait_for_timeout(1800)
    page.screenshot(path=os.path.join(OUT, "dashboard.png"), clip={"x": 120, "y": 56, "width": 1126, "height": 880})
    page.locator("#storymap").scroll_into_view_if_needed()
    # evaluation + health: the two panels below the story map
    page.wait_for_selector("#evnote")
    box = page.locator("#evnote").bounding_box()
    top = page.evaluate("document.querySelector('#rings').closest('.panel').getBoundingClientRect().top + window.scrollY")
    h = page.evaluate("document.querySelector('#rings').closest('.panel').getBoundingClientRect().height")
    page.screenshot(path=os.path.join(OUT, "evaluation.png"), full_page=True, clip={"x": 120, "y": top - 4, "width": 1126, "height": h + 8})
    page2 = ctx.new_page()
    page2.goto(f"{a.base}/?{Q}#preview")
    page2.wait_for_selector("#ov.on #mscr .rhook", timeout=15000)
    page2.wait_for_timeout(500)
    page2.evaluate("T=Math.min(TOTAL,11); tick(0,true)")
    page2.wait_for_timeout(300)
    mb = page2.locator("#modal").bounding_box()
    page2.screenshot(path=os.path.join(OUT, "preview.png"), clip={"x": mb["x"], "y": mb["y"], "width": mb["width"], "height": min(mb["height"], 620)})
    br.close()
print("screenshots in", OUT)
