# Taiwan Stock Quant Radar

Streamlit-based Taiwan equity scanner with scheduled post-close ranking, technical analysis, and strategy backtesting.

## Run locally

1. Use Python 3.14, matching CI and the generated dependency lock.
2. Copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` and fill in the required credentials.
3. Install pinned dependencies with `python -m pip install -r requirements.lock`.
4. Start the app with `streamlit run test.py`.

Never commit `.streamlit/secrets.toml`; it is intentionally ignored by Git.

## Daily scan

`.github/workflows/daily_scan.yml` runs at 15:00 Asia/Taipei on weekdays, with a 22:00 retry. Monday through Thursday use the top 300 stocks by daily trading volume; Friday expands the same run to 500. `SCAN_LIMIT=300` or `SCAN_LIMIT=500` can explicitly override this for a manual run. The scanner derives the trading date from the latest TWII bar and uses a Firestore lease under `system_locks` so concurrent or duplicate runs do not overwrite one another or increment streaks twice.

The configured `CORE_TICKERS` (default `2330,2317,2454`) are always retained without increasing the selected universe size. The Streamlit app only reads `market_data/daily_scan`; it never starts a broad scan from a user session.

Each daily Top-10 ranking is stored with its complete scan fields in `top10_history/{date}`. Position tracking writes an idempotent OHLC, daily return, holding return, MFE/MAE, rank, score, and action snapshot to `top10_tracking_history/{date}`. The current tracker document keeps the latest snapshot and recent history dates for the UI; a same-day forced rerun replaces that day's entries instead of duplicating them.

After a completed ranking, the official daily Top-10 is selected only from records whose saved `Entry_Status` is `現在可執行`, preserving quantitative-score order and assigning a fresh actionable rank from 1 to 10. Waiting-pullback, waiting-volume, and insufficient-condition records never fill an empty slot, so the list can honestly contain fewer than ten stocks. The scanner sends two 1080×1400 Telegram PNGs for this executable universe: a ranking overview and a detailed execution sheet with entry zone, stop, target, technical win rate, sample credibility, and odd-lot position size. The detailed sheet title is the next weekday after the analysis date in `M/D股票預測` format, while the full analysis date remains visible for provenance. Each stock is sized independently with `floor(5000 / (current price - stop price))`, so its price loss at the saved stop does not exceed NT$5,000; fees, tax, and slippage are explicitly excluded. Starting on 2026-08-28, the same post-close workflow also sends a daily tracking-performance PNG built only from saved authentic OHLC records. It reports equal-weight daily/open performance, data completeness, action counts, completed-trade statistics, and—when more than ten positions exist—the five strongest and five weakest holding returns to avoid presenting winners alone. Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` either as GitHub repository secrets or inside `STREAMLIT_SECRETS`. Independent delivery state is stored in `notifications/daily_top10_{date}`, `notifications/daily_executable_{date}`, and `notifications/daily_tracking_performance_{date}`, so the 22:00 backup run only retries a missing image; `python scanner.py --resend-telegram` intentionally overrides the guards.

The optional real-time Telegram webhook accepts a Taiwan stock code, exact name, or `/stock <code>` from the allow-listed `TELEGRAM_CHAT_ID` and replies with a single-stock PNG. Cloud Run validates Telegram's `X-Telegram-Bot-Api-Secret-Token`, ignores every other chat, deduplicates update IDs in Firestore, uses the latest formal scan when available, and performs an on-demand provider-backed calculation otherwise. Deploy it with the `部署 Telegram 即時股票解析` GitHub Actions workflow after configuring `GCP_CREDENTIALS` and `TELEGRAM_WEBHOOK_SECRET` repository secrets.

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
buy/sell commission, stock transaction tax, minimum commission, and two-sided slippage;
the most recent 30% of trades is reported separately as a validation segment.
The default stock commission/tax assumptions follow the
[TWSE investing guide](https://www.twse.com.tw/zh/about/company/guide.html); broker-specific discounts and minimum fees can differ.

For public deployments, configure Streamlit authentication or set a private
`USER_DATA_NAMESPACE`. Favorites and simulated orders are stored in hashed, revisioned
documents so users and concurrent tabs cannot silently overwrite one another. Without
either setting, those items are isolated to the anonymous Streamlit session.

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
