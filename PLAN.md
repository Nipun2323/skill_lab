# SyncroMove — build plan

Next-place prediction for a logged-in user from their own check-ins, using a fixed, explainable
scoring algorithm (Markov transition + time of day + frequency + recency + a tiny popularity term).
No training, no fitted weights, no ML libraries.

Status legend: `[ ]` todo · `[x]` done · `[!]` blocked

---

## Blocker before Phase A: the dataset

This session runs in a cloud container, not on the Windows laptop. Kaggle and the original
Google Sites page are **not reachable** from here (network policy), and there is no `kaggle` CLI.
PyPI and Google Fonts are reachable, so everything else can be built.

Ways to get `dataset_TSMC2014_NYC.txt` (or the Kaggle `.csv`) in:

1. **Attach the file / zip to the chat** — it lands in the container, I point `--data` at it.
   Nothing is committed; `data/raw/` is git-ignored.
2. **Allow the download host** for this environment (the original source's Google Drive link)
   so I can fetch it myself.
3. Build Phases B–D against a synthetic file now; you run Phase A + evaluation on your laptop
   and commit only `data/venues.csv.gz`, `data/samples/…`, `docs/evaluation_results.json`.
   Weakest option: I couldn't verify §4.1/§5.2 myself.

Until then, Phase A code and Phase B code can be written and unit-tested on synthetic data,
but the **checkpoints (stats match, results within ±1 pt) cannot be signed off**.

---

## Repository layout

```
config.py                 defaults + env/.env overrides (§2.2 table)
run.py / serve.py         Flask dev server / waitress
predictor/
  types.py                CheckIn, Profile, Score, Prediction dataclasses
  algorithm.py            build_profile, score_candidates, top_k, time_bucket, haversine_km
scripts/
  import_dataset.py       raw file -> venues.csv.gz + sample_histories.json.gz, prints stats
  evaluate.py             time-ordered replay of last 20%, writes docs/evaluation_results.json
  common.py               shared raw-file reader (encoding/separator/header detection, cleaning)
app/
  __init__.py             create_app, security headers, error handlers, venue sync on start
  db.py                   sqlite connection, schema, load_venues (checksum in app_meta)
  security.py             CSRF, session, login_required, rate limit glue
  validators.py           every server-side rule, ValidationError(field, message, status, code)
  services/repository.py  all SQL, always scoped by session user_id
  services/prediction_service.py
  auth.py views.py api.py
  templates/ static/{css,js,fonts}
data/venues.csv.gz, data/samples/sample_histories.json.gz   (committed)
docs/LLD.md, docs/EVALUATION.md, docs/evaluation_results.json, docs/accuracy.png
tests/                    test_algorithm, test_auth, test_api, test_evaluate_smoke
```

Dependencies: runtime `Flask>=3.0,<4`, `waitress>=3.0`; dev `pytest`, `matplotlib`.
Stdlib only for data (`csv`, `gzip`, `json`, `sqlite3`, `statistics`).

---

## Phase A — dataset import  (checkpoint: §4.1 stats printed and matching)

- [ ] `scripts/common.py`: read bytes, decode UTF-8 → fall back to latin-1; sniff tab vs comma;
      detect header row (first field not numeric); read 8 columns **by position**;
      parse `Tue Apr 03 18:00:09 +0000 2012` → epoch UTC int.
- [ ] `clean()`: drop exact duplicate rows (expect 227,428 → 227,178); sort by (user, ts);
      build per-user sequences `(venue_id, ts_utc, tz_offset_min)` with back-to-back
      same-venue repeats collapsed (`COLLAPSE_REPEATS`).
- [ ] `venue_table()`: median lat/lon per venue, category = most frequent label, popularity =
      check-in count.
- [ ] Sample histories: 25 users picked with `random.Random(42)`; user ids replaced by
      `sample_01..25` (anonymised); only users whose cleaned sequence is 100–500 rows so the copy
      stays small.
- [ ] Print: rows raw / after dedup, users, venues, category names, category ids,
      min/median/max check-ins per user, first/last timestamp, rows after collapse.
- [ ] Compare against §4.1 in the output (✓/✗ per stat).

## Phase B — algorithm + evaluation  (checkpoint: results table vs §5.2)

- [ ] `algorithm.py` exactly as §4.3: profile in one pass; candidates = visited − current
      (cold start: top-100 popular); five signals each normalised over candidates (or all zero);
      weighted sum from config; probability = score / Σscore; rank by score → popularity → id;
      reason = largest weighted contribution, "a"/"an" by vowel.
- [ ] Order-2 Markov with back-off when pair count < 2.
- [ ] Time bucket: local time = ts + offset; `hour // 3` + 8 × (weekend); 16 buckets.
- [ ] Unit test reproducing §4.4 (Gym 0.553, Coffee Shop 0.246, Subway 0.105, Home 0.097 —
      I re-did the arithmetic: recency total 2.657, all four scores check out).
- [ ] `evaluate.py`: global time cutoff at 80th percentile of cleaned check-ins; popularity from
      pre-cutoff rows only; per user, incremental replay (profile updated in O(1) per step rather
      than rebuilt, same result — verified against `build_profile` in a test); query time = real
      check-in time.
- [ ] Methods: popularity only, most-visited, Markov-only (back-off to frequency),
      full with `W_TIME=0`, full.
- [ ] Metrics: top-1/3/5, MRR, top-3 category, top-3 within 1 km, top-3 by prior-history band,
      per-user median top-3, runtime. Writes `docs/evaluation_results.json`.
- [ ] Any gap > 1 pt vs §5.2 → find the cause and report it; do **not** adjust weights.

## Phase C — web app  (checkpoint: running at http://127.0.0.1:5000)

- [ ] Schema §3.1 with FKs, cascades, indexes; venues synced from `venues.csv.gz` on first start
      (checksum in `app_meta`, re-sync if file changes).
- [ ] Auth: register (optional "Load a sample New York history"), login with rate limit
      (5 failures per email or IP / 15 min → 429 with minutes), logout, session rotation,
      safe `next` redirect.
- [ ] CSRF token in session; form field for pages, `X-CSRF-Token` header for API.
- [ ] Security headers: CSP `default-src 'self'`, X-Frame-Options DENY, nosniff,
      Referrer-Policy no-referrer; `Cache-Control: no-store` on `/api/*`; 32 KB body limit.
- [ ] API endpoints §2.2 with `{"error": {code, field, message}}` errors.
- [ ] `predicted_rank`: on check-in, look up the latest logged prediction batch for this user
      whose context is the current last check-in; rank 1–3 hit, 0 miss, null if none shown.
- [ ] Prediction logging skips identical consecutive batches for the same context.
- [ ] Account page: delete check-ins / delete account (password re-entry), JSON export.
- [ ] UI: transit-map hero (inline SVG, equirectangular, cos(lat) scaling), next-stops list with
      "Why?", check-in panel, recent visits with Predicted #n / Missed tags, accuracy card,
      time picker. Overpass woff2 self-hosted + OFL licence. No inline JS/CSS. 360 px, focus
      rings, reduced motion.
- [ ] Client-side validation module with the exact messages from §2.2.
- [ ] `/api/health` → `degraded` with message when venues file missing.

## Phase D — tests, docs, hygiene

- [ ] pytest: test_algorithm, test_auth, test_api, test_evaluate_smoke (§5.3) — all green.
- [ ] README (PowerShell + bash), `.env.example`, `.gitignore`
      (`.env`, `instance/`, `data/raw/`, venvs, `__pycache__`, `kaggle.json`).
- [ ] `docs/LLD.md` sections 1–7, numbers pulled from `evaluation_results.json` (rendered by a
      small script, not typed by hand); `docs/EVALUATION.md` + `docs/accuracy.png`.
- [ ] End-to-end manual run: register with sample, top 3, Why?, check-in, predicted_rank recorded,
      export, delete. Report what was run.
- [ ] Grep for ML libraries / training code; `git status` clean of secrets and raw data.

---

## Points I'm flagging up front

- **§5.2 is a target I can't pre-verify.** If my numbers differ by > 1 pt, I'll trace it
  (cutoff rule, collapse before vs after split, tie handling, how "∞" ranks count) and report the
  cause rather than nudge anything.
- **Cutoff definition:** "last 20% by time" is ambiguous between 20% of raw rows and 20% of
  cleaned/collapsed rows. I'll use cleaned+collapsed rows and check the cutoff lands at
  2012-11-23 20:58 UTC as stated; if not, I'll say which definition matches.
- **Sample-history timestamps are 2012–2013.** A user adding a check-in "now" is fine
  (not before last), but the predicted time bucket will use today's time. That's expected; the
  UI will say the history is from 2012–13 NYC.
- **Venue search returns NYC places only.** Distances from the user's last check-in; for a user
  with no history, search sorts by popularity instead (`near_last_checkin: false`).
