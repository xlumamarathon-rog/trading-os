# vendor/ — USE repos (cloned locally, gitignored, never committed)

Clone with: `bash scripts/clone_vendors.sh` (or manually below). R1: read source here BEFORE wiring.

| Repo | Verified against source (2026-08-04) |
|---|---|
| marketcalls/openalgo | REST prefix `/api/v1`; placeorder schema: apikey, strategy(=our SEBI Algo ID), exchange, symbol, action(BUY/SELL), quantity (fractional only on crypto exchanges); response `{"orderid": ...}`. Payload builders: `src/core/broker_payloads.py` (unit-tested). |
| TauricResearch/TradingAgents | Entry point `TradingAgentsGraph.propagate(company_name, trade_date, asset_type="stock")` (graph/trading_graph.py:362); decision text via `final_trade_decision` + `SignalProcessor.process_signal(text)`. M11 precompute loop wraps THIS — not an invented API. |
| Ichinga-Samuel/aiomql | `aiomql.lib.bot.Bot`, `Trader`, `Order` classes (src/aiomql/lib). Wrapped ONLY inside mt5_service/ on the Windows VPS (MetaTrader5 pip dep is Windows-only). |

| 666ghj/MiroFish | Flask backend verified: POST /simulation/create, /simulation/prepare, /report/generate, /report/generate/status, GET /report/<id>. Adapter: src/intel/mirofish_adapter.py (route sequence asserted in tests). Service runs via its own docker-compose. |
| xbtlin/ai-berkshire | Claude Code skill pack (9 .md skills incl. dyp-ask, deep-company-series). Output parsed by src/intel/verdict_bridge.py (Ticker/Recommendation/Conviction contract). |
| Zhihan1996/TradeTheEvent | evaluate_news.json format verified from run_backtest.py: title/text/pub_time/labels{ticker,start_time}. Loader: src/ml/edt_loader.py (lookahead-defective rows excluded+counted). |
| stefan-jansen/machine-learning-for-trading | Reference notebooks (DSR, event studies) — cross-check source for M31/M23; not imported. |

ALL SEVEN vendors now cloned + integrated. Drift canaries: tests/vendor/test_vendor_integration.py
(scan actual vendor source; fail on upstream API drift — wire into nightly CI).

## pip libraries — verified importable in build env
arch (GARCH path LIVE-TESTED: var_worker.garch_or_ewma_forecast returns model="garch"),
quantstats, jugaad-data, backtesting, lightgbm, fastapi, httpx, pydantic.
Deploy-only (heavier/py3.11): riskfolio-lib, skfolio, vectorbt, stumpy, zipline-reloaded,
empyrical-reloaded, sentence-transformers, transformers, EasyEventStudies, aiomql, openalgo(client).
