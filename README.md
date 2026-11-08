# PromoFinder: find the best promos in a microdrama screenplay

PromoFinder reads the timestamped screenplays of a whole show and returns 20-60 s promo clips. Each clip comes with the episode,
the start and end of every segment, the hook line, the cliffhanger line and a one-line reason, ranked in the order you would post
them. Every timestamp and every quoted line is checked against the screenplay by code. A dashboard lets an editor preview, shortlist
and export; the same pipeline runs from the command line.

> The input scripts are Kuku's confidential content. They are **not** in this repository, and the raw outputs of a run quote them,
> so share those only privately.

## 1. Set up (5 minutes)

```bash
pip install -r requirements.txt            # PyYAML only; the pipeline itself is standard library
cp .env.example .env                       # put your key in .env, e.g. GEMINI_API_KEY=...  (never commit it)
python run_pipeline.py --check-llm         # one tiny call: checks the key and the model
```

Optional, only for the tests and for regenerating screenshots: `pip install playwright` (uses the Edge/Chrome you already have).

## 2. Run it

```bash
python run_pipeline.py --input "<folder with the episode files>"            # whole show -> out/
python run_pipeline.py --input "<folder>" --episodes 1 2 3 --out out_try     # a few episodes
python app.py --scripts "<folder>"                                           # dashboard at http://127.0.0.1:8000
```

Input: a folder (or a ZIP, in the dashboard) of episode files, `.txt`, `.md` or `.srt`, in any naming style. Four screenplay layouts
are read: the Kuku block format (`**SPEAKER (tone):**` + `Start: (mm:ss) | End: (mm:ss)`), inline times (`[01:05] NAME: text`),
subtitle files, and scripts with no timestamps at all (times are then **estimated** from the length of the lines and the run says so).
Episodes without a number are numbered by file order. A format that cannot be read stops the run and names the files.

Outputs in `out/`: `slate.md` / `slate.json` (the recommended slate), `report.md`, `promos.csv` / `promos.json` (every ranked clip, each with a
`clip_id` you can use as a tracking tag), `quality.md` / `quality.json` (health checks measured on the run itself, no human labels needed),
`dropped_by_validator.json`, `run_log.json` (requests, tokens, time, failures, the scoring weights used).

### On a new show (interview runbook)

```bash
python run_pipeline.py --input "<folder>" --provider mock --episodes 1 2 3     # 10 s, offline: does the format parse?
python run_pipeline.py --input "<folder>" --episodes 1 2 3 4 5 --out out_try   # about 5 minutes on the free tier
python run_pipeline.py --input "<folder>"                                     # about 420 requests, about 30 min on the free tier
```
- **Faster with a paid or provided API:** set `llm.min_seconds_between_calls: 0.3` and `pipeline.workers: 8`.
- **Another model or endpoint:** edit `llm:` in `config.yaml`, or `--provider openai_compatible --base-url <url> --model <name>` (the key variable is
  `llm.api_key_env`). Endpoints without a JSON mode are handled. If the provider is switched, one model does every stage.
- **A used-up daily quota stops the run at once** and says everything finished is cached: run the same command again to continue.

## 3. Web app

`python app.py --scripts "<folder>"`, then open http://127.0.0.1:8000. It runs the same pipeline, listens on localhost only and never serves the key.

- **Dashboard:** the slate as reel-style cards, a live preview phone, the story map (best clip per episode), run overview (time, model, validators), run health,
  and the evaluation against reference clips (labelled development vs held-out, with failure examples). Test it against your own picks.
- **Promos:** grid or list, search, filter, sort; **Preview** plays the screenplay lines in time as subtitles, with the hook and cliffhanger tagged.
- **Shortlist:** star clips, add notes, copy the brief or export.
- **Run:** drop `.txt/.md/.srt` files or one `.zip` (or give a folder), **Check files** shows what was detected (episodes, blocks, how times were obtained) before
  anything runs, then Run with a live log.
- **Exports:** CSV and JSON of every clip shown, and the slate as CSV, JSON or Markdown.

## 4. How it works

| Step | What | Who |
|---|---|---|
| 1 Parse | any supported format -> timed beats | code |
| 2 Funnel | every 20-60 s window on beat boundaries, scored by generic hook / cliffhanger rules; top 12 per episode | code |
| 3 Judge | the model reads an episode once, quotes the hook and cliffhanger, rates them 1-5, may add windows | model |
| 4 Stranger test | the model sees only the clip: could a newcomer follow it? one repair (prepend a setup) | model |
| 5 Strict validation | snap to beats, 20-60 s, ordering, dedupe, and **every shown quote is a real line of its clip** (pulled in, trimmed or replaced) | code |
| 6 Rank | finalists = the best 60 clips of the whole show (at most 2 per episode), judged head to head in a Swiss tournament, each pair in both orders | model + code |
| 7 Slate | a varied set of 12, labelled for spoilers, trimmed to open on the hook and stop on the cliffhanger | code (+ model labels) |
| 8 Review | the editor accepts, reorders or drops | human |

The scoring formulas are in `promofinder/scoring.py`; their weights are in `config.yaml` (`scoring:`). The scores are the model's judgement of a clip, not measured audience behaviour.

## 5. Testing and evaluation

```bash
python eval/run_all_tests.py --scripts "<folder>"      # core unit tests, formats + disguised drama, adapter vs a fake server, browser test (needs the app running)
python eval/run_eval.py --run out                      # the full evaluation against every reference set -> eval_reports/
python eval/quote_report.py --run out --scripts "<folder>"
```
- **Matching rule:** same episode and time-span IoU >= 0.5, because human and tool boundaries rarely coincide.
- **`eval/gold_user.json`** (8 clips picked by hand while the tool was being built) and **`eval/gold_builder.json`** (7 clips drafted by the AI assistant) are
  **development sets**: they were looked at while the tool was designed, so results on them are optimistic. **No independent test set has been scored.**
  `eval/GOLD_FREEZE.json` records their hashes and labels.
- **For a clean test:** put new picks in `eval/gold_heldout.json`, check them against the real screenplay boundaries with
  `python eval/check_gold.py <file> --scripts <folder>`, freeze them with `python eval/freeze_gold.py <file> --role heldout --scripts <folder>`, change nothing, then run
  `python eval/run_eval.py`. A frozen file that changes is refused. Score a held-out set once.
- `eval/transfer_test.py` renames the characters, strips headers, drops the timestamps, and converts the scripts to other formats: parsing, the funnel and the pipeline still work.

## 6. What was tried and not kept

- Reasoning-first judging and editor examples for the judge: more split verdicts, human picks ranked lower (`eval/tournament_experiment.py`).
- Averaging two models: they agree (Spearman 0.76) but the average did not help (`eval/ensemble_experiment.py`).
- A spoiler penalty in the ranking: it cost human picks and is an untested hypothesis, so spoilers are a label only (`slate.spoiler_penalty: 0`).
- Show-specific words in the funnel looked like an improvement on the reference sets and were noise; they were removed so the funnel transfers.

## 7. Known limits

Screenplay text only (no video, audio or audience data). The funnel's word lists are English. The model rates hook, cliffhanger and standalone clarity;
emotional intensity, curiosity, retention and social-media fit are not scored separately. Cross-episode ranking is unreliable (see the write-up).
Evaluation is on development sets only. The free tier allows about one call every 4.5 s and a daily cap per model.

## 8. Layout

`promofinder/` the pipeline (parse, candidates, judge, strict, stranger, rank, tournament, slate, quality, scoring, llm) | `run_pipeline.py`, `run_funnel.py` | `app.py` + `fronthand/` (the whole front end)
the dashboard | `eval/` metrics, tests and experiments | `config.yaml` | `examples/` calibration clips used only in an experiment.


## Live demo (read-only)

`fronthand/` holds the whole front end: `app.html` (the dashboard the local app serves) and `index.html` + `data/` (a static, read-only copy for a completed 50-episode run, ready for Vercel: framework preset **Other**, root directory `fronthand`, no build command). Uploading and running need the real app (`python app.py`). Rebuild it with `python make_demo_site.py --run out` while the app is running.

Live read-only demo: https://rajesh-kuku-assigment-fronthand.vercel.app
