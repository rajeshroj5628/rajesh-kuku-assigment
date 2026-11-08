"""One command: the full evaluation of a run against every reference set, written to eval_reports/.

    python eval/run_eval.py --run out [--gold eval/gold_user.json eval/gold_builder.json]

Each set is labelled by its role in eval/GOLD_FREEZE.json: 'dev' sets were looked at while the tool was designed (so results are
optimistic); only a 'heldout' set, frozen before the last design change and scored once, is an independent test. If no held-out
set exists, the report says so instead of implying independence. Nothing here is tuned; it only measures."""
import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval.metrics import evaluate  # noqa: E402
from promofinder.parse import fmt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def role_of(path):
    reg_path = os.path.join(ROOT, "eval", "GOLD_FREEZE.json")
    key = os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")
    reg = json.load(open(reg_path, encoding="utf-8")) if os.path.isfile(reg_path) else {}
    if key not in reg:
        return "not frozen", None
    if hashlib.sha256(open(path, "rb").read()).hexdigest() != reg[key]["sha256"]:
        raise SystemExit(f"{path} changed after it was frozen ({reg[key]['frozen_at']}); refusing to score a moved target.")
    return reg[key]["role"], reg[key]["frozen_at"]


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="out")
    ap.add_argument("--gold", nargs="*", default=None)
    a = ap.parse_args()
    golds = a.gold or sorted(os.path.join("eval", f) for f in os.listdir(os.path.join(ROOT, "eval")) if f.startswith("gold_") and f.endswith(".json"))
    promos = json.load(open(os.path.join(a.run, "promos.json"), encoding="utf-8"))
    sp = os.path.join(a.run, "slate.json")
    slate = json.load(open(sp, encoding="utf-8")) if os.path.isfile(sp) else []
    full = {p["rank"]: p for p in promos}
    slate = [dict(full.get(s["rank"], s), slate_rank=s.get("slate_rank")) if s.get("rank") in full else s for s in slate]
    report, md = {}, [f"# Evaluation of `{a.run}`  ({datetime.date.today()})", "",
                      "Matching rule: same episode and time-span IoU >= 0.5. Small sets: one clip moves a recall figure by 12-14 points.", ""]
    roles = []
    for g in golds:
        role, when = role_of(g)
        roles.append(role)
        r = evaluate(promos, slate, json.load(open(g, encoding="utf-8"))["promos"])
        report[os.path.basename(g)] = {"role": role, "frozen_at": when, **r}
        md += [f"## {os.path.basename(g)}  [{role}{', frozen ' + when if when else ''}]", "",
               f"- found among all clips (recall): **{r['recall_any'] * 8:.0f}/{r['n']}**" if False else f"- found among all clips: **{round(r['recall_any'] * r['n'])}/{r['n']}**",
               f"- inside the overall top 10 / 12 / 20: {pct(r['recall_at_10'])} / {pct(r['recall_at_12'])} / {pct(r['recall_at_20'])}",
               f"- MRR {r['mrr']}, median rank of the best matching clip {r['median_rank']}",
               f"- best clip of its own episode: top-1 {pct(r['in_episode_top1'])}, top-3 {pct(r['in_episode_top3'])}",
               f"- slate precision (lower bound): {r['slate_matches']}/{r['slate_size']} = {pct(r['slate_precision_lower_bound'])}; "
               f"in episodes a human labelled: {pct(r['precision_in_labelled_episodes'])} of {r['labelled_episodes_in_slate']} slate clips", ""]
        md += ["| Ep | human clip | best tool clip | IoU | overall rank | in-episode rank |", "|---|---|---|---|---|---|"]
        for i in r["items"]:
            md.append(f"| {i['episode']} | {fmt(i['gold'][0])}-{fmt(i['gold'][1])} | {fmt(i['tool'][0]) + '-' + fmt(i['tool'][1]) if i['tool'] else '-'} | {i['iou']} | {i['rank'] or '-'} | "
                      f"{str(i['ep_rank']) + '/' + str(i['ep_total']) if i['ep_rank'] else '-'} |")
        md += ["", "Failure examples:"]
        for i in r["failures"]["not_in_top_12"]:
            c = i["champion"]
            md.append(f"- Ep{i['episode']} human clip {fmt(i['gold'][0])}-{fmt(i['gold'][1])} ('{i['note']}') is outside the overall top 12 (rank {i['rank'] or 'not matched'}); "
                      f"the tool's best clip of that episode is {fmt(c['span'][0])}-{fmt(c['span'][1])}, IoU {c['iou']} with the human one." if c else f"- Ep{i['episode']}: no clip")
        for f in r["failures"]["slate_clips_in_labelled_episodes_without_a_match"]:
            md.append(f"- slate #{f['slate_rank']} (Ep{f['episode']} {fmt(f['span'][0])}-{fmt(f['span'][1])}) sits in an episode a human labelled but does not match the human pick.")
        md.append("")
    if "heldout" not in roles:
        md += ["## Independent test", "", "**No held-out set has been scored.** Every number above is on development sets that were seen while the tool was built, "
               "so none of them is an independent measure of quality on unseen picks.", ""]
    os.makedirs(os.path.join(ROOT, "eval_reports"), exist_ok=True)
    stem = os.path.join(ROOT, "eval_reports", os.path.basename(os.path.normpath(a.run)))
    json.dump(report, open(stem + ".json", "w", encoding="utf-8"), indent=1)
    open(stem + ".md", "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))
    print(f"written: {stem}.md / .json")


if __name__ == "__main__":
    main()
