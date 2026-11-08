"""Step 1 - parse a Kuku-style screenplay .txt into timed beats.

Format handled (one file per episode):
    EPISODE NUMBER / EPISODE LOGLINE / CONTINUITY NOTES header
    SCENE NN / Scene heading / Scene start / Scene end
    **TYPE (tone):**   then   Start: (mm:ss.ff) | End: (mm:ss.ff)   then text

Character names are NOT trusted downstream (they are inconsistent in the data),
so we keep the raw speaker label only for display.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field, asdict

TS = r"(\d+):(\d+(?:\.\d+)?)"
BLOCK_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
TIME_RE = re.compile(rf"Start:\s*\(?\s*{TS}\s*\)?\s*[|,;/-]?\s*End:\s*\(?\s*{TS}\s*\)?", re.I)


def to_sec(m: str, s: str) -> float:
    return int(m) * 60 + float(s)


def fmt(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


@dataclass
class Beat:
    idx: int
    kind: str          # camera | action | dialogue | sound
    label: str         # raw label e.g. "LADY JULIANA" / "CAMERA"
    tone: str          # text inside parentheses, if any
    start: float
    end: float
    text: str
    scene: int
    scene_heading: str = ""


@dataclass
class Episode:
    number: int
    logline: str
    continuity: str
    duration: float
    beats: list[Beat] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)  # {n, heading, start, end}
    source: str = ""
    timing: str = "screenplay"      # screenplay | inline | srt | estimated  (how the times were obtained)
    untimed_blocks: int = 0         # script blocks that had no timestamp and were therefore not used

    def to_dict(self):
        d = asdict(self)
        return d


def _kind(label: str) -> str:
    u = label.upper()
    if u.startswith("CAMERA"):
        return "camera"
    if u.startswith("ACTION"):
        return "action"
    if u.startswith("SOUND"):
        return "sound"
    return "dialogue"


def _split_label(label: str) -> tuple[str, str]:
    """'LADY JULIANA (Hurt, Bitter)' -> ('LADY JULIANA', 'Hurt, Bitter').
    Handles multiple groups such as 'MATRIARCH (O.S.) (Firm)'."""
    groups = re.findall(r"\(([^)]*)\)", label)
    name = re.sub(r"\s*\([^)]*\)", "", label).strip()
    return name, ", ".join(groups)


SPEECH_WORDS_PER_SEC = 2.7


def _est(text: str) -> float:
    """Rough on-screen length of a line: speech speed, with a floor. Only used when a script carries no times."""
    return max(1.5, len(text.split()) / SPEECH_WORDS_PER_SEC + 0.6)


def _estimated(blocks: list[tuple[str, str]]) -> list[Beat]:
    out, t = [], 0.0
    for label_full, blob in blocks:
        name, tone = _split_label(label_full)
        d = _est(blob)
        out.append(Beat(idx=len(out), kind=_kind(label_full), label=name, tone=tone, start=round(t, 2), end=round(t + d, 2), text=blob, scene=0))
        t += d + 0.2
    return out


INLINE_RE = re.compile(r"^\s*[\[(]?(\d+):(\d+(?:[.,]\d+)?)[\])]?\s*[-\u2013:]?\s*([A-Za-z][A-Za-z0-9 .'/&\-]{0,40}?)\s*(\([^)]*\))?\s*:\s*(.+)$")


def _inline_timestamps(lines: list[str]) -> list[Beat]:
    """Lines like  [00:12.5] JULIANA (hurt): text   or   01:05 ACTION: text  - each starts at its own time, ends at the next one."""
    rows = []
    for ln in lines:
        m = INLINE_RE.match(ln)
        if m:
            rows.append((to_sec(m.group(1), m.group(2).replace(",", ".")), m.group(3).strip(), (m.group(4) or "").strip("() "), m.group(5).strip()))
    if len(rows) < 3:
        return []
    out = []
    for i, (s, label, tone, text) in enumerate(rows):
        e = rows[i + 1][0] if i + 1 < len(rows) and rows[i + 1][0] > s else s + _est(text)
        out.append(Beat(idx=len(out), kind=_kind(label), label=label, tone=tone, start=s, end=min(e, s + 20.0), text=text, scene=0))
    return out


SRT_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _srt(lines: list[str]) -> list[Beat]:
    """Subtitle files (.srt): a time line  00:00:12,500 --> 00:00:15,000  followed by the text."""
    out, i = [], 0
    while i < len(lines):
        m = SRT_RE.search(lines[i])
        if m:
            g = [int(x) for x in m.groups()]
            s, e = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            j, txt = i + 1, []
            while j < len(lines) and lines[j].strip():
                txt.append(lines[j].strip())
                j += 1
            if txt:
                out.append(Beat(idx=len(out), kind="dialogue", label="", tone="", start=s, end=e, text=" ".join(txt), scene=0))
            i = j
        else:
            i += 1
    return out if len(out) >= 3 else []


def parse_episode(path: str) -> Episode:
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().splitlines()

    base_name = os.path.basename(path)
    num_m = (re.search(r"S\d+E0*(\d+)", base_name, re.I) or re.search(r"episode[-_ ]?0*(\d+)", base_name, re.I)
             or re.search(r"(?:^|[^a-z])ep[-_ ]?0*(\d+)", base_name, re.I))
    number = int(num_m.group(1)) if num_m else 0
    logline = continuity = ""
    scene_n, scene_heading = 0, ""
    scenes: list[dict] = []
    beats: list[Beat] = []
    all_blocks: list[tuple[str, str]] = []     # every **LABEL:** block, timed or not
    untimed = 0

    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if raw.upper().startswith("EPISODE NUMBER:"):
            m = re.search(r"(\d+)", raw)
            if m:
                number = int(m.group(1))
        elif raw.upper().startswith("EPISODE LOGLINE:"):
            logline = raw.split(":", 1)[1].strip()
        elif raw.upper().startswith("CONTINUITY NOTES:"):
            continuity = raw.split(":", 1)[1].strip()
        elif re.match(r"SCENE\s+\d+", raw, re.I):
            scene_n += 1
            scene_heading = ""
            scenes.append({"n": scene_n, "heading": "", "start": None, "end": None})
        elif raw.startswith("Scene heading:") and scenes:
            scene_heading = raw.split(":", 1)[1].strip()
            scenes[-1]["heading"] = scene_heading
        elif raw.startswith("Scene start:") and scenes:
            m = re.search(TS, raw)
            if m:
                scenes[-1]["start"] = to_sec(*m.groups())
        elif raw.startswith("Scene end:") and scenes:
            m = re.search(TS, raw)
            if m:
                scenes[-1]["end"] = to_sec(*m.groups())
        else:
            bm = BLOCK_RE.match(raw)
            if bm:
                label_full = bm.group(1)
                j = i + 1
                start = end = None
                text: list[str] = []
                while j < len(lines):
                    t = lines[j].strip()
                    if BLOCK_RE.match(t) or re.match(r"SCENE\s+\d+", t, re.I) \
                            or t.startswith("--- SCRIPT END"):
                        break
                    tm = TIME_RE.search(t)
                    if tm:
                        g = tm.groups()
                        start, end = to_sec(g[0], g[1]), to_sec(g[2], g[3])
                    elif t and not t.startswith("Scene "):
                        text.append(t)
                    j += 1
                blob = " ".join(text).strip().strip('"')
                all_blocks.append((label_full, blob))
                if start is not None:
                    name, tone = _split_label(label_full)
                    beats.append(Beat(
                        idx=len(beats), kind=_kind(label_full), label=name,
                        tone=tone, start=start, end=end,
                        text=blob, scene=scene_n, scene_heading=scene_heading))
                else:
                    untimed += 1                      # counted and reported, never silently dropped
                i = j
                continue
        i += 1

    timing = "screenplay"
    if not beats:                                     # a different format: try the other ways a script can carry its times
        for mode, fn in (("inline", lambda: _inline_timestamps(lines)), ("srt", lambda: _srt(lines))):
            got = fn()
            if got:
                beats, timing = got, mode
                break
        if not beats and all_blocks:                  # no times at all: build a timeline from the order and length of the lines
            beats, timing = _estimated(all_blocks), "estimated"
    duration = max([b.end for b in beats], default=0.0)
    return Episode(number=number, logline=logline, continuity=continuity,
                   duration=duration, beats=beats, scenes=scenes, source=path,
                   timing=timing, untimed_blocks=untimed if timing == "screenplay" else 0)


def load_show(folder: str, log=print) -> list[Episode]:
    """Load every episode text file under `folder` (recursive), sorted by number.

    Never loses an episode silently: if two files claim the same number, or none is given, episodes are numbered by
    file order and the run says so. If most files cannot be parsed it stops with a clear message."""
    paths = sorted([p for ext in ("txt", "md", "srt") for p in glob.glob(os.path.join(folder, "**", f"*.{ext}"), recursive=True)],
                   key=lambda p: _natural(os.path.basename(p)))
    eps, skipped = [], []
    for p in paths:
        if os.path.basename(p).lower() in ("readme.md", "requirements.txt"):
            continue
        try:
            ep = parse_episode(p)
        except Exception as exc:  # keep going, report clearly
            skipped.append((p, str(exc)))
            continue
        if not ep.beats:
            skipped.append((p, "no timed beats found (format differs?)"))
            continue
        eps.append(ep)
    for p, why in skipped:
        log(f"[parse] SKIPPED {os.path.basename(p)}: {why}")
    if not eps:
        raise SystemExit(f"No parsable episode files found under: {folder}\n"
                         "Expected blocks like  **SPEAKER (tone):**  followed by  Start: (mm:ss.ff) | End: (mm:ss.ff)  and the text.")
    if skipped and len(skipped) > len(eps):
        raise SystemExit(f"Only {len(eps)} of {len(eps) + len(skipped)} files could be parsed - the screenplay format is probably different. "
                         "See the SKIPPED lines above, and promofinder/parse.py for the format handled.")
    nums = [e.number for e in eps]
    if len(set(nums)) != len(nums) or 0 in nums:
        log("[parse] WARNING episode numbers missing or duplicated; numbering the files 1..N in file-name order")
        for i, e in enumerate(sorted(eps, key=lambda e: _natural(os.path.basename(e.source))), 1):
            e.number = i
    eps.sort(key=lambda e: e.number)
    return eps


def _natural(s: str):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def summarize(eps: list[Episode]) -> dict:
    """What the parser found - printed at the start of every run so a format problem is visible at once."""
    beats = [b for e in eps for b in e.beats]
    dlg = [b for b in beats if b.kind == "dialogue"]
    modes = {}
    for e in eps:
        modes[e.timing] = modes.get(e.timing, 0) + 1
    return {"episodes": len(eps), "beats": len(beats), "dialogue_beats": len(dlg), "timing": modes,
            "untimed_blocks": sum(e.untimed_blocks for e in eps),
            "with_tone": sum(1 for b in dlg if b.tone), "with_logline": sum(1 for e in eps if e.logline),
            "with_scenes": sum(1 for e in eps if e.scenes),
            "episode_seconds": [round(min(e.duration for e in eps)), round(max(e.duration for e in eps))]}
