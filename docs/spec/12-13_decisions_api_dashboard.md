<!-- ChainWatch specification, §12 to §13. Part of CLAUDE.md; section numbers are global. -->

## 12. Decisions and reason codes

### 12.1 Tiers

Every scored transaction gets one of three **recommended** actions. The system never freezes, blocks or reports anything by itself (rule D2).

| Tier | Meaning | Recommended action text (`decision/tiers.py`, shown verbatim in API and UI) |
|---|---|---|
| `allow` | Nothing unusual enough to act on | "No action recommended." |
| `verify` | Pattern worth a quick check | "Recommend a step-up check with the sender (for example a confirmation prompt) before this payment completes." |
| `hold` | Strong pattern, money may be about to leave | "Recommend holding this payment for review by a fraud analyst before the money leaves the account." |

```python
TIER_ORDER = {"allow": 0, "verify": 1, "hold": 2}


def tier_from_score(score: float, thr: Thresholds) -> str:  # ties go to the higher tier
    return "hold" if score >= thr.hold else "verify" if score >= thr.verify else "allow"


def decide(base_score: float, thr: Thresholds, floor: str | None) -> tuple[str, float]:
    tier = tier_from_score(base_score, thr)
    if floor and TIER_ORDER[floor] > TIER_ORDER[tier]:
        tier = floor
        base_score = max(base_score, thr.hold if floor == "hold" else thr.verify)
    return tier, base_score  # (final tier, effective score)
```

The **Decision** object (pydantic `DecisionOut`, §13.1) carries: `event_id`, `decision`, `score` (effective, after escalation), `base_score` (before escalation), `risk` (`round(100 x score)`), `detector` (`G2+R` in production), `reasons` (0 to 3), `ring_id` and `ring_score` (when the sender or receiver belongs to an open or alerting ring), `recommended_action` (text above), `latency_us` (`features`, `model`, `decision`, `total`), `model_version` (first 8 hex characters of the model package's manifest hash), and in compare mode `shadow` (`B0` and `B1` score and decision for the same event).

### 12.2 Reason codes (`configs/reasons.toml`, `decision/reasons.py`)

Reasons are rules over the feature dictionary and the benign profile. They are **descriptions of observed values**, never accusations, and never use SHAP or model internals. The text must stay true when read by a customer or an analyst.

**Rule format** (no `eval` anywhere; a tiny interpreter):

```toml
[[reason]]
code     = "FAST_PASS_ON"
weight   = 90
when     = [["s_matched_share", ">=", 0.7], ["s_dwell_s", "<=", 900]]     # all must hold
when_any = []                                                              # optional: at least one must hold
text     = "Passed on {s_matched_share|pct} of the money it had just received, within {s_dwell_s|duration} (faster than about {s_dwell_s|rank_inv_pct}% of normal transactions)."
evidence = ["s_matched_share", "s_dwell_s"]
```

Clause operators: `>=`, `<=`, `>`, `<`, `==`, `rank>=` (benign-profile rank of the value is at least the number), `rank<=`. Text filters (whitelist; anything else is a startup error): `int`, `pct` (0.83 gives `83%`), `duration` (seconds to `41 seconds`, `6 minutes`, `3.2 hours`, `2 days`), `days`, `inr` (`INR 12,500`), `rank_pct` (profile rank x 100, rounded), `rank_inv_pct` ((1 - rank) x 100, rounded). **Derived values** available to rules and templates besides raw features: `g_nbr_total = g_nbr_in_susp_cnt + g_nbr_out_susp_cnt + g_dst_nbr_in_susp_cnt`, `near_cap = cfg.features.near_cap_inr`, `ring_id`, `ring_score`. A config check at start-up verifies that every feature a rule mentions exists in the registry and that every template placeholder resolves.

**Catalogue** (initial thresholds; tune on validation only, record changes in `docs/decisions.md`):

| Code | Weight | Condition | Text (abridged) |
|---|---|---|---|
| `RING_MEMBER_OUT` | 100 | Set by escalation (§11.5) | "This account is part of a linked group of accounts ({ring_id}) showing a relay pattern; money leaving it is recommended for review." |
| `RING_MEMBER_IN` | 95 | Set by escalation | "This payment goes to an account in a linked group ({ring_id}) showing a relay pattern." |
| `FAST_PASS_ON` | 90 | `s_matched_share >= 0.7` and `s_dwell_s <= 900` | "Passed on {pct} of money it had just received, within {duration}." |
| `NEW_SENDERS_BURST` | 85 | `d_in_uniq_new_1h >= 3` | "{n} of the senders paying this account in the last hour were paying it for the first time." |
| `MANY_SENDERS_IN` | 80 | `d_in_uniq_1h >= 4` and `d_in_uniq_1h rank>= 0.995` | "The receiving account got money from {n} different senders in the last hour." |
| `FAN_OUT_FAST` | 80 | `s_out_uniq_1h >= 3` and `s_pass_ratio_1h >= 0.6` | "Sent money on to {n} different accounts within an hour." |
| `DEEP_TRAIL` | 75 | `g_trail_depth >= 2` | "Money has already moved through {n} accounts in a chain before this payment." |
| `NEIGHBOUR_PATTERN` | 72 | any of `g_nbr_in_susp_cnt`, `g_nbr_out_susp_cnt`, `g_dst_nbr_in_susp_cnt` at least 1 | "{g_nbr_total} connected accounts already show a similar relay pattern." |
| `HIGH_PASS_THROUGH` | 70 | `s_pass_ratio_1h >= 0.8` and `s_in_amt_1h > 0` | "Sent on {pct} of the money received in the last hour." |
| `ROUND_TRIP` | 68 | `e_reverse_recent == 1` and `s_matched_share >= 0.5` | "Money is going back towards an account it recently came from." |
| `CONNECTED_GROUP` | 66 | `g_flow_comp_size >= 4` | "Part of a connected group of {n} accounts passing money on." |
| `NEW_RECEIVER_ACCOUNT` | 65 | `d_age_s <= 21600` and `d_in_uniq_1h >= 2` | "The receiving account is new (first seen {duration} ago) and is collecting from several senders." |
| `DORMANT_RECEIVER` | 65 | `d_idle_s >= 1209600` | "The receiving account had been inactive for {days} days." |
| `DORMANT_SENDER` | 60 | `s_idle_s >= 1209600` and `s_matched_share >= 0.5` | "This account had been inactive for {days} days before this burst of activity." |
| `DRAIN_BALANCE` | 60 | `t_s_bal_known == 1` and `t_s_drain >= 0.95` | "Sends almost the whole balance ({pct}) in one payment." |
| `NEAR_LIMIT_AMOUNT` | 50 | `t_near_cap == 1` | "The amount is just under INR {near_cap} (an illustrative limit used in this demo)." |
| `MODEL_SCORE` | 10 | Fallback when decision is not `allow` and nothing else fired | "The overall pattern of recent activity scores high compared with normal activity (risk {risk})." |

**Selection** (`reasons.explain(features, decision, ring_info, profile) -> list[Reason]`): no reasons when the decision is `allow`. Otherwise collect rules whose conditions hold (ring codes come from the engine's escalation), sort by `weight` descending then file order, keep the top 3, and fill `evidence` with the named features rounded to 3 significant digits. Output object `Reason = {code, text, evidence}`.

**Tests:** one positive and one negative fixture per rule; templates render without unresolved braces; no reason contains accusatory words (a test greps for a small deny-list such as "criminal", "fraudster", "guilty", "thief"); rules are deterministic and order-stable.

### 12.3 Ring-level reasons (`decision/reasons.py: ring_reasons(ring)`)

Up to three lines in the `RingAlert.reasons` field, computed from the ring's flagged edges:
1. `RING_STRUCTURE`: "{n_accounts} accounts are linked by {n_edges} flagged transfers within {span}: {n_entry} entry, {n_relays} relay and {n_exit} exit account(s)."
2. `RING_RELAY_SPEED`: "Relay accounts passed on {pct} of the money they received, a median of {duration} after it arrived." (median over relays of the delay between first flagged credit and first flagged debit; pct = relay out amount / relay in amount)
3. `RING_EXIT`: "{exit_amount|inr} has reached exit accounts (cash-out or merchant payment) so far; longest chain {longest_chain} transfers."

All three are neutral descriptions. Entry accounts are described as "entry", not "victim" or "suspect": they may be ordinary people who were tricked (§13.4).

---

## 13. Serving API and dashboard

The serving package runs on x86_64 and on s390x with pure-Python dependencies only (§5.2). `ScoringEngine` (§4) is the only place that scores; REST, WebSocket, replay and benchmarks all call it.

### 13.1 REST API (prefix `/v1`, JSON, UTF-8)

Run: `chainwatch serve` (flags `--host`, `--port`, `--model-dir`, `--config`, `--no-replay`, `--compare`) which calls `uvicorn.run(create_app(...), host, port, loop="asyncio", http="h11", ws="websockets", workers=1)`. Never use `uvloop` or `httptools` (they have no s390x wheels).

| Method and path | Purpose | Notes |
|---|---|---|
| `GET /healthz` | Liveness and readiness | `{"status": "ok", "ready": true, "version": "...", "uptime_s": 12.3}`; no auth; `ready` is false until models and replay data have loaded |
| `GET /v1/info` | Model, platform and engine summary | Shape below |
| `POST /v1/score` | Score one transaction | Body `EventIn`; returns `DecisionOut`; duplicate `event_id` returns the cached decision with header `X-Idempotent-Replay: true` |
| `POST /v1/score/batch` | Score up to `max_batch` transactions in order | Body `{"events": [EventIn, ...]}`; returns `{"results": [DecisionOut or {"error": {...}}, ...]}`; one bad event does not stop the others |
| `GET /v1/rings` | List rings | Query `status=alert,open` (default), `limit=50`, `detail=false` (edges omitted unless `true`) |
| `GET /v1/rings/{ring_id}` | One ring with edges and reasons | 404 if unknown |
| `GET /v1/accounts/{account_id}` | Account state summary | `account_view()` output plus the last 20 decisions involving the account and its ring id; 404 if never seen |
| `GET /v1/metrics` | Latency, throughput, counts, memory | Shape below |
| `GET /v1/replay/status` | Replay state | `{"state": "idle|running|paused|finished", "sim_ts": 0.0, "speed": 300.0, "position": 0, "total": 0, "lag_s": 0.0}` |
| `POST /v1/replay/start` | Start or restart from the warm-up point | Optional body `{"speed": 300.0}` |
| `POST /v1/replay/pause`, `POST /v1/replay/resume` | Pause or resume | 409 `replay_state` if not applicable |
| `POST /v1/replay/speed` | Change speed | Body `{"speed": 1200.0}`; allowed 1 to 5000 |
| `POST /v1/replay/next_campaign` | Fast-forward to 10 simulated minutes before the next ground-truth campaign | Events in between are processed (state stays correct) but not streamed to the UI; WS sends one `fastforward` message |
| `POST /v1/replay/reset` | Rebuild engine state and go back to `idle` | Reloads model package |
| `POST /v1/inject/scenario` | Inject a synthetic mule-ring campaign into the live stream | Body `{"typology": "funnel", "seed": 1}`; typologies as in §8.2.3; returns `{"ring_id": "INJ1", "n_events": 37, "starts_at": 123456.0}`; limited by `inject_max_per_minute` |
| `GET /` and `/static/*` | Dashboard | No auth |
| `WS /v1/ws` | Live stream | Section 13.3 |

**`EventIn`** (pydantic, `extra="forbid"`; label fields such as `is_fraud` are rejected with 422): `event_id: int`, `ts: float`, `src: str` (1 to 64 characters), `dst: str`, `amount: float` (greater than 0 and below 1e9), `type: Literal["TRANSFER","PAYMENT","CASH_OUT","CASH_IN","DEBIT"]`, optional `src_bal_before`, `src_bal_after`, `dst_bal_before`, `dst_bal_after` (float or null).

**`DecisionOut`**: fields in §12.1, plus `shadow: {"B0": {"score": 0.0, "decision": "allow"}, "B1": {...}}` only when compare mode is on.

**`GET /v1/info`** shape:

```json
{"service": "chainwatch", "version": "0.1.0", "mode": "replay", "provenance": "ChainWorld (synthetic)",
 "model": {"dir": "models/default", "trained_on": "world_medium_seed0", "detectors": ["B0","B1","G2"],
           "production": "G2+R", "model_version": "3fa9c1d2", "registry_hash": "sha256:...", "config_hash": "sha256:..."},
 "platform": {"machine": "s390x", "system": "Linux", "python": "3.12.x", "cpu_count": 4, "byteorder": "big"},
 "engine": {"clock": 0.0, "events": 0, "accounts": 0, "active_states": 0},
 "compare_mode": false, "auth_required": false}
```

**`GET /v1/metrics`** shape: `latency_us: {"features": {"p50":..,"p95":..,"p99":..,"max":..,"n":..}, "model": {...}, "decision": {...}, "total": {...}}` (engine-internal stage timings), `throughput: {"events_per_s_10s":..,"events_per_s_60s":..,"total_events":..}`, `decisions: {"allow":..,"verify":..,"hold":..}`, `rings: {"open":..,"alert":..,"closed":..}`, `memory: {"rss_mb":..}` (from `resource.getrusage`; units differ by OS, handle both), `engine: {...}`.

**Errors:** always `{"error": {"code": "...", "message": "..."}}`. Codes: `bad_request` (400/422), `unauthorized` (401), `not_found` (404), `late_event` (409; the event is older than `clock - max_lateness_s`), `replay_state` (409), `rate_limited` (429), `not_ready` (503).

**Engine access:** endpoints are `async def` and call `engine.process(event)` inline (about a millisecond; no thread hand-off), guarded by a `threading.Lock` (rule E8). In the batch endpoint call `await asyncio.sleep(0)` every 50 events so the WebSocket and health endpoints stay responsive.

**Security (`serving/security.py`):** if the env var named by `[serving].api_key_env` is set, every `/v1/*` call (including the WebSocket, via query `?key=`) needs a matching `X-API-Key` header, compared with `hmac.compare_digest`; `/healthz`, `/` and `/static/*` stay open. Optional token-bucket rate limit per client IP when `rate_limit_rps > 0`. CORS off unless `cors_origins` is non-empty. Request body limit 1 MB. Responses carry `Cache-Control: no-store`. No outbound network calls anywhere (rule D3). Structured access log lines never contain payloads beyond `event_id`.

### 13.2 Replay controller and injection (`serving/replay.py`, `serving/inject.py`)

- **Source:** `data/replay/demo.csv.gz` (labeled) via the stdlib CSV reader. Labels are split off at load time: the engine receives an `Event` (no labels); the controller keeps `truth[event_id]` for the UI overlay and the comparison panel.
- **Warm-up:** at start, events before `[replay].warmup_until_ts` (day 3) are processed silently (state built, counted in metrics under `warmup`, not streamed).
- **Clock:** a background `asyncio` task wakes every 50 ms wall-clock (speed starts at `[replay].default_speed`), advances `sim_ts += speed x dt`, pops events with `ts <= sim_ts`, processes them in order and pushes `{event, decision, truth}` items to the WebSocket hub. At most `max_batch` events per tick; if a backlog builds the status shows `lag_s` and the speed is effectively capped (never drop events).
- **State machine:** `idle -> running <-> paused -> finished`; `reset` returns to `idle` and rebuilds a fresh engine. No seeking backwards.
- **`next_campaign`:** uses `demo_rings.json` first timestamps; fast-forwards to `first_ts - 600 s`, processing events at full speed without streaming them.
- **Injection:** `inject.py` is a pure-Python (no numpy) generator `make_campaign(typology, rng, start_ts, victims, mules, sink) -> list[LabeledEvent]` following the typology table in §8.2.3 (same structure and timing laws, using `random.Random.lognormvariate`; delays scaled by `inject_time_scale = 0.5` so the demo ring unfolds in minutes). Victims are drawn from demo accounts with archetype `customer` that are not used by any campaign; mules and the collector get fresh ids `X0001...`; the sink is a random demo `agent` account. Amounts: victims LogNormal(median 9,000, sigma 0.9) clipped to `[500, 40000]`. Balances are null (unknown) for injected events, which the engine treats as "balance unknown"; the UI says so. Events are merged into the pending queue in time order starting at `max(sim_ts, now) + 5 s`; truth carries `ring_id = "INJ<n>"` and `injected = true`. Injection requires the replay to be `running` or `paused`.

### 13.3 WebSocket protocol (`serving/ws.py`)

`WS /v1/ws` is push-only (clients may send `{"type": "ping"}`; the server ignores other input). The hub keeps one bounded queue (100 batches) per client; when full, the oldest batch is dropped and the next message includes `"dropped": k`.

Server messages (JSON, one per `ws_batch_ms` while events flow):

```json
{"type": "snapshot", "info": {...}, "rings": [RingAlert...], "recent": [Item...], "replay": {...}, "metrics": {...}}
{"type": "batch", "items": [Item...], "ring_events": [RingEvent...], "replay": {...}, "metrics": {...}, "dropped": 0}
{"type": "fastforward", "to_ts": 0.0, "skipped": 0}
{"type": "heartbeat", "ts": 0.0}
```

`Item` = `{"event": EventIn, "decision": DecisionOut, "truth": {"is_fraud": 0, "ring_id": "-1", "hop": -1, "src_role": "legit", "dst_role": "legit", "injected": false}}`; `truth` is present only in labeled replay and is clearly drawn as an overlay (§13.4). `RingEvent` = `{"kind": "create|update|merge|alert|close", "ts": 0.0, "ring": RingAlert}`. `snapshot` is sent once on connect; `heartbeat` every 15 s.

### 13.4 Dashboard (`serving/static/`, vanilla HTML/CSS/JS, vendored `vendor/cytoscape.min.js`)

No CDN, no frameworks, works offline, readable on a projector (dark theme, large type, keyboard shortcuts: space play/pause, `n` next campaign, `i` inject). Reconnects the WebSocket with exponential backoff (1 s to 15 s). Keeps at most 200 rows in the feed and 3,000 graph elements.

Panels:
1. **Header:** product name, a **provenance badge** ("Synthetic data (ChainWorld)"), model line ("trained on world_medium_seed0"), a **platform badge** from `/v1/info` (for example `s390x, big-endian` or `x86_64`), replay controls (play/pause, speed 60x/300x/1200x, "next campaign", reset) and the **Inject** menu (typology dropdown).
2. **Live feed:** newest first. `allow` is summarised as a counter; `verify` and `hold` rows show tier badge (colour **and** shape/icon), amount, short ids, risk, and up to 3 reason chips. Click a row to select it.
3. **Graph (cytoscape):** accounts of open and alerting rings (plus entry accounts as they appear). Node shape and colour by role (entry circle, relay diamond, exit square); edge width by amount; new edges flash; layout `breadthfirst` (directed) with entry accounts as roots, re-run when a ring changes; selecting a ring fits the view. Toggle **"Show ground truth (demo only)"** outlines the real ring members from `truth`, so the audience can see what the system found versus what was planted.
4. **Ring panel:** list of rings (alerting first) with score bar, accounts, relays, longest chain, exit amount, three ring reasons, and the first-alert time. A **"Mark reviewed"** button only changes local UI state and says so in a tooltip (no server action, rule D2).
5. **Comparison panel (needs `compare_mode` and labeled replay):** a table per ground-truth ring: first time a one-payment-at-a-time model (`B0`) would have flagged two of its accounts versus first time `G2+R` did, and the time of the ring's first exit transaction. Header text: "In this synthetic replay". Shows "not flagged" honestly.
6. **Metrics strip:** engine latency p50 and p95 (total, with stage breakdown on hover), events per second, counts of allow/verify/hold, rings open/alerting, resident memory, and the latest sparkline of p95.
7. **Footer (always visible):** "Synthetic data. Recommendations only: a human reviews every case. Entry accounts may belong to people who were tricked. This demo makes no claim about real UPI traffic."

Wording rules (D2): "pattern", "linked accounts", "recommended", "entry/relay/exit". Never "criminal", "fraudster", "scam account". The UI never shows an accusation about a named person (all ids are opaque anyway).

### 13.5 Metrics module (`serving/metrics.py`)

- Stage timers use `time.perf_counter_ns()`.
- `LatencyHistogram`: log-spaced buckets (ratio sqrt(2)) from 10 microseconds up to `latency_hist_max_ms`, plus an exact ring buffer of the last 20,000 totals for exact p50/p95/p99. Report the exact figures; keep the histogram for the UI sparkline.
- Throughput: 1-second counters in a ring buffer of 120 seconds.
- **Terminology (must be kept in every report):** `engine` latency = time inside `ScoringEngine.process`; `end-to-end` latency = measured by `bench/load_test.py` from the client side over HTTP on localhost (includes JSON encoding, uvicorn and the network stack). Never present one as the other.

### 13.6 App factory and start-up

`create_app(config_path=None, model_dir=None, replay=True)`: in the `lifespan`, load config, validate registry hash against the model package (§9.8), load `PortableGBDT` models, thresholds, benign profile and reasons; build `ScoringEngine`; if `replay`, load the demo data and build the controller (idle); log one line per step with timings; flip `ready`. If anything fails, `/healthz` reports `ready: false` with a short `detail` and the process stays up (so the container does not crash-loop silently). Start-up must not download anything.
