# Taiwan Stock Quant Radar

Streamlit-based Taiwan equity scanner with scheduled post-close ranking, technical analysis, and strategy backtesting.

## Run locally

1. Use Python 3.14, matching CI and the generated dependency lock.
2. Copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` and fill in the required credentials.
3. Install pinned dependencies with `python -m pip install -r requirements.lock`.
4. Start the app with `streamlit run test.py`.

Never commit `.streamlit/secrets.toml`; it is intentionally ignored by Git.

## Daily scan

`.github/workflows/daily_scan.yml` runs at 15:17 Asia/Taipei on weekdays, with 16:17 and 22:17 recovery runs. Monday through Thursday use the top 300 stocks by daily trading volume; Friday expands the same run to 500. `SCAN_LIMIT=300` or `SCAN_LIMIT=500` can explicitly override this for a manual run. The scanner derives the trading date from the latest TWII bar, rejects stock bars from a different date, and uses a 45-minute Firestore lease plus a 40-minute job timeout so a crashed primary run cannot block the recovery schedule.

The configured `CORE_TICKERS` (default `2330,2317,2454`) are always retained without increasing the selected universe size. The Streamlit app only reads `market_data/daily_scan`; it never starts a broad scan from a user session. Large scan rows and tracker positions use schema-v2 manifests with bounded documents in `daily_scan_chunks` and `top10_tracker_chunks`; all readers remain compatible with legacy inline documents and reject missing/partial chunks instead of calculating from incomplete data.

Each daily Top-10 ranking is stored with its complete scan fields in `top10_history/{date}`. Position tracking writes an idempotent OHLC, daily return, holding return, MFE/MAE, rank, score, and action snapshot to `top10_tracking_history/{date}`. New signals can fill only when the current TAIEX bar confirms the signal date as its preceding market session, so exchange holidays do not become false expirations; missing next-session data is never replaced by a later bar. Same-bar exits are resolved only when the daily path makes them observable. An ambiguous entry/exit ordering is frozen as unresolved, not carried as a hypothetical position, and excluded from performance. Each history document also freezes separate schema-v2 and legacy cumulative summaries, so a later exit cannot rewrite an older report.

After a completed ranking, the official daily Top-10 is selected only from records whose saved `Entry_Status` is `現在可執行`, preserving quantitative-score order and assigning a fresh actionable rank from 1 to 10. Score ties prefer validation evidence and lower overheat instead of the largest same-day jump, and each known industry is capped at two names. Execution now separately records source completeness and model reliability, requires minimum backtest/validation evidence, checks official current-snapshot quarterly financial risk and multi-day institutional selling, and rejects bearish-market, weak effective reward/risk, and over-concentrated positions. Waiting-pullback, waiting-volume, and insufficient-condition records never fill an empty slot, so the list can honestly contain fewer than ten stocks. The scanner sends a ranking overview and a detailed execution sheet with entry zone, stop, target, technical win rate, sample/model credibility, and odd-lot position size. Sizing uses the entry-zone high and finds the largest integer share count whose modeled loss is at most NT$5,000 after full-rate buy/sell commissions, minimum commissions, sell tax, and stop slippage; gaps and liquidity can still exceed that estimate. Starting on 2026-08-28, the workflow also sends a daily tracking-performance image built only from saved authentic OHLC records. Independent delivery state records pending, failed, and sent attempts. All three artifacts are attempted even if one fails, and a multi-page performance retry resumes only unsent pages; `python scanner.py --resend-telegram` intentionally sends them again.

The Telegram link bot accepts a Taiwan stock code, name, or `/stock <code>` from the allow-listed `TELEGRAM_CHAT_ID` and replies with a button linking directly to that stock's Streamlit analysis page. The webhook uses a transactional processing lease with bounded retries and 30-day TTL metadata. The five-minute GitHub Actions poller remains a fallback, checkpoints each handled update, and safely exits without deleting an active webhook. The production Streamlit URL is the default, and the optional repository variable `ANALYSIS_BASE_URL` can override it after an app-domain change. The app resolves both `?stock=2330` and URL-encoded `?query=台積電`, while ambiguous names request a ticker instead of silently opening the wrong stock.

Historical gaps can be audited with `python backfill_top10.py` and applied only after reviewing the dry-run with `python backfill_top10.py --apply`. The tool backs up the current tracker, enriches archived rankings only with matching historical OHLC, and creates explicit `missing`, `partial`, or `unverified` date records when the original ranking cannot be recovered. It never recalculates a past ranking with present-day fundamentals.

## Data integrity

The app does not generate substitute market values when a required source fails. Missing revenue, institutional flow, quotes, and backtest samples remain missing in storage and display as `--` or `資料不足`, rather than `0`. A score is emitted only when every required technical field is present and finite. Historical snapshot mode truncates OHLCV to the requested date, uses a date-scoped cache, and does not reuse current macro, revenue, or institutional data. Rule-based risk and valuation indicators are labeled as heuristics, not probabilities or market consensus.

## Tests

Run the deterministic unit tests without contacting market-data services:

```powershell
python -B -m unittest discover -s tests -v
```

The tests cover trading-date resolution, same-day scan idempotency, confidence penalties, institutional-score integration, and trailing-stop bar ordering.

The backtest is explicitly a walk-forward technical-signal test. It does not claim to
reconstruct historical EPS, monthly revenue, or institutional data. Returns include
buy/sell commission, stock transaction tax, minimum commission, and stop-execution slippage;
the most recent 30% of trades is reported separately as a validation segment.
Current `executable_v3` records explicitly preserve overall, training, and validation
counts. The primary displayed rate is the adjusted training rate, not the overall
or realized tracking win rate. A legacy record without a known split stays labeled
as legacy; its validation count is not subtracted or added again. Signal-date plans
must pass the same price, daily-change anti-chase, volume, and cost controls before
next-session fills count. Per-stage diagnostics explain rejected plans, unfilled
orders, unresolved execution, and incomplete trades without inventing samples.

The homepage's **條件符合，待驗證** view is a separate, read-only paper-observation
list: only historical-evidence gates are removed for this diagnostic view, while
price, data and risk checks remain in force. Every row is explicitly non-executable
and is never supplied to the official Top-10, Telegram prediction sheets or position
tracker. An empty executable list is allowed; current thresholds are not lowered
to force ten names. Daily performance rows label legacy versus next-session zone
execution, and retain entry-time sample/rate metadata rather than today's values.
The default stock commission/tax assumptions follow the
[TWSE investing guide](https://www.twse.com.tw/zh/about/company/guide.html); broker-specific discounts and minimum fees can differ.

For public deployments, configure Streamlit authentication or set a private
`USER_DATA_NAMESPACE`. Favorites and simulated orders are stored in hashed, revisioned
documents so users and concurrent tabs cannot silently overwrite one another. Without
either setting, those items are isolated to the anonymous Streamlit session.

## Manual trade journal analyst

The **交易日誌分析師** page keeps manually confirmed real trades and explicitly
recorded missed decisions separate from simulated orders and the Top-10 hypothetical
tracker. Enter an actual entry and share count, then either enter an actual exit or
close the open record later. Actual buy/sell fees and tax are optional, but when
missing the journal reports gross P/L only and leaves net P/L unknown. A missed
decision requires both its decision date and a manually observed date/price
(which may be later on the same day). The observed change is never treated as
an executable trade or missed profit.

The page reviews the latest 30 records for repeated plan deviations, stop execution,
planned risk above NT$5,000, early profitable exits needing review, self-reported
emotion tags, and explicitly recorded missed opportunities. It always shows three
discipline rules, marking rules as personalized only after sufficient comparable
evidence. These are audit prompts, not psychological diagnoses or promises of profit.

Journal cloud persistence requires a Streamlit-authenticated user. Anonymous
sessions are kept only in memory; export a JSON backup and import it after logging
in or in a later session. A static `USER_DATA_NAMESPACE` is not used for the journal,
because it could expose one person's real trades to other visitors on a public app.
Cloud reads must succeed and their revision must be known before a journal write;
an outage cannot replace the saved journal with an empty list.

Development checks:

```powershell
python -m pip install -r requirements-dev.txt
ruff check . --select E9,F63,F7,F82
mypy app_security.py scan_state.py top10_tracker.py
coverage run -m unittest discover -s tests -v
coverage report
```

Regenerate the transitive production lock after reviewing upgrades:

```powershell
python -m piptools compile requirements.txt --output-file requirements.lock --strip-extras
```
