"""Make a folder of screenshots (script text blurred) plus a ready prompt, to give to another AI or a person for a UI review.

    python app.py --scripts "<folder>"            (in another terminal)
    python eval/make_review_pack.py --run out

Writes review_pack/: 9 screenshots (desktop, phone-sized, light mode, preview mid-play and at the end), PROMPT.md and
CHECKLIST.md. Every line of script text is blurred (?redact=1), so the pictures show layout and design, not Kuku's content."""
import argparse
import os

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--run", default="out")
ap.add_argument("--browser", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
a = ap.parse_args()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "review_pack")
os.makedirs(OUT, exist_ok=True)
Q = f"run={a.run}&redact=1"

shots = [  # (file, url suffix, width, height, full page, seek seconds)
    ("01_dashboard_desktop", f"?{Q}#dash", 1366, 900, True, None),
    ("02_promos_grid_desktop", f"?{Q}#promos", 1366, 1000, False, None),
    ("03_preview_start", f"?{Q}#preview", 1366, 900, False, 0),
    ("04_preview_playing", f"?{Q}#preview", 1366, 900, False, 9),
    ("05_preview_end_card", f"?{Q}#preview", 1366, 900, False, 999),
    ("06_run_tab_desktop", f"?{Q}#run", 1366, 900, False, None),
    ("07_dashboard_phone_390px", f"?{Q}#dash", 390, 844, True, None),
    ("08_promos_phone_390px", f"?{Q}#promos", 390, 844, False, None),
    ("09_dashboard_light_mode", f"?{Q}&theme=light#dash", 1366, 900, True, None),
]
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=a.browser, headless=True)
    for name, suffix, w, h, full, seek in shots:
        ctx = br.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce", device_scale_factor=1,
                             color_scheme="light" if "theme=light" in suffix else "dark")
        page = ctx.new_page()
        page.goto(a.base + "/" + suffix)
        page.wait_for_timeout(1800)
        if seek is not None:
            page.wait_for_selector("#ov.on #mscr", timeout=15000)
            page.wait_for_timeout(500)
            if seek:
                page.evaluate(f"T=Math.min(TOTAL,{seek}); tick(0,true)")
                page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(OUT, name + ".png"), full_page=full)
        ctx.close()
        print("saved", name, flush=True)
    br.close()

open(os.path.join(OUT, "PROMPT.md"), "w", encoding="utf-8").write("""# Prompt to give another AI (attach the 9 screenshots)

You are a senior product designer and front-end reviewer. The attached screenshots are the web app for **PromoFinder**: a tool that
reads a short-drama show's screenplays and gives an editor a ready-to-post slate of 12 promo clips (20-60 s, each opening on a hook
and ending on a cliffhanger). The lines of script text are blurred on purpose; judge layout and design, not the words.

Who will look at it: an evaluator at a media company who has about 10 minutes and will also run it on a new show.

Please review, in this order, and be specific (point at a screenshot and an element):
1. First 10 seconds: is it clear what this is, what the result is, and what to do next? What is the first thing the eye lands on?
2. Visual hierarchy and consistency across the 9 screenshots (type sizes, spacing, colour use, buttons). What looks unfinished?
3. Readability: contrast, small text, text that is cut off, anything hard to tap on the phone-sized screenshots (7 and 8).
4. Light mode (9) vs the default dark mode: any element that breaks?
5. The video-style preview (3, 4, 5): does it explain the idea of a promo (hook, cliffhanger, "start from Ep 1") at a glance?
6. What is missing for someone deciding whether to trust the result?
7. Give me the TOP 5 fixes, ranked by impact, each with: what to change, why, and how hard it is (S/M/L).

Do not invent features that are not visible. If you are unsure, say so.
""")
open(os.path.join(OUT, "CHECKLIST.md"), "w", encoding="utf-8").write("""# 5-minute hands-on checklist (for a person, or an AI that can use a browser)

Open http://127.0.0.1:8000 (start it with:  python app.py --scripts "<folder>").

1. Dashboard: find the #1 promo of the slate, its episode and its start/end time.            [ ] clear  [ ] confusing
2. Click the phone in the top-right of the hero: does the preview open?                       [ ] yes    [ ] no
3. In the preview press Play, then drag/click to the end. Do you see the "Start from Ep 1" card?  [ ] yes  [ ] no
4. Star two clips. Open the Shortlist tab, type a note, press "Copy brief".                    [ ] works  [ ] broken
5. Promos tab: search for a character name; switch Grid / List; press "CSV".                  [ ] works  [ ] broken
6. Dashboard > "Test it against your own picks": add Episode 18, 00:00 - 00:52 and evaluate.  [ ] works  [ ] broken
7. Run tab: drop 3 episode .txt files, tick "Offline demo", press Run. Does it finish?         [ ] works  [ ] broken
8. Resize the window to phone width. Does anything overflow or overlap?                       [ ] fine   [ ] problems
9. Toggle the sun/moon button in the header.                                                  [ ] fine   [ ] problems

Write down anything that made you hesitate. Those hesitations are the real findings.
""")
print("review pack:", OUT)
