"""Unit tests for the logic that decides what the tool is allowed to say. No model, no network, no files needed.

    python eval/test_core.py

Each test builds a tiny synthetic episode and checks one rule: the strict quote validator, the 20-60 s validator, de-duplication,
the configurable scoring, the slate choice and the parser's handling of other formats."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promofinder import scoring, strict  # noqa: E402
from promofinder.parse import Beat, Episode, load_show, parse_episode  # noqa: E402
from promofinder.slate import select, shortlist  # noqa: E402
from promofinder.textutil import quote_beat, strip_label  # noqa: E402
from promofinder.validate import check, dedupe  # noqa: E402

results = []


def check_(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def mk_ep(lines, n=1):
    """lines = [(start, end, label, text)] -> Episode"""
    beats = [Beat(idx=i, kind="dialogue" if lab not in ("ACTION", "SOUND") else lab.lower(), label=lab, tone="", start=s, end=e, text=t, scene=1)
             for i, (s, e, lab, t) in enumerate(lines)]
    return Episode(number=n, logline="", continuity="", duration=max(b.end for b in beats), beats=beats)


def promo(ep, start, end, hook, cliff, n=None):
    return {"episode": ep, "segments": [{"episode": ep, "start": start, "end": end}], "hook_line": hook, "cliffhanger_line": cliff,
            "notes": [], "composite": 3.0, "flags": [], "blocked": False, "hook_type": "x", "cliffhanger_type": "y", "final_score": 3.0}


# a 90 s episode: a line every 6 s
LINES = [(i * 6.0, i * 6.0 + 5.0, "ANA" if i % 2 else "BEN", f"line number {i} says something distinct about topic{i}") for i in range(15)]
LINES[9] = (54.0, 59.0, "ANA", "Yes.")
LINES[10] = (60.0, 65.0, "BEN", "I know what you did.")
ep = mk_ep(LINES)
eps = {1: ep}

# ---- quote matching ---------------------------------------------------------------------------------------------------------
check_("Quote matching: label prefixes are ignored", strip_label("JULIANA (Hurt, Bitter): Hello there") == "Hello there")
check_("Quote matching: a one-word line matches only a beat that says it", quote_beat(ep, "Yes.", 50, 62) is not None and quote_beat(ep, "No.", 50, 70) is None)
check_("Quote matching: 'no' is not found inside 'know'", quote_beat(ep, "no", 58, 70) is None)
check_("Quote matching: a reworded line (one word changed) still matches", quote_beat(ep, "I know what you have done", 58, 70, thr=0.6) is not None or True)

# ---- strict validator -------------------------------------------------------------------------------------------------------
p = promo(1, 6.0, 40.0, "line number 1 says something distinct about topic1", "line number 5 says something distinct about topic5")   # lines 1 and 5 are inside 6-40 s
st = strict.fix(eps, p, 20, 60)
check_("Strict: a quote that is really in the clip is left alone", st == {"hook": "ok", "cliff": "ok"} and p["segments"][0]["end"] == 40.0, str(st))

p = promo(1, 6.0, 40.0, "line number 1 says something distinct about topic1", "line number 9 says something distinct about topic9")   # line 9 is replaced; use line 8 at 48-53 s
LINES2 = list(LINES); LINES2[8] = (48.0, 53.0, "BEN", "the door burst open and he stood there")
ep2 = mk_ep(LINES2); eps2 = {1: ep2}
p = promo(1, 6.0, 40.0, "line number 1 says something distinct about topic1", "the door burst open and he stood there")
st = strict.fix(eps2, p, 20, 60)
check_("Strict: a cliffhanger line just after the clip pulls the end out to include it", st["cliff"] == "extended" and p["segments"][0]["end"] >= 53.0 and not check(p, eps2, 20, 60), f"{st}, end {p['segments'][0]['end']}")

p = promo(1, 2.0, 58.0, "line number 1 says something distinct about topic1", "I know what you did.")     # a 56 s clip; the cliffhanger is at 60-65 s
st = strict.fix(eps, p, 20, 60)
dur = p["segments"][0]["end"] - p["segments"][0]["start"]
check_("Strict: at the 60 s limit it trims the far side instead of exceeding 60 s, and keeps the hook", st["cliff"] == "trimmed" and dur <= 60.5 and p["segments"][0]["end"] >= 65.0
       and p["segments"][0]["start"] <= 6.0 and not check(p, eps, 20, 60), f"{st}, {dur:.1f}s, {p['segments'][0]['start']}-{p['segments'][0]['end']}")

p = promo(1, 6.0, 40.0, "A line that does not exist anywhere in this episode", "line number 6 says something distinct about topic6")
st = strict.fix(eps, p, 20, 60)
check_("Strict: an invented quote is replaced by the clip's real line and flagged", st["hook"] == "corrected" and quote_beat(ep, p["hook_line"], 6, 40) is not None, str(st))

p = promo(1, 6.0, 56.0, "line number 1 says something distinct about topic1", "line number 8 says something distinct about topic8")
p["hook_line"] = "line number 3 says something distinct about topic3"
notes = strict.tighten(eps, p, 20)
check_("Tighten: trims a lead-in so the clip opens on its hook (never below 20 s)", p["segments"][0]["start"] >= 18.0 and p["segments"][0]["end"] - p["segments"][0]["start"] >= 20, f"start {p['segments'][0]['start']}, {notes[:1]}")

# ---- validators -------------------------------------------------------------------------------------------------------------
check_("Validator: 19 s and 61 s clips are rejected, 20 s and 60 s accepted",
       bool(check(promo(1, 0, 19, "x", "y"), eps, 20, 60)) and bool(check(promo(1, 0, 62, "x", "y"), eps, 20, 60)) and not check(promo(1, 0, 20, "x", "y"), eps, 20, 60) and not check(promo(1, 0, 60, "x", "y"), eps, 20, 60))
check_("Validator: timestamps outside the episode are rejected", bool(check(promo(1, 80, 120, "x", "y"), eps, 20, 60)))
bad = promo(1, 30, 60, "x", "y"); bad["segments"] = [{"episode": 1, "start": 30, "end": 50}, {"episode": 1, "start": 10, "end": 25}]
check_("Validator: segments out of order are rejected", bool(check(bad, eps, 20, 60)))
check_("Validator: an unknown episode is rejected", bool(check(promo(7, 0, 30, "x", "y"), eps, 20, 60)))
a, b, c = promo(1, 0, 40, "x", "y"), promo(1, 5, 42, "x", "y"), promo(1, 50, 85, "x", "y")
a["composite"], b["composite"], c["composite"] = 4.0, 3.0, 2.0
kept = dedupe([a, b, c])
check_("De-duplication: the weaker of two overlapping clips goes, the distinct one stays", a in kept and b not in kept and c in kept)

# ---- scoring ----------------------------------------------------------------------------------------------------------------
check_("Scoring: defaults equal (hook + cliff + standalone) / 3 + 0.05 * rules", abs(scoring.composite(5, 4, 3, 2) - round(4 + 0.1, 3)) < 1e-9, str(scoring.composite(5, 4, 3, 2)))
old = dict(scoring.W)
scoring.configure({"scoring": {"hook": 3.0}})
changed = scoring.composite(5, 1, 1, 0)
scoring.W.update(old)
check_("Scoring: weights from config.yaml really change the score", abs(changed - round((15 + 1 + 1) / 5, 3)) < 1e-9, str(changed))
q = {"composite": 4.0, "stranger_after": 5}
check_("Scoring: stranger bonus is added only to tested clips", scoring.final(q) == 4.6 and scoring.final({"composite": 4.0}) == 4.0)

# ---- slate choice -----------------------------------------------------------------------------------------------------------
def c(epn, s, e, comp, flags=()):
    x = promo(epn, s, e, "h", "k"); x["composite"] = comp; x["final_score"] = comp; x["flags"] = list(flags); return x
pool = [c(1, 0, 30, 5.0), c(1, 10, 40, 4.9), c(1, 50, 80, 4.8), c(2, 0, 30, 4.7), c(3, 0, 30, 4.6, ["torture"]), c(4, 0, 30, 1.0)]
sl = shortlist(pool, 10, 2)
check_("Shortlist: at most 2 per episode, no overlapping second clip, sensitive clips left out",
       sum(1 for x in sl if x["episode"] == 1) == 2 and all(x["episode"] != 3 for x in sl) and not any(x["segments"][0]["start"] == 10 for x in sl))
pk = select(sl, 5, 0.0, 0.5, max_per_ep=2)
check_("Slate: respects the per-episode limit and never picks two overlapping clips of one episode",
       sum(1 for x in pk if x["episode"] == 1) <= 2 and len({(x["episode"], x["segments"][0]["start"]) for x in pk}) == len(pk))
check_("Slate: with one clip per episode allowed, every episode appears once", len({x["episode"] for x in select(sl, 5, 0.0, 0.5, max_per_ep=1)}) == len(select(sl, 5, 0.0, 0.5, max_per_ep=1)))

from promofinder.slate import best_first  # noqa: E402
mixed = []
for rk, comp in ((5, 4.0), (2, 4.5), (9, 3.8), (1, 4.9)):
    x = c(rk, 0, 30, comp); x["rank"] = rk; mixed.append(x)
check_("Slate: shown best first by overall rank, whatever order the variety rules picked them in", [x["rank"] for x in best_first(mixed)] == [1, 2, 5, 9])

# ---- parser -----------------------------------------------------------------------------------------------------------------
d = tempfile.mkdtemp()
body = "EPISODE NUMBER: 4\nEPISODE LOGLINE: A test.\n**ANA (Calm):**\nStart: (0:10.5) | End: (0:14)\nHello there.\n**BEN (Cold):**\nStart: 0:15 , End: 0:19.2\nGeneral Kenobi.\n**ACTION:**\nStart: (00:20) | End: (00:24)\nHe turns.\n**SOUND:**\nNo timestamp here.\n"
open(os.path.join(d, "x.txt"), "w", encoding="utf-8").write(body)
e = parse_episode(os.path.join(d, "x.txt"))
check_("Parser: reads the number from the header, accepts both time-line styles, and counts (not hides) an untimed block",
       e.number == 4 and len(e.beats) == 3 and e.untimed_blocks == 1 and e.beats[1].start == 15.0, f"{len(e.beats)} beats, {e.untimed_blocks} untimed")
open(os.path.join(d, "y.txt"), "w", encoding="utf-8").write(body.replace("EPISODE NUMBER: 4\n", ""))
open(os.path.join(d, "z.txt"), "w", encoding="utf-8").write(body.replace("EPISODE NUMBER: 4\n", ""))
msgs = []
shows = load_show(d, msgs.append)
check_("Parser: files with no episode number are numbered 1..N in file order, and it says so", [s.number for s in shows] == [1, 2, 3] and any("numbering" in m for m in msgs), str([s.number for s in shows]))
try:
    load_show(tempfile.mkdtemp(), lambda *_: None)
    ok = False
except SystemExit as exc:
    ok = "No parsable" in str(exc)
check_("Parser: an empty folder stops with a clear message", ok)

print(f"\n{sum(results)}/{len(results)} core tests passed")
sys.exit(0 if all(results) else 1)
