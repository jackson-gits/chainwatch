<!-- ChainWatch specification, §9 to §11. Part of CLAUDE.md; section numbers are global. -->

## 9. Feature engine

One module, `chainwatch/features/engine.py`, produces **every** feature, for training and for serving (rule E4). Training data is made by replaying a stream through `FeatureEngine.step()`; there is no second, pandas-based implementation. `features/reference.py` is a deliberately slow brute-force version used only by tests to prove the fast one is right.

### 9.1 Principles

1. **Strictly causal.** The features of event *e* depend only on events with a smaller `(ts, event_id)` plus the fields of *e* itself (amount, type, balances, ids). Never on later events, never on labels.
2. **Features do not depend on model outputs.** No stacking. "Suspicious" flags (§9.7) are rule-based, driven by `[features]` thresholds. This keeps training features identical to serving features and avoids leakage through model-on-model feedback. It also means `step()` can compute features and update state in one call, without waiting for a score.
3. **Bounded state.** Every per-account structure has a cap (`deque_cap`, `payee_memory_n`, `amount_history_n`) and idle accounts are evicted (`state_ttl_s`). Counters saturate at the cap; the brute-force tests run with huge caps, and cap behaviour has its own tests.
4. **No NaN, no inf, no nulls.** Every feature has a default (§9.3) used when its inputs are missing. The exporter and the portable evaluator assume this (§10.3).
5. **Float64 everywhere.** Never convert features to float32 on the way to training or scoring: a float32 rounding can flip a split near a threshold and break train/serve parity.
6. **Time.** `ts` is seconds since simulation start; `ts = 0` is 00:00 UTC of day 0. The local hour used by `t_hour` is `int(((ts + tz_offset_s) % 86400) // 3600)`. ChainWorld's generator must apply the same conversion when it uses hour-of-day weights (§8.2.2). PaySim's `step` is treated as UTC hours (UNVERIFIED semantics; note it in the data card).
7. **Window convention.** A window of length *W* ending at `ts` is the half-open interval `(ts - W, ts]`. Windows come from `[features].windows_s = [600, 3600, 86400]`, written `_10m`, `_1h`, `_24h` in feature names (the names are fixed even if the config values are changed; a config check rejects other lengths for now).
8. **Out-of-order events.** The engine keeps a clock. State and features use `ts_eff = max(ev.ts, clock)`, then `clock = ts_eff`. Offline streams are sorted, so `ts_eff = ts`. Online, small disorder within `max_lateness_s` is therefore harmless and state deques stay monotone.
9. **"Including the current event".** Where a feature is marked **(+cur)** it counts the event being scored as if already inserted (its own fields are known at scoring time). All other counters use prior state only. Implementation: read prior state, then add the current contribution explicitly; insert the event into state only in the apply phase (§9.5).

### 9.2 Detector column sets

| Detector | Groups | Count (P1) | Meaning |
|---|---|---|---|
| `B0` | T | 13 | One payment at a time. Weak reference (§1.3 item 8: "one-payment-at-a-time model") |
| `B1` | T + S + D + E | 50 | Everything an account's **own history** can tell: sender and receiver windows, FIFO pass-through, new-payee signals. The strong, fair baseline (rule H2) |
| `G2` | T + S + D + E + G | 59 | B1 plus **multi-account** structure: money-trail depth, funnel width, neighbour suspicion, flow components |

Group prefixes: `t_` transaction, `s_` sender account, `d_` receiver account, `e_` edge (pair), `g_` graph. The order inside each detector's vector is the order of the tables below (this is the canonical order in `registry.py`).

### 9.3 Feature catalogue

Defaults apply when the input does not exist (new account, empty window). **P0** marks the walking-skeleton subset (§19); with `[features].priority = "P0"` only P0 features are computed and the detector column sets shrink accordingly (the registry hash changes, so models trained on one setting cannot load under the other).

#### T group (transaction only; 13)

| Name | Definition | Default | P0 |
|---|---|---|---|
| `t_amount` | `amount` | n/a | yes |
| `t_log_amount` | `log1p(amount)` | n/a | yes |
| `t_type_transfer`, `t_type_payment`, `t_type_cash_out`, `t_type_cash_in`, `t_type_debit` | One-hot of `type` (1.0 or 0.0) | n/a | yes |
| `t_hour` | Local hour 0..23 (§9.1 item 6) | n/a | yes |
| `t_is_round` | 1 if `amount >= round_min_amount` and `amount` is a multiple of 100 (use `round(amount, 2) % 100 == 0`) | 0 | yes |
| `t_near_cap` | 1 if `near_cap_lo_frac x near_cap_inr <= amount <= near_cap_inr` | 0 | yes |
| `t_s_drain` | `clip(amount / src_bal_before, 0, 1.5)` if `src_bal_before` is known and above 0 | 0 | yes |
| `t_s_bal_after_zero` | 1 if `src_bal_after` is known and below 1.0 | 0 | yes |
| `t_s_bal_known` | 1 if `src_bal_before` is known | 0 | yes |

Notes. Only **sender-side** balances are used (this is the known PaySim shortcut; §8.3 reports how much of the score it carries). `t_near_cap` is a standard "just below a limit" heuristic; B0 gets it too, so it gives G2 no edge over the baselines. Its numbers are illustrative (UNVERIFIED as real limits).

#### S group (sender account history; 22)

Let *S* = `ev.src`. "Out-events" are S's debits; "in-events" are credits to S.

| Name | Definition | Default | P0 |
|---|---|---|---|
| `s_age_s` | `ts - first_seen(S)`; 0 if S was never seen before | 0 | |
| `s_idle_s` | `ts - last_seen(S)` (any prior event as sender or receiver); 0 if never seen | 0 | |
| `s_out_cnt_10m`, `s_out_cnt_1h`, `s_out_cnt_24h` | Number of S out-events in the window **(+cur)** | 1 | `_1h` only |
| `s_out_uniq_1h` | Distinct receivers among S out-events in 1h **(+cur)** | 1 | |
| `s_out_amt_1h` | Sum of S out amounts in 1h **(+cur)** | `amount` | |
| `s_in_cnt_1h`, `s_in_cnt_24h` | Credits to S in the window (prior) | 0 | |
| `s_in_uniq_1h` | Distinct payers of S in 1h (prior) | 0 | yes |
| `s_in_amt_1h` | Sum of credits to S in 1h (prior) | 0 | |
| `s_pass_ratio_1h` | `clip(s_out_amt_1h / s_in_amt_1h, 0, 2)` if `s_in_amt_1h > 0` else 0 | 0 | yes |
| `s_pass_ratio_24h` | Same over 24h (uses internal 24h sums, **(+cur)** for out) | 0 | |
| `s_matched_share` | Share of **this** debit's amount covered by S's earlier credits, FIFO, within `fifo_lookback_s` (§9.6) | 0 | yes |
| `s_dwell_s` | Amount-weighted mean age (seconds) of the credits consumed by this debit; `dwell_default_s` if nothing matched | `dwell_default_s` | yes |
| `s_dwell_ewma_s` | EWMA (`dwell_ewma_alpha`) of S's previous matched dwell values (prior) | `dwell_default_s` | |
| `s_matched_n` | Number of distinct credits consumed by this debit | 0 | |
| `s_matched_uniq_src` | Distinct senders of the consumed credits | 0 | |
| `s_new_payee` | 1 if S never paid this receiver before (within `payee_memory_n` memory) | 1 | yes |
| `s_new_payee_share_24h` | Share of S out-events in 24h (+cur) that went to payees new at the time | 1 | |
| `s_round_share_24h` | Share of S out-events in 24h (+cur) with `t_is_round = 1` | 0 | |
| `s_amt_vs_median` | `clip(amount / median(last amount_history_n S out amounts, prior), 0, 50)`; 1 if no history | 1 | |

#### D group (receiver account history; 12)

Let *D* = `ev.dst`.

| Name | Definition | Default | P0 |
|---|---|---|---|
| `d_age_s` | `ts - first_seen(D)`; 0 if never seen | 0 | |
| `d_idle_s` | `ts - last_seen(D)`; 0 if never seen | 0 | |
| `d_first_credit` | 1 if D never received a credit before | 1 | |
| `d_in_cnt_10m`, `d_in_cnt_1h` | Credits to D in the window **(+cur)** | 1 | `_10m` only |
| `d_in_uniq_1h` | Distinct payers of D in 1h **(+cur)** | 1 | yes |
| `d_in_uniq_new_1h` | Among D's payers in 1h **(+cur)**, how many were **first-time payers** (their first-ever payment to D fell in that window) | 1 | yes |
| `d_in_amt_1h` | Sum of credits to D in 1h **(+cur)** | `amount` | |
| `d_out_cnt_1h` | Debits by D in 1h (prior) | 0 | |
| `d_out_uniq_1h` | Distinct receivers of D's debits in 1h (prior) | 0 | |
| `d_pass_ratio_1h` | `clip(D out amount in 1h / D credit amount in 1h, 0, 2)` using **prior** state for both; 0 if no prior credit | 0 | yes |
| `d_dwell_ewma_s` | D's dwell EWMA (prior) | `dwell_default_s` | |

#### E group (the pair; 3)

| Name | Definition | Default | P0 |
|---|---|---|---|
| `e_pair_out_cnt` | Prior transactions from S to D remembered in S's counterparty memory | 0 | |
| `e_reverse_recent` | 1 if S received from D within the last 24h (round-trip signal) | 0 | |
| `e_amt_vs_pair_mean` | `clip(amount / mean amount of prior S-to-D payments, 0, 50)`; 1 if none | 1 | |

#### G group (graph; 9; used by G2 only)

| Name | Definition | Default | P0 |
|---|---|---|---|
| `g_trail_depth` | Depth of this transaction in a money trail (§9.7): 0 if the sender paid from its own money, else 1 + the deepest consumed credit, capped at `trail_depth_cap` | 0 | yes |
| `g_funnel_in` | Estimated number of origin senders whose money feeds this debit (§9.7), capped at `funnel_cap` | 1 | |
| `g_src_susp` | 1 if S is currently flagged suspicious (§9.7) | 0 | yes |
| `g_dst_susp` | 1 if D is currently flagged suspicious | 0 | yes |
| `g_nbr_in_susp_cnt` | Number of S's distinct payers in the last hour (scan at most `nbr_scan_cap`) that are currently suspicious | 0 | yes |
| `g_nbr_out_susp_cnt` | Number of S's distinct recent payees (paid within 1h, scan at most `nbr_scan_cap`, excluding D) that are currently suspicious | 0 | |
| `g_dst_nbr_in_susp_cnt` | Number of D's distinct payers in the last hour, excluding S, that are currently suspicious | 0 | |
| `g_flow_comp_size` | `max(size of S's flow component, size of D's)`, capped at `flow_comp_cap` (§9.7); 1 for singletons | 1 | |
| `g_flow_comp_edges` | Number of flow edges inside the larger of the two components, capped at `flow_comp_cap` | 0 | |

The G group has exactly 9 features, in the order of this table.

**Counts (P1):** T 13 + S 22 + D 12 + E 3 + G 9 = 59. **P0 counts:** T 13, S 6 (`s_out_cnt_1h, s_in_uniq_1h, s_pass_ratio_1h, s_matched_share, s_dwell_s, s_new_payee`), D 4 (`d_in_cnt_10m, d_in_uniq_1h, d_in_uniq_new_1h, d_pass_ratio_1h`), E 0, G 4 (`g_trail_depth, g_src_susp, g_dst_susp, g_nbr_in_susp_cnt`) = 27. A unit test asserts these counts against `registry.py`.

### 9.4 State model

Two layers, both in `features/state.py`.

**Light registry** (one small record per account ever seen, never evicted; a few floats): `first_seen: float`, `last_seen: float`, `susp_until: float` (0 if never flagged). Used for ages, idle times and suspicion lookups of neighbours. At the scale of this project (hundreds of thousands of accounts) this is a few tens of MB; document the real figure in `results/` (§15) and note that a production system would shard or expire it.

**Heavy state** (`AccountState`, `__slots__`, created on first use, evicted when idle for `state_ttl_s`):

| Field | Type | Contents | Bound |
|---|---|---|---|
| `out` | `deque` | `(ts, dst, amount, new_payee, is_round)` per debit | `deque_cap` |
| `inc` | `deque` | `(ts, src, amount, first_contact)` per credit | `deque_cap` |
| `credits` | `deque` of lists | `[ts, remaining, src, depth, origins]`, the FIFO of unspent credits | `deque_cap` |
| `cp` | `OrderedDict` | counterparty id to `[last_out_ts, last_in_ts, out_cnt, in_cnt, out_amt_sum]`; most recently touched at the end | `payee_memory_n` (LRU) |
| `amt_hist` | `deque` | last `amount_history_n` debit amounts | `amount_history_n` |
| `dwell_ewma` | float | EWMA of dwell | 1 |

Eviction sweep: when `int(ts // 3600)` increases, remove heavy state of accounts with `last_seen < ts - state_ttl_s`. Because `state_ttl_s` (48 h) exceeds the longest window (24 h) and the FIFO lookback, windowed features are unaffected by eviction; only memories longer than the TTL (counterparty memory, dwell EWMA) reset, which is documented behaviour and tested.

Window scans: events are appended in time order, so a window is the suffix of the deque. Scan from the right and stop at the first event outside the **24 h** window, computing all of 10 m / 1 h / 24 h counts, sums and distinct sets in **one pass**. Never rescan per feature.

### 9.5 Per-event algorithm

```python
def step(self, ev: Event) -> dict[str, float]:
    ts = max(ev.ts, self.clock)
    self.clock = ts
    self._tick(ts)  # flow rebuild (§9.7) and hourly TTL sweep, keyed on floor(ts / interval)
    rs, rd = self._reg(ev.src), self._reg(ev.dst)  # light records (created empty)
    S, D = self._state(ev.src), self._state(ev.dst)  # heavy state (created empty)

    # ---- READ PHASE: nothing below mutates state ----
    f = {}
    f.update(self._t_features(ev, ts))  # group T
    so, si = scan(S.out, ts), scan(S.inc, ts)  # one pass each, all windows
    do, di = scan(D.out, ts), scan(D.inc, ts)
    f.update(self._s_features(ev, ts, rs, S, so, si))  # group S (adds the +cur terms)
    plan = plan_fifo(S, ev.amount, ts, self.cfg)  # §9.6, read-only
    f.update(self._fifo_features(plan))  # s_matched_*, s_dwell_s, depth, origins
    f.update(self._d_features(ev, ts, rd, D, do, di))  # group D
    f.update(self._e_features(ev, ts, S))  # group E (from S.cp)
    if self.use_graph:
        f.update(
            self._g_features(ev, ts, S, D, so, si, di, plan)
        )  # group G (suspicion, neighbours, flow comps)

    # ---- APPLY PHASE: single place where state changes, always in this order ----
    commit_fifo(S, plan, ts, self.cfg)  # consume credits, pop emptied/expired
    S.out.append((ts, ev.dst, ev.amount, new_payee, is_round))
    S.amt_hist.append(ev.amount)
    D.inc.append((ts, ev.src, ev.amount, first_contact))
    D.credits.append(
        [ts, ev.amount, ev.src, plan.depth, plan.origins]
    )  # the credit carries the trail depth and origins
    self._touch_cp(S, ev.dst, out=True, ts=ts, amount=ev.amount)  # LRU update of S.cp
    self._touch_cp(D, ev.src, out=False, ts=ts, amount=ev.amount)
    if plan.matched_share > 0:
        S.dwell_ewma = alpha * plan.dwell + (1 - alpha) * S.dwell_ewma
    if suspicion_rule(f, cfg):  # §9.7, uses features computed above
        rs.susp_until = ts + cfg.susp_ttl_s
    rs.first_seen = rs.first_seen or ts
    rd.first_seen = rd.first_seen or ts  # None-safe in real code
    rs.last_seen = rd.last_seen = ts
    if plan.matched_share >= cfg.flow_edge_min_matched and not hub(S) and not hub(D):
        self._add_flow_edge(ts, ev.src, ev.dst)  # §9.7
    return f
```

Notes the implementation must honour:

- **new_payee** = the receiver is not in `S.cp` or `S.cp[dst].out_cnt == 0`, evaluated before `_touch_cp`. **first_contact** = the sender is not in `D.cp` or `D.cp[src].in_cnt == 0`, evaluated before the update.
- `d_in_uniq_new_1h` counts distinct payers among D's in-events in the last hour whose stored `first_contact` flag is 1, plus the current event if its `first_contact` is 1 and its sender is not already counted.
- `hub(X)` = (distinct counterparties of X in the last hour, from `s_out_uniq_1h`-style scans: distinct payees + distinct payers) exceeds `hub_degree_cap`. Use the sums already computed in the read phase (an upper bound; that is fine and deterministic).
- `susp_until` is only ever set by the rule in §9.7; neighbours are checked by comparing `susp_until > ts`.
- All dictionary iteration that influences output (distinct sets, neighbour scans) must be deterministic: iterate deques/OrderedDicts in their insertion order, never over a raw `set`.
- Pure Python only. `math`, `collections`, `bisect`, `statistics.median` are fine. No numpy in this module (the serving image has none).
- Public helpers: `FeatureEngine(cfg, detector_names=("G2",))` (computes the union of columns the named detectors need; `use_graph` is true when G2 is among them), `vector(f, names) -> list[float]`, `stats() -> dict` (accounts in registry, active heavy states, approximate bytes), `account_view(account_id) -> dict` (for the UI side panel: windows, FIFO head, suspicion flag, first/last seen).

### 9.6 FIFO credit consumption

Money is treated as first-in, first-out: a debit is funded by the oldest unspent credits that are still inside `fifo_lookback_s` (default 24 h). This is a modelling convention, stated as such in the docs. Credits older than the lookback are expired (dropped from the left) and never match.

```python
def plan_fifo(S, amount, ts, cfg) -> Plan:
    remaining = amount
    takes = []  # takes: list of (credit_list_ref, take_amount)
    for c in S.credits:  # oldest first
        if ts - c[0] > cfg.fifo_lookback_s:  # expired: skip (apply phase pops it)
            continue
        if remaining <= 1e-9:
            break
        t = min(c[1], remaining)
        if t > 1e-9:
            takes.append((c, t))
            remaining -= t
    matched = amount - remaining  # >= 0
    share = matched / amount
    if matched > 0:
        dwell = sum(t * (ts - c[0]) for c, t in takes) / matched
        n = len(takes)
        uniq = len({c[2] for c, _ in takes})
        keep = [c for c, t in takes if t / matched >= cfg.trail_min_share]
        if share >= cfg.flow_edge_min_matched:  # S passed money through
            depth = min(cfg.trail_depth_cap, 1 + max((c[3] for c in keep), default=0))
            origins = min(cfg.funnel_cap, sum(c[4] for c in keep) or 1)
        else:
            depth, origins = 0, 1
    else:
        dwell, n, uniq, depth, origins = cfg.dwell_default_s, 0, 0, 0, 1
    return Plan(share, dwell, n, uniq, depth, origins, takes)


def commit_fifo(S, plan, ts, cfg):
    for c, t in plan.takes:
        c[1] -= t
    while S.credits and (S.credits[0][1] <= 0.01 or ts - S.credits[0][0] > cfg.fifo_lookback_s):
        S.credits.popleft()
```

Details to get right:
- Credits with `remaining` below 0.01 are treated as empty. Partial consumption keeps the credit at the head.
- The credit created for the receiver D carries `depth` and `origins` of **this** transaction (that is how trail depth propagates one hop at a time without graph search).
- A victim paying out of its own balance will usually have `share` near 0 and `depth = 0`. A mule forwarding fresh money has `share` near 1, `dwell` of minutes, and `depth >= 1`.
- Edge cases to test: debit larger than all credits (share below 1); many tiny credits; a credit exactly consumed; credits expiring between two debits; zero credits; equal timestamps.

### 9.7 Suspicion, trail depth, funnel, flow components

**Suspicion flag (rule-based).** After scoring a debit by S, if all three hold, set `susp_until(S) = ts + susp_ttl_s`:
1. `s_matched_share >= susp_matched_share_min` (0.7),
2. `s_dwell_s <= susp_dwell_max_s` (900 s),
3. `s_in_uniq_1h >= susp_min_in_uniq_1h` (2, prior state).

`suspicious(X, t)` is `susp_until(X) > t`. This is a deliberately simple "looks like a relay" rule. It will also fire on some traders, family hubs and agents (they are hard negatives by design); G2's job is to use it together with other signals. Do not tune these four numbers on the test split; if tuned at all, tune on validation and log it in `docs/decisions.md`.

**Trail depth and funnel width** are the `depth` and `origins` fields of the Plan (§9.6):
- `g_trail_depth`: 0 when the sender paid mostly from its own funds (`share < flow_edge_min_matched`); otherwise `1 + max(depth of credits that supplied at least trail_min_share of the matched amount)`, capped at `trail_depth_cap`. A three-hop mule chain therefore shows depths 0, 1, 2, 3 along the trail.
- `g_funnel_in`: if relaying, the sum of the `origins` of the consumed credits (capped at `funnel_cap`); else 1. It is an estimate: the same origin reached over two paths is counted twice, and a partially consumed credit contributes its full `origins` each time. Document it as an estimate in the data card.

**Flow components.** A *flow edge* `(S, D, ts)` exists when the debit passed money through (`share >= flow_edge_min_matched`) and neither endpoint is a hub. Components are the connected components of flow edges seen in the last `flow_window_s`.
- Incremental: a union-find (dict-based, path compression, union by size) is updated in the apply phase. The features read the structure **before** the current edge is added.
- Rebuild: when `int(ts // flow_rebuild_every_s)` increases, rebuild the union-find from scratch from the flow-edge deque, dropping edges with `ts_e <= tick_time - flow_window_s`, where `tick_time = tick_id * flow_rebuild_every_s`. The rebuild happens **before** the current event is processed, using only edges with `ts_e < ts`. This makes the structure a deterministic function of the event stream.
- `g_flow_comp_size` = `min(flow_comp_cap, max(size(find(S)), size(find(D))))`, with 1 for accounts not in any edge. `g_flow_comp_edges` = number of flow edges in the larger component, capped; track per-root edge counts during union and recompute at rebuild.
- The flow-edge deque is bounded (maxlen 500,000); log a warning if it ever fills.

### 9.8 Registry module and configuration flags

`features/registry.py` holds the single source of truth:

```python
@dataclass(frozen=True)
class FeatureSpec:
    name: str; group: str          # "T","S","D","E","G"
    default: float | str           # a number, or the name of a cfg value such as "dwell_default_s"
    priority: str                  # "P0" or "P1"
    doc: str

FEATURES: tuple[FeatureSpec, ...]  # canonical order = order of the tables in §9.3
def feature_names(detector: str, priority: str = "P1") -> list[str]   # "B0" -> T; "B1" -> T,S,D,E; "G2" -> all
def registry_hash(names: list[str], cfg_features: dict) -> str         # SHA-256 over names + the [features] values that change semantics
```

`configs/features.toml` may only lower priorities or disable a feature for an ablation (`disabled = ["g_flow_comp_size", ...]`); disabled features are removed from the vector and change the registry hash. The model JSON stores `feature_names` and `registry_hash`; `ScoringEngine` refuses to start if the hash computed from the current config differs from the model's, with a message that says which side to retrain.

### 9.9 Building feature matrices

`features/matrix.py` replays a world through the engine once and writes everything detectors, thresholds and evaluation need.

```bash
chainwatch features --world data/processed/world_medium_seed0     # writes data/processed/features_world_medium_seed0/
```

Algorithm: read `events.csv.gz`, sort check, create `FeatureEngine(cfg, ("G2",))` (computes all 59), call `step()` for every event **including burn-in** (burn-in rows are processed but not stored). For each stored row, keep the full feature vector (float64), label, timestamp and identifiers. Split by event time (`eval.split_days`, §8.5): train `[burn_in_days, 15)`, validation `[15, 20)`, test `[20, 30)`.

Output directory (all `.npy`, no pickles, so they load with `mmap_mode="r"`):

```text
features_<world_tag>/
  names.json            # ordered feature names (G2 order); B0 and B1 are column subsets
  meta.json             # config_hash, registry_hash, world file SHA-256, row counts per split, replay seconds
  train_X.npy  val_X.npy  test_X.npy            # float64, shape (n, 59)
  {split}_y.npy                                  # int8, is_fraud
  {split}_ts.npy  {split}_event_id.npy           # float64, int64
  {split}_ring_id.npy  {split}_hop.npy           # int32, int16 (analysis only)
  {split}_src.npy  {split}_dst.npy               # fixed-width unicode ('<U12'), needed by the ring evaluation
  {split}_amount.npy  {split}_type.npy           # float64, int8 (type code = index in ["TRANSFER","PAYMENT","CASH_OUT","CASH_IN","DEBIT"])
```

Disk and time (expectations, UNVERIFIED until measured, record real figures in `docs/STATUS.md`): the medium world is about 0.7 GB of features per seed; a replay of about 1.5 million events is expected to take minutes to tens of minutes in pure Python. Log progress every 100,000 events with rate and ETA (rule E9). Provide `--limit N` and `--small` flags. Seeds can be replayed in parallel processes (one process per world); the engine itself stays single-threaded.

For PaySim the same command applies (`--world data/processed/paysim`), after `chainwatch paysim-convert` (§18.2) writes the canonical stream and a `meta.json` with the PaySim step-based split.

### 9.10 Correctness and performance requirements

- **Equivalence test:** on `world_small` events (first 20,000) and on 5 hand-built edge-case streams, `FeatureEngine` equals `reference.py` for **all** 59 features within 1e-9, with caps set huge. The reference recomputes every feature from the list of all prior events using plain loops (including a plain BFS for flow components at the same tick boundaries).
- **Causality test:** for random cut points *i*, features of events `0..i` computed on the prefix equal those computed on the full stream; and changing or deleting any event after *i* does not change them.
- **Determinism:** two runs give byte-identical matrices.
- **Cap tests:** with small caps, counters saturate exactly as documented; eviction resets only the documented memories.
- **No bad values:** `math.isfinite` for every feature of every event in the small world.
- **Speed targets (to be measured, §15; do not state as results before measuring):** P0 under about 150 microseconds per event and P1 under about 300 microseconds per event on a laptop in the single-threaded engine. If P1 misses badly, profile before removing features: the usual culprits are repeated deque scans (fix: one pass), `statistics.median` on lists (fix: cap at `amount_history_n`), and string concatenation in keys.

---

## 10. Models

Gradient-boosted trees (LightGBM) are **trained** in the train environment (x86_64, §5.1) and **served** by our own pure-Python evaluator reading a JSON file. The serving image never imports LightGBM, numpy or scikit-learn (they have no s390x wheels for current releases, §3.2).

### 10.1 Detector ladder

| Id | Features | Ring logic | Purpose |
|---|---|---|---|
| `B0` | T (13) | no | Weak reference: one payment at a time |
| `B0+R` | T | yes | Shows ring logic alone cannot rescue a weak scorer |
| `B1` | T + S + D + E (50) | no | **Strong fair baseline** (account history, no graph) |
| `B1+R` | T + S + D + E | yes | Isolates the gain from ring grouping on top of a strong scorer |
| `G2` | all (59) | no | Graph features only |
| `G2+R` | all | yes | **The full system** (what the API serves) |

Three models are trained (B0, B1, G2); the `+R` variants reuse the same scores and add the ring layer (§11), with their own thresholds (§10.4, §11.6).

### 10.2 Training procedure (`model/train.py`)

```bash
chainwatch train --features data/processed/features_world_medium_seed0 --out models/default --detectors B0,B1,G2
```

1. **Load** `train_X.npy` (mmap) and `val_X.npy`; select columns by `feature_names(detector)`.
2. **Training rows:** all positives (`is_fraud = 1`) and a seeded random `neg_downsample` (0.2) fraction of negatives (`np.random.default_rng(seed)`); weights: positives 1.0, kept negatives `1 / neg_downsample` so scores approximate probabilities on the real class balance. Refuse to train (clear error) when positives in the training split are under 200 and tell the user to use a larger preset.
3. **Validation set:** the full validation split (no downsampling, weight 1) for early stopping on `average_precision`.
4. **Parameters** (from `[model]`):

```python
params = dict(
    objective="binary",
    metric="average_precision",
    learning_rate=lr,
    num_leaves=num_leaves,
    max_depth=max_depth,
    min_child_samples=min_child_samples,
    subsample=subsample,
    subsample_freq=1,
    colsample_bytree=colsample_bytree,
    reg_lambda=reg_lambda,
    max_bin=255,
    seed=seed,
    deterministic=True,
    force_row_wise=True,
    use_missing=False,
    zero_as_missing=False,
    num_threads=n_jobs,
    verbosity=-1,
)
booster = lgb.train(
    params,
    dtrain,
    num_boost_round=n_estimators,
    valid_sets=[dval],
    callbacks=[lgb.early_stopping(early_stopping_rounds), lgb.log_evaluation(50)],
)
```

5. **Ship size:** export `min(best_iteration, ship_max_trees)` trees (`num_iteration=...`). If truncated, compute validation PR-AUC both ways and store both in the report (honest accuracy cost of the latency cap).
6. **Parity gate:** after export, load the JSON with `PortableGBDT` and compare against `booster.predict` on 5,000 random validation rows; maximum absolute difference in probability must be below 1e-7 or training fails.
7. **Outputs:** `models/default/<det>.json` (§10.3) and `models/default/train_report_<det>.json` (hyperparameters, `best_iteration`, shipped trees, train/validation PR-AUC, feature importance by gain, wall time, row counts, library versions).

Fairness rule (H2): all three detectors share the same hyperparameters, the same early stopping and the same data split. If any tuning is done (for example `num_leaves` in {15, 31, 63}, chosen on validation PR-AUC), run the **same** grid for each detector and log the winning values. No tuning on the test split, ever.

Scores are ranking scores, not guaranteed calibrated probabilities. The UI shows `risk` as `round(100 x score)` and says so; do not call it a probability in the UI or docs.

### 10.3 Portable model format (`model/export.py`, `model/portable_gbdt.py`)

JSON file, format name `chainwatch-gbdt-1`:

```json
{
  "format": "chainwatch-gbdt-1",
  "detector": "G2",
  "feature_names": ["t_amount", "t_log_amount", "..."],
  "registry_hash": "sha256:...",
  "objective": "binary",
  "sigmoid": 1.0,
  "trained_on": {"world": "world_medium_seed0", "config_hash": "sha256:...", "train_rows": 0, "pos_rows": 0, "lightgbm": "x.y.z"},
  "n_trees": 300,
  "trees": [
    {"feature": [12, 5, 40], "threshold": [0.5, 3.2, 1.0], "left": [1, -1, -3], "right": [2, -2, -4],
     "leaf": [0.01, -0.2, 0.3, 0.7]}
  ]
}
```

Tree encoding (flat arrays per tree): node `i` tests `x[feature[i]] <= threshold[i]`; if true go to `left[i]`, else `right[i]`. A child value `>= 0` is an internal node index; a negative value `c` means leaf number `~c` (so `-1` is leaf 0, `-2` is leaf 1). `leaf[j]` is the leaf output. A tree with only a root leaf is encoded with empty `feature` and a single `leaf`, plus the field `"root_leaf": true`.

Raw score is the sum of leaf outputs over all trees (LightGBM folds the initial score into the first tree; verify this with the parity test, and if the installed version stores it separately, add it as `"base_score"` in the JSON and in the evaluator). Probability = `1 / (1 + exp(-sigmoid x raw))`; use a numerically safe form for large negative `raw`.

**Exporter** (`export.py`, train env): read `booster.dump_model(num_iteration=k)`; walk each `tree_structure` iteratively; **assert** `decision_type == "<="` for every internal node, `missing_type == "None"` (or irrelevant because `use_missing=False`), and that no categorical splits exist; otherwise raise. Thresholds are written as Python floats (`json.dump` writes shortest round-trip repr, which is exact for doubles). Do not round.

**Evaluator** (`portable_gbdt.py`, serve env, pure Python, no third-party imports):

```python
class PortableGBDT:
    @classmethod
    def load(cls, path) -> "PortableGBDT": ...  # validates format, feature_names, tree encoding
    def predict_raw(self, x: Sequence[float]) -> float:
        s = 0.0
        for feat, thr, left, right, leaf in self._trees:  # tuples of lists, prebound
            i = 0
            while i >= 0:
                i = left[i] if x[feat[i]] <= thr[i] else right[i]
            s += leaf[~i]
        return s

    def predict(self, x) -> float: ...  # sigmoid
    def predict_many(self, rows) -> list[float]: ...
```

Optimisation allowed after correctness is proven by the parity test (and only then): compile each tree to nested `if/else` Python source with `exec` (often 2 to 3 times faster than the list-walking loop), keeping the loop version as the reference in tests. Another allowed optimisation: reorder nothing; never change numerical semantics.

Parity requirements (tests in §17): LightGBM `predict` versus `PortableGBDT.predict` on 10,000 rows, max abs diff below 1e-7; also exact leaf agreement for all rows (compare LightGBM `pred_leaf=True` against the evaluator's leaf indices); plus a hand-built tree test with boundary values (`x == threshold` goes left).

### 10.4 Thresholds (`model/thresholds.py`) and tiers

Thresholds are computed on the **validation split** only (H5), per variant key (`B0`, `B0+R`, `B1`, `B1+R`, `G2`, `G2+R`).

Base procedure (no rings; used for `B0`, `B1`, `G2`, and as the P0 procedure for `+R` variants):
- Let `neg` be the scores of validation transactions with `is_fraud = 0`.
- `verify = np.quantile(neg, 1 - fpr_verify, method="higher")`, `hold = np.quantile(neg, 1 - fpr_hold, method="higher")`, `edge = np.quantile(neg, 1 - fpr_edge, method="higher")`; enforce `hold >= verify`.
- Decision: `score >= hold` gives `hold`; else `score >= verify` gives `verify`; else `allow`. Ties go to the higher tier. Because of ties the achieved FPR can differ slightly from the target; store the achieved validation FPR next to each threshold and report both validation and test achieved FPR (the test figure is a report, not a tuning input).

`+R` variants: `ring_alert` and the escalation-aware calibration are in §11.6.

`thresholds.json` schema:

```json
{
  "format": "chainwatch-thresholds-1",
  "created_from": {"world": "world_medium_seed0", "split": "validation", "config_hash": "sha256:..."},
  "variants": {
    "G2+R": {"verify": 0.0, "hold": 0.0, "edge": 0.0, "ring_alert": 0.0,
             "val": {"n_neg": 0, "n_pos": 0, "fpr_verify": 0.0, "fpr_hold": 0.0, "false_rings_per_day": 0.0},
             "calibration": {"mode": "simple", "iterations": 0, "fpr_verify_effective": 0.0}}
  }
}
```

The serving engine uses variant `G2+R` for production decisions and, when compare mode is on, `B0` and `B1` thresholds for the shadow decisions.

### 10.5 Benign profile (`model/profile.py`)

From validation transactions with `is_fraud = 0`, store for every feature in the G2 order: `n` and a quantile grid `q` at the levels `[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999]` (nearest-rank, plain floats) in `models/default/benign_profile.json`. `profile.py` (pure Python, serve env) exposes `rank(feature, value) -> float`, the approximate share of legitimate transactions at or below `value`, by bisecting the grid and interpolating linearly (clamped to 0.0005 and 0.9995 outside the grid), and `typical(feature) -> float` (the median). Reason codes (§12) use these so the text can say "faster than about 99% of normal transactions" without any SHAP dependency.

Optional P1: `PortableGBDT.contributions(x)` returns per-feature path contributions (Saabas-style: along the decision path add `value(child) - value(parent)` to the split feature), computed in pure Python; used only to order reasons when several rules fire. Not needed for the first version.

### 10.6 Model package layout

```text
models/default/
  B0.json  B1.json  G2.json                 # portable trees (each well under 5 MB; keep ship_max_trees small enough)
  thresholds.json                           # §10.4
  benign_profile.json                       # §10.5
  manifest.json                             # config_hash, registry_hash, world file hashes, LightGBM and Python versions,
                                            # created_at (UTC ISO), SHA-256 of each file above, "provenance": "ChainWorld"
  train_report_B0.json  train_report_B1.json  train_report_G2.json
```

`models/default` is trained on `world_medium_seed0`. The demo replay world (`seed 2026`, small) is never used for training, tuning or threshold fitting. `scripts/verify_models.py` (called by `make verify-models`) recomputes the SHA-256 values and the registry hash and fails on mismatch. The serving engine accepts `CHAINWATCH_MODEL_DIR` to point at another package (for example one trained on PaySim); the UI shows the model's `trained_on.world` so nobody mistakes which data a demo model came from.

---

## 11. Rings

A *ring* is a small connected group of accounts that, in a short time, takes money in and passes it on in a way the detector scores as suspicious. The ring layer sits on top of any detector's per-transaction scores (that is how `B0+R`, `B1+R`, `G2+R` are formed).

### 11.1 Definitions

- **Flagged edge:** a scored transaction `src -> dst` whose **base** score (before any ring escalation) is at least the detector's `edge` threshold. Only flagged edges enter the grouper.
- **Linking endpoints of an edge:** `src` always; `dst` too unless the edge `type` is in `[rings].terminal_types` (`CASH_OUT`, `PAYMENT`, `DEBIT`). A receiver of a terminal edge is an *exit*: it is included as a member of the ring but it does not link other edges. Also, an account with more than `[rings].hub_degree_cap` distinct flagged counterparties inside `ttl_s` is a *hub* and never links.
- **Link rule:** two flagged edges are in the same ring when they share a **linking** endpoint and their timestamps differ by at most `link_window_s`. (Time-bounded, so a busy account does not glue together unrelated hours.) Formally, keep per account the timestamp and ring of its latest flagged edge as a linking endpoint; a new edge at time `t` joins (and merges) the rings of the endpoints whose latest linking edge is within `link_window_s` of `t`.
- **Relay (pass) account:** a member that is the receiver of at least one flagged edge and later the sender of at least one flagged edge, the outgoing edge within `link_window_s` of the incoming one.
- **Roles (shown neutrally in the UI, inferred from structure only, never from labels):** `entry` (flagged money out, none in), `relay`, `exit` (flagged money in, none out; typically the cash-out or merchant end).
- **Qualifies as a ring:** at least `min_accounts` accounts, at least `min_flagged_edges` edges and, when `require_pass_node = true`, at least one relay. Components that do not qualify stay tracked as *candidates* (not shown, not escalated) because the next edge may complete them.

### 11.2 Incremental algorithm (`rings/grouping.py`)

State: a dict-based union-find over **edge ids**; per component: member accounts (with role counters), edge list, `first_ts`, `last_ts`, `peak_score`, `status`, `alert_emitted`; `last_link[account] = (ts, edge_id)`; deque of flagged edges for expiry; an index `account -> ring_id` for lookups.

`add_edge(ev, score)`:
1. Create edge id; push to the deque.
2. For each linking endpoint `x` of the edge (skipping hubs): if `last_link[x]` exists and `ts - last_link[x].ts <= link_window_s`, `union(edge, last_link[x].edge)`. Then set `last_link[x] = (ts, edge)`.
3. Add both endpoints to the component's members; update role counters and the relay set (a member that now has both a flagged in-edge and a flagged out-edge within `link_window_s`).
4. Recompute the component's `ring_score` (§11.3); update `peak_score`; evaluate the lifecycle (§11.4); return the list of `RingEvent`s produced (create, update, merge, alert).

`tick(ts)`: at each `rebuild_every_s` boundary (`int(ts // rebuild_every_s)` increases): drop edges with `ts_e < ts - ttl_s`; if any expired, rebuild the components from the remaining edges (same rules) and re-map ring ids so identities are stable: each new component takes the id of the old ring with the largest edge overlap (ties: the smaller id) if that id is not already taken, else a new id. Rings with no remaining edges are `closed`. Closed rings stay in a bounded history (last 200) for the UI and metrics.

Merging two rings keeps the smaller (older) id, records `merged_from`, and emits a `merge` event. Alert status is kept if either side had alerted.

`member_floor(src, dst)` (for escalation, §11.5) is O(1) through the `account -> ring_id` index.

All operations are deterministic functions of the ordered flagged-edge sequence and the tick times.

### 11.3 Ring score (`rings/scoring.py`)

```python
def noisy_or(scores: list[float], k: int) -> float:
    p = 1.0
    for s in sorted(scores, reverse=True)[:k]:
        p *= 1.0 - min(max(s, 0.0), 1.0)
    return 1.0 - p


def ring_score(edge_scores: list[float], n_relays: int, k: int) -> float:
    return noisy_or(edge_scores, k) * (1.0 - 0.5**n_relays)
```

Meaning: strong evidence from the best few edges, discounted when there is little structural evidence (zero relays gives 0, one relay 0.5, two 0.75, three 0.875). Because of the discount, the validated `ring_alert` threshold for a detector is usually well below 1; it is chosen from data, not guessed (§11.6). Unit-test the numbers (for example scores 0.5, 0.5 and 1 relay: noisy-OR 0.75, ring score 0.375).

### 11.4 Lifecycle and alert object

Status: `candidate` (does not qualify) -> `open` (qualifies, `ring_score < ring_alert`) -> `alert` (`ring_score >= ring_alert`) -> `closed` (no flagged edge for `ttl_s`). A ring emits:
- `alert` once, when it first reaches the threshold;
- `update` when membership changes or `ring_score` grows by at least `update_delta`;
- `merge` when two rings join;
- `close` at expiry.

`RingAlert` (pydantic model in `schema.py`; also the REST/WebSocket payload, §13):

| Field | Meaning |
|---|---|
| `ring_id` | Stable id such as `R0000123` |
| `status` | `open`, `alert` or `closed` |
| `first_ts`, `last_ts`, `alert_ts` | Times of first edge, last edge, and first crossing of the threshold |
| `ring_score`, `peak_score` | Current and best score |
| `accounts` | Each: `id`, `role` (`entry`/`relay`/`exit`), `in_amount`, `out_amount`, `first_ts` |
| `edges` | Up to `max_edges_in_alert` flagged edges (highest amounts first): `event_id`, `ts`, `src`, `dst`, `amount`, `type`, `score` |
| `n_accounts`, `n_edges`, `n_relays` | Counts |
| `inflow`, `outflow`, `exit_amount` | Sums over flagged edges; `exit_amount` = amount on terminal edges |
| `longest_chain` | Number of edges on the longest time-ordered path through flagged edges (DAG longest path; cycles in round trips are cut by time order) |
| `reasons` | Up to 3 plain-text reasons (§12.3) |
| `merged_from` | Ids merged into this ring |
| `truth` | **Only in labeled replay for the UI overlay:** ground-truth `ring_id`, typology and purity. Never present in `/v1/score` responses; omitted when the engine runs unlabeled |

Neutrality (rule D2): wording says "pattern", "accounts involved" and "recommended action", never "criminals" or "fraudsters". Entry accounts may be tricked account holders; the UI says so (§13.4).

### 11.5 Escalation

When `[rings].escalation = true`, and a transaction touches a member of a ring whose status is `alert`:
- if `src` is a member: effective score `= max(base_score, thresholds.hold)`, reason `RING_MEMBER_OUT` (stop money leaving);
- else if `dst` is a member: effective score `= max(base_score, thresholds.verify)`, reason `RING_MEMBER_IN` (check money still arriving).

`escalate_out_to` and `escalate_in_to` in the config select the tiers (`"hold"` and `"verify"` by default; the floor values are the matching thresholds). Escalation is evaluated on the ring state **before** the current event (the same order as §4 step 6). The **edge flag always uses the base score**, so escalation never feeds back into ring formation. This also keeps ring membership independent of the escalation setting, which makes the `+R` versus no-`R` comparison clean.

Cost of escalation: it can raise tiers on legitimate transactions of tricked entry accounts and of innocent accounts that were linked by a flagged edge. That is exactly why the evaluation counts escalated false alarms in the FPR (§11.6, §14).

### 11.6 Calibrating the ring layer on validation

For each detector `d` the evaluation protocol (and `chainwatch thresholds`) does the following on the **validation** split only, using precomputed base scores from the feature matrices (no re-featurising):

1. **Edge threshold** `edge = verify` from §10.4 (`fpr_edge` equals `fpr_verify` by default).
2. Run the grouper over the validation stream in time order with `ring_alert = 0` (so every qualifying ring is tracked) and record for every ring its `peak_score` and, using labels **offline only**, its purity (share of its flagged edges with `is_fraud = 1`).
3. A ring is **false** when purity is below `false_ring_purity` (0.5). For a candidate threshold `theta`, `false_rings(theta)` is the number of false rings with `peak_score >= theta`. Choose the smallest `theta` such that `false_rings(theta) / validation_days <= max_false_rings_per_day`. That is `ring_alert`.
4. **P0:** stop here; report FPR before and after escalation separately.
   **P1 (`calibrate_with_rings = true`): matched FPR.** With escalation on, extra legitimate transactions are raised, so the realised FPR exceeds the target. Bisect `fpr_verify_eff` in `(0, fpr_verify]` (at most `bisect_iters` iterations): derive `verify`, `edge` from `fpr_verify_eff` and `hold` from `fpr_hold x fpr_verify_eff / fpr_verify`; redo steps 2 and 3; replay the validation stream with escalation; measure FPR (share of legitimate transactions whose final tier is `verify` or `hold`); stop when it is within 2% relative of `fpr_verify` and not above it. Store `fpr_verify_effective` and the achieved FPR. All `+R` variants are therefore compared with their non-`R` counterparts at the **same final false-alarm rate** (this is what the PDF promised, §1.3 item 8).
5. Write the results into `thresholds.json` under the `+R` variant keys.

### 11.7 Interfaces

```python
class RingGrouper:
    def __init__(self, cfg: RingConfig, edge_threshold: float, ring_alert: float): ...
    def add_edge(self, ev: Event, score: float) -> list[RingEvent]
    def tick(self, ts: float) -> list[RingEvent]
    def member_floor(self, src: str, dst: str) -> str | None    # "hold", "verify" or None (escalation, §11.5)
    def snapshot(self, ring_id: str) -> RingAlert
    def rings(self, status: tuple[str, ...] = ("open", "alert")) -> list[RingAlert]
    def stats(self) -> dict                                       # counts for /v1/metrics
```

`RingEvent` carries `kind` (`create|update|merge|alert|close`), `ts`, `ring_id` and a snapshot. The offline evaluation drives the same class with precomputed scores (no HTTP), so the numbers in the report come from the code that the API runs.

### 11.8 Tests specific to rings (listed in §17)

Chain of three flagged edges forms a ring with 1 relay; a star of three independent senders into one receiver does not (no relay); terminal edges do not link; a hub account does not glue two rings; two rings merge through a shared relay inside `link_window_s` and do not merge outside it; expiry closes a ring and ids stay stable across a rebuild; noisy-OR numbers; escalation floors (out to `hold`, in to `verify`); determinism over a shuffled-then-resorted input; ring state is identical whether escalation is on or off; false-ring counting and the threshold search on a toy example; matched-FPR bisection converges on a toy example.
