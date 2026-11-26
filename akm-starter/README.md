> **First run:** The journal and signals source files are stored in numbered text parts. Run `python prepare_sources.py` from this folder before running their tests or using those components. The parts will be reassembled locally.

# AKM trading office — public starter

A local, early-stage starter, **not a deployed or complete trading system**. Three independent components share a repository but are not connected to each other:

- `office/`: a simple browser page and loopback-only Python bridge for MT5 quotes. The bridge checks for a connected MT5 terminal and verifies that account trading permission is disabled on every request. It displays only supported, fresh, valid bid/ask quotes and never sends orders. Tested with simulated data; real MT5/Windows/broker compatibility remains unverified. The default watchlist is XAUUSD, US30, USTEC and US500; only exact names or the `m` suffix are supported. Missing or ambiguous symbols are not guessed.
- `journal/`: independent manual-entry SQLite journal and daily/weekly/monthly reviews via command line. No connection to the office or MT5; no imported trades, account sync or UI.
- `signals/`: independent offline Python library to validate and authenticate caller-supplied JSON candidates against explicit configuration and deduplicate event IDs. It is **not a webhook server**, live feed, strategy engine, verified signal or trading advice. Accepted events are unverified and require human review. No integration with the journal, office or broker exists.

## Windows read-only office

1. Install Python 3.10+ and a Windows MT5 terminal, and sign into the correct account with an investor/read-only password. Confirm trading is disabled in the terminal. Never put credentials in this project or the bridge.
2. Install the Python MT5 binding: `py -m pip install MetaTrader5` (Windows, compatible Python and broker terminal required).
3. Run `office\START_AKM.bat`, or `cd office` then `py bridge.py`. Open **http://127.0.0.1:8765/** on the same machine if it does not open automatically. Keep the console open; Ctrl+C or closing the window stops it. Never expose or port-forward this local service. It has no user authentication; other processes on the same computer can access it.
4. A disconnected state, unsupported symbol, invalid quote or stale/future tick is shown without a price. Quotes older than 120 seconds or over 5 seconds in the future are suppressed. There is no guarantee of quote accuracy; independently verify prices and account permissions. Read-only checks may fail for some broker configurations.

## Manual journal

Requires Python 3.10+ with SQLite; no external packages. Choose a private local path: the database and input files are plaintext and contain sensitive information. Use device encryption, OS permissions and protected backups. Nothing is automatically backed up or synced. Never commit database, inputs, or notes; `.gitignore` helps but is not a security boundary.

In `journal/`, make a UTF-8 `trade.json` with real, manually verified values, using this shape (the sample below is fictional):

```json
{"opened_date":"2026-01-01","closed_date":null,"symbol":"TEST_A","direction":"buy","currency":"USD","realized_pnl":null,"planned_risk":null,"strategy":"example","notes":""}
```

Then `py akm_journal.py --db journal.sqlite add < trade.json`, `py akm_journal.py --db journal.sqlite list`, or `py akm_journal.py --db journal.sqlite review weekly 2026-01-01`. `get <id>` retrieves and `replace <id> < trade.json` replaces an entire entry. `review` also supports `daily` and `monthly`; `save-review weekly <date> < review.json` saves notes from a JSON object with a `notes` field. Amounts are decimal strings or null; no sizing, fee or risk verification is performed. The journal does not execute trades.

## Offline signal validation

The template `signals/shared_rules.template.json` is deliberately unconfigured and has no private rules or risk limits. Copy it to a **private** `rules.json` outside the repository. Set `status` to `configured`, retain `manual_only` and `not_validated`, set `known_instruments` and `approved_instruments` to explicit uppercase symbols, optionally `proposed_instruments`, and set `timeframes` to a nonempty subset of `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w`. Approved instruments must belong to known or proposed; known and proposed lists must not overlap; each list must not contain duplicates. This validates formatting and allowlists only; there is no risk, stop or trading-rule enforcement.

Import `akm_signals` from the `signals` directory. Generate a random 32–256-byte secret securely and provide it through the `AKM_WEBHOOK_SECRET` environment variable; never commit, print or share it. Load `rules = akm_signals.load_rules(path)`, then call `akm_signals.intake(body_bytes, server_secret=akm_signals.secret_from_env(), rules=rules, dedupe_db=private_db_path)`. Body is a UTF-8 JSON object containing exactly `secret`, `event_id`, `strategy`, `symbol`, `timeframe`, `direction` (`long` or `short`), and timezone-aware ISO `observed_at`. Strategy is a short identifier, not a validated model. IDs are globally and persistently deduplicated in a local SQLite file; protect this plaintext store and its backups. Rejection is fail-closed. The library does not check market truth or timestamp freshness. It is not a production webhook: any future listener needs transport security, authentication design, rate limits and independent security review.

## Tests

From the project root, run separately:

```text
cd office && py -m unittest discover -v && cd ..
cd journal && py -m unittest discover -v && cd ..
cd signals && py -m unittest discover -v && cd ..
```

Tests use mock MT5 and fictional data; success does not prove a live connection or strategy performance. On Linux/macOS substitute `python3`; only the simulated bridge tests are portable, not the Windows MT5 integration.

## Further work and safety

Possible future work includes secure opt-in integrations, UI for journal entries, independently verified market-data adapters and security testing. None is implemented. Never put API keys, account numbers, private strategy, personal trades or confidential branding in a public repository. Git history can preserve committed secrets even after deletion. No investment advice, automated decisions, trading, deployment or performance guarantee is provided.
