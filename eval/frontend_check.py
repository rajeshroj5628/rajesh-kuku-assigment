"""End-to-end check of the web app in a real browser (clicks, downloads, upload and run).

    pip install playwright          (uses your installed Edge/Chrome, no browser download)
    python app.py --scripts "<folder>"            (in another terminal)
    python eval/frontend_check.py --run out --scripts "<folder>"

Prints PASS / FAIL for each feature. The Run test uses the offline mock model (no API calls) and cleans up after itself."""
import argparse
import csv
import glob
import io
import json
import os
import shutil
import sys
import time

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--run", default="out")
ap.add_argument("--scripts", required=True)
ap.add_argument("--browser", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
a = ap.parse_args()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def read_download(dl):
    with open(dl.path(), encoding="utf-8-sig") as f:
        return f.read()


with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=a.browser, headless=True)
    ctx = br.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True, reduced_motion="reduce")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(f"{a.base}/?run={a.run}#dash")
    page.wait_for_selector("#slate .reel", timeout=20000)
    page.wait_for_timeout(800)

    # ---- dashboard --------------------------------------------------------------------------------
    n_reels = page.locator("#slate .reel").count()
    check("Dashboard: slate shows reel cards", n_reels >= 6, f"{n_reels} cards")
    hero = page.locator("#hero .hn").all_inner_texts()
    check("Dashboard: hero stats filled in", len(hero) == 4 and all(h.strip() not in ("", "0") for h in hero), str(hero))
    check("Dashboard: story map chart drawn", page.locator("#storymap svg path").count() >= 2)
    check("Dashboard: trust rings shown", page.locator("#rings .rg").count() == 4)
    status_text = page.locator("#stat").inner_text()
    check("Dashboard: processing status chip", "Idle" in status_text or "Last run" in status_text or "Running" in status_text, status_text)

    # ---- reel preview + player --------------------------------------------------------------------
    page.locator("#slate .reel [data-a=p]").first.click()
    page.wait_for_selector("#ov.on #mscr .rhook", timeout=15000)
    poster = page.locator("#mscr").inner_text()
    check("Preview: opens with the hook shown", "HOOK" in poster, poster.replace("\n", " ")[:60])
    page.locator("#sp").click(); page.locator("#sp").click()                   # speed 4x
    page.locator("#pl").click()
    page.wait_for_timeout(3500)
    w = float(page.evaluate("parseFloat(document.getElementById('pfill').style.width)||0"))
    mid = page.locator("#mscr").inner_text()
    check("Preview: Play advances the progress bar", w > 3, f"{w:.0f}% after 3.5s at 4x")
    check("Preview: phone changes to the spoken lines", mid != poster and "HOOK · FIRST 3 SECONDS" not in mid, mid.replace("\n", " ")[:70])
    page.locator("#pl").click()                                                  # pause
    box = page.locator("#pbar").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2)   # seek to the very end
    page.wait_for_timeout(400)
    end = page.locator("#mscr").inner_text()
    check("Preview: end card with 'start from Ep 1'", "Ep 1" in end and "CONTINUED" in end, end.replace("\n", " ")[:70])
    page.locator("#mstar").click()
    check("Preview: star adds to the shortlist badge", page.locator("#nstar").inner_text() == "1")
    page.keyboard.press("Escape")
    check("Preview: Escape closes it", not page.locator("#ov.on").count())

    # ---- shortlist --------------------------------------------------------------------------------
    page.locator("#nav [data-t=short]").click()
    page.wait_for_selector("#shortlist textarea")
    page.locator("#shortlist textarea").first.fill("add a title card")
    page.reload(); page.wait_for_selector("#shortlist textarea", timeout=20000)      # the page reopens on the tab you were on
    check("Shortlist: star survives a page reload", page.locator("#nstar").inner_text() == "1")
    check("Shortlist: editor note survives a reload", page.locator("#shortlist textarea").first.input_value() == "add a title card")
    with page.expect_download() as d:
        page.locator("#exportcsv").click()
    rows = list(csv.reader(io.StringIO(read_download(d.value))))
    check("Shortlist: CSV export (1 clip + note)", len(rows) == 2 and rows[1][-1] == "add a title card", f"{len(rows) - 1} row(s)")

    # ---- promos tab: grid, search, list, exports --------------------------------------------------
    page.locator("#nav [data-t=promos]").click()
    page.wait_for_selector(".reelgrid .reel")
    total_txt = page.locator("#stats").inner_text()
    total = int(total_txt.split(" of ")[1].split(" ")[0])
    check("Promos: grid of reel cards", page.locator(".reelgrid .reel").count() >= 100, total_txt)
    page.locator("#q").fill("Juliana"); page.wait_for_timeout(300)
    filtered = int(page.locator("#stats").inner_text().split(" of ")[0])
    check("Promos: search filters the list", 0 < filtered < total, f"{filtered} of {total}")
    page.locator("#q").fill("")
    page.wait_for_timeout(300)
    with page.expect_download() as d:
        page.locator("#expcsv").click()
    rows = list(csv.reader(io.StringIO(read_download(d.value))))
    check("Promos: CSV export has every clip (not only the 150 shown)", len(rows) - 1 == total, f"{len(rows) - 1} rows vs {total}")
    with page.expect_download() as d:
        page.locator("#expjson").click()
    js = json.loads(read_download(d.value))
    check("Promos: JSON export has every clip with timestamps", len(js) == total and "segments" in js[0], f"{len(js)} clips")
    page.locator("#vl").click(); page.wait_for_selector("#list .card")
    check("Promos: list view", page.locator("#list .card").count() > 0)
    page.locator("#vg").click()

    # ---- your own picks ---------------------------------------------------------------------------
    page.locator("#nav [data-t=dash]").click(); page.wait_for_selector("#g-add")
    page.locator("#g-ep").fill("18"); page.locator("#g-s").fill("00:00"); page.locator("#g-e").fill("00:52"); page.locator("#g-add").click()
    page.locator("#g-run").click()
    page.wait_for_selector("#g-out table", timeout=20000)
    out = page.locator("#g-out").inner_text()
    check("Your picks: evaluated against the run", "found" in out and "EP" in out.upper() and "RANK" in out.upper(), out.replace("\n", " ")[:60])
    page.locator("#g-clear").click()

    # ---- theme -------------------------------------------------------------------------------------
    before = page.evaluate("document.documentElement.dataset.theme")
    page.locator("#theme").click()
    check("Theme toggle changes the theme", page.evaluate("document.documentElement.dataset.theme") != before)

    # ---- run tab: upload + run (offline mock) ------------------------------------------------------
    page.locator("#nav [data-t=run]").click()
    files = sorted(glob.glob(os.path.join(a.scripts, "**", "*.txt"), recursive=True))[:3]
    page.set_input_files("#files", files)
    flist = page.locator("#flist").inner_text()
    check("Run: uploaded files listed", "3 file" in flist, flist)
    page.locator("#mock").check(); page.locator("#name").fill("pwtest")
    page.locator("#run").click()
    seen_running = False
    t0 = time.time()
    while time.time() - t0 < 150:
        page.evaluate("pollStatus()")                    # refresh the chip now instead of waiting for its 4 s timer
        if "Running" in page.locator("#stat").inner_text():
            seen_running = True
        if "Finished" in page.locator("#msg").inner_text():
            break
        page.wait_for_timeout(120)
    done = "Finished" in page.locator("#msg").inner_text()
    check("Run: upload + Run finishes and shows the log", done and page.locator("#log").inner_text().strip() != "", f"{time.time() - t0:.0f}s")
    check("Run: status chip showed 'Running'", seen_running)
    page.wait_for_timeout(1500)
    sel = page.locator("#run-sel").input_value()
    check("Run: new run opens on the dashboard", "pwtest" in sel and page.locator("#slate .reel").count() > 0, sel)
    # ---- zip upload, file check, malformed input, slate download, evaluation note ---------------------------------
    import tempfile
    import zipfile
    tmpd = tempfile.mkdtemp()
    zp = os.path.join(tmpd, "show.zip")
    with zipfile.ZipFile(zp, "w") as z:
        for f in files:
            z.write(f, "pack/inner/" + os.path.basename(f))
        z.writestr("__MACOSX/._junk.txt", "x")
        z.writestr("notes.pdf", "not a screenplay")
    page.locator("#nav [data-t=run]").click()
    page.locator("#flist .x2").click()                                           # clear the earlier files
    page.set_input_files("#files", [zp])
    page.locator("#check").click()
    page.wait_for_selector("#inspect.ok, #inspect.warn, #inspect.bad", timeout=30000)
    insp = page.locator("#inspect").inner_text()
    check("Zip upload: episodes inside the archive are detected", "Detected 3 episodes" in insp, insp.replace("\n", " ")[:90])
    page.locator("#flist .x2").click()
    bad = os.path.join(tmpd, "garbage.txt")
    open(bad, "w", encoding="utf-8").write("this is not a screenplay\nno blocks, no times\n")
    page.set_input_files("#files", [bad])
    page.locator("#check").click()
    page.wait_for_selector("#inspect.bad", timeout=30000)
    check("Malformed input: clear error instead of a crash", "Could not read these files" in page.locator("#inspect").inner_text(), page.locator("#inspect").inner_text().replace("\n", " ")[:70])
    page.locator("#flist .x2").click()
    page.set_input_files("#files", [os.path.join(tmpd, "show.zip")])
    open(os.path.join(tmpd, "empty.zip"), "wb").write(b"PK\x05\x06" + b"\x00" * 18)
    page.locator("#flist .x2").click()
    page.set_input_files("#files", [os.path.join(tmpd, "empty.zip")])
    page.locator("#check").click()
    page.wait_for_function("document.querySelector('#msg').textContent.length>0 || !document.querySelector('#inspect').hidden", timeout=30000)
    check("Empty archive: refused with a message", "no .txt" in page.locator("#msg").inner_text().lower() or "bad" in page.locator("#inspect").get_attribute("class"),
          page.locator("#msg").inner_text()[:70])
    page.locator("#nav [data-t=dash]").click(); page.wait_for_selector("#dl-slatemd")
    with page.expect_download() as d:
        page.locator("#dl-slatemd").click()
    md = read_download(d.value)
    check("Slate Markdown export (of the run on screen)", md.lstrip().startswith("# Recommended slate") and md.count("Hook:") >= 3, f"{md.count('Hook:')} clips")
    page.wait_for_selector("#evnote")
    check("Evaluation: says the reference sets are development sets, not an independent test", "development sets" in page.locator("#evnote").inner_text())
    check("Overview: processing time, model and validation chips", page.locator("#ovr .ochip").count() >= 3, f"{page.locator('#ovr .ochip').count()} chips")
    real = [e for e in errors if "status of 400" not in e]            # the empty-archive test asks for a 400 on purpose
    check("No JavaScript errors in the console (the deliberate 400 from the bad-upload test excluded)", not real, "; ".join(real[:3]))
    br.close()

# clean up what the test created
for d in glob.glob(os.path.join(ROOT, "runs", "pwtest*")):
    shutil.rmtree(d, ignore_errors=True)
for d in glob.glob(os.path.join(ROOT, "uploads", "*")):
    if os.path.isdir(d) and time.time() - os.path.getmtime(d) < 600:
        shutil.rmtree(d, ignore_errors=True)
bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + (f"  | FAILED: {bad}" if bad else ""))
sys.exit(1 if bad else 0)
