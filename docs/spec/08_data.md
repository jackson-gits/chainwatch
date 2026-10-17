<!-- ChainWatch specification, §8. Part of CLAUDE.md; section numbers are global. -->

## 8. Data

Three sources feed the same canonical schema: **ChainWorld** (primary, our simulator), **PaySim** (secondary, public), **Organizer** (only if provided at kickoff). Every downstream module sees only the canonical schema.

### 8.1 Canonical event schema

An **Event** (what the serving engine sees; no labels):

| Field | Type | Rules |
|---|---|---|
| `event_id` | int | Unique. Ties on `ts` are ordered by `event_id` |
| `ts` | float | Seconds since simulation start (or any monotone clock). Never wall-clock |
| `src` | str | Opaque account id of the sender (the account debited) |
| `dst` | str | Opaque account id of the receiver (the account credited) |
| `amount` | float | Greater than 0, in INR |
| `type` | enum | `TRANSFER` (person to person), `PAYMENT` (to a merchant), `CASH_OUT` (customer to cash-out agent), `CASH_IN` (agent to customer), `DEBIT` (autopay or bill) |
| `src_bal_before`, `src_bal_after`, `dst_bal_before`, `dst_bal_after` | float or null | Null when unknown (merchants and agents in ChainWorld, merchants in PaySim) |

A **LabeledEvent** adds ground truth used only by replay, evaluation and the UI overlay. It is a separate type; the engine's input type must not have these fields (a test enforces it, §17):

| Field | Meaning |
|---|---|
| `is_fraud` | 1 if the transaction is part of a mule-ring campaign (victim to mule, mule to mule, or mule to sink) |
| `ring_id` | Campaign id or -1 |
| `hop` | Index along the money trail (victim to first mule = 0, then 1, 2, ...) or -1 |
| `src_role`, `dst_role` | `victim`, `mule`, `collector`, `sink`, `legit` (UI and analysis only) |

**Files.** Event streams are `csv.gz` with the columns `event_id,ts,src,dst,amount,type,src_bal_before,src_bal_after,dst_bal_before,dst_bal_after,is_fraud,ring_id,hop,src_role,dst_role`; empty string means null. Read and written with stdlib `csv` and `gzip` (so the same reader works in the serve env). Sorted by `(ts, event_id)`. `canonical.validate()` checks: sorted; amount > 0; type in enum; no duplicate ids; when both balances are known, `src_after = src_before - amount` within 0.01 (and the same for `dst`, `+ amount`).

Account metadata `accounts.csv.gz`: `account_id,archetype,is_mule,is_unwitting,first_ring_id`. Used only for analysis and the injection API. Never used as a feature.

### 8.2 ChainWorld (primary synthetic benchmark)

Goal: a payment network with **realistic confusers** and **controlled mule-ring structure**, so we can measure whether ring awareness helps. It is UPI-*like*, not UPI data. Make no claims about real UPI behaviour.

**Account IDs are opaque** (e.g. `A00012345`, assigned by a seeded random permutation). Nothing in an ID reveals archetype, role or mule status (rule D1).

#### 8.2.1 Archetypes

| Archetype | Count | Behaviour (what it looks like) | Why it is legitimate |
|---|---|---|---|
| `customer` | `n_customers` | Pays merchants and contacts, occasional cash-in or cash-out | Baseline population |
| `merchant` | `n_merchants` | Many distinct senders per day (30 to 300); pays out once a day around 22:00 to its owner | Long dwell; scheduled payout. Balance not tracked (null) |
| `trader` | `n_traders` | Business-hours in and out; pass-through ratio 0.6 to 0.95; many payees; amounts 5k to 80k | Dwell 20 min to 6 h; stable repeat payees |
| `agent` | `n_agents` | Cash-out agents: very high fan-in (customers cash out) and fan-out (cash-in to customers), many round amounts. Balance null | **Hardest confuser**: also the sink of real mule cash-outs, so the sender's behaviour, not the receiver, must carry the signal |
| `salary_payer` | `n_salary_payers` | Fan-out bursts on day 1 and the last day of each month to 50 to 400 employees | Funded from own balance, not short-dwell credits |
| `landlord` | `n_landlords` | Monthly fan-in of 3 to 10 within days 1 to 5 | No quick forwarding |
| `family_hub` | 1% of customers | Receives from 3 to 6 relatives, forwards 50 to 90% within hours to others | Dwell hours to days; stable payees |
| `split_bill` events | about 0.3 groups per 100 customers per day | One friend pays a merchant, 3 to 5 friends reimburse within 5 to 30 min, group forwards within 10 to 40 min | Fan-in plus quick forward, but to a stable merchant, roughly equal amounts |
| `charity_collect` events | 10 per world | 20 to 100 donors send 100 to 2,000 each within 2 h; collector pays out within 1 to 24 h (some under 15 min) | Honest flash crowd; tests false positives |
| `new_customer` | 5% of customers | Account first appears mid-simulation, small first transactions | Normal behaviour of a young account |
| `dormant_customer` | 3% of customers | Idle for 14 days or more, then active again | Normal behaviour after dormancy |

#### 8.2.2 Background (legitimate) activity

- Per-customer daily rate: `lambda_i ~ LogNormal(median 0.6, sigma 1.0)` events per day, multiplied by an hour-of-day weight and a calendar weight.
- Hour-of-day weights (illustrative shape, local time, 24 values starting at 00:00): `0.20 0.10 0.08 0.06 0.08 0.20 0.50 0.90 1.30 1.50 1.60 1.70 1.70 1.50 1.40 1.50 1.60 1.80 2.00 2.10 1.90 1.40 0.90 0.50`, normalised. Calendar weights: days 1 and 2 of month and last 2 days x1.5 for transfers and payments; weekends x1.2 for merchant payments (sim day 0 falls on weekday `world.t0_weekday`, where 0 is Monday; day-of-month is counted from sim day 0 as day 1). Hours are local: `local = ts + project.tz_offset_s` (§9.1 item 6).
- Type mix per customer event: `PAYMENT` 50%, `TRANSFER` 35%, `CASH_OUT` 8%, `CASH_IN` 6%, `DEBIT` 1% (illustrative; if the PaySim profile is available, replace with PaySim's shares and quantile-map amounts, see §8.3).
- Counterparties: each customer has a contact list of `3 + Geometric(0.3)` accounts; 85% of transfers go to contacts, 15% to new payees. Merchants chosen with a Zipf(1.1) popularity law.
- Amounts: `TRANSFER` LogNormal(median 800, sigma 1.1); `PAYMENT` LogNormal(median 250, sigma 0.9); `CASH_OUT` LogNormal(median 2000, sigma 0.8); `CASH_IN` LogNormal(median 3000, sigma 0.8). Clip to `[1, upi_txn_cap_inr]`. Some amounts rounded to multiples of 10, 50 or 100 (30% of transfers).
- Initial balance of tracked accounts: LogNormal(median 5000, sigma 1.0). Low balance raises the chance of a `CASH_IN`.
- `noise_level` scales sigma and randomness; `hard_negative_boost` scales the frequency of the confuser archetypes.

#### 8.2.3 Mule-ring campaigns (the fraud)

Each campaign has: a typology, a start time (uniform over days `[burn_in_days, days)` with a mild evening skew), victims, mule accounts, and a money trail ending in a sink.

Common parameters:

| Parameter | Default |
|---|---|
| Victims | `3 + Geometric(0.12)`, capped at 60 (mean about 10), drawn at random from ordinary customers |
| Victim amount | LogNormal(median 9000, sigma 0.9), clipped to `[500, 0.9 x victim balance, upi_txn_cap_inr]` |
| Campaign span (victim arrivals) | LogNormal(median 90 min, sigma 0.7), cap 8 h; arrival offsets `Beta(1.2, 2.5) x span` |
| First-layer mules `m1` | 1 to 4, victims assigned with Dirichlet(1) weights |
| Mule profile mix | `new` 40% (first seen 0 to 6 h before first credit), `dormant` 35% (idle at least 14 days), `active` 25% (ordinary user; unwitting) |
| Mule reuse | 25% chance an account is reused in a later campaign (at least 2 days later), up to 3 times |
| Unwitting flag | `active` mules and half of `dormant` mules (analysis only) |
| Sink | 85% `CASH_OUT` to an `agent` account; 15% `PAYMENT` to a `merchant` (gift-card style laundering) |

Typologies (weights in `[world.mix]`):

| Typology | Structure | Timing and amounts |
|---|---|---|
| `funnel` | victims -> L1 mules (1 to 4) -> collectors (1 to 2) -> sink | L1 forwards after delay LogNormal(median 6 min, sigma 0.7); with probability 0.5 a mule waits for 2 to 6 credits (or 20 min) and forwards once; forward fraction U(0.90, 0.99); collector cash-out after LogNormal(median 8 min, sigma 0.7) in 1 to 3 chunks |
| `layered_chain` | 3 to 15 victims -> mule 1 -> mule 2 -> ... (3 to 6 hops) -> sink | Per-hop delay LogNormal(median 4 min, sigma 0.6); per-hop skim U(0.01, 0.04) |
| `split_merge` | victims -> L1 (2 to 4) -> each splits to 2 or 3 L2 mules -> one collector (merge) -> sink | Delay LogNormal(median 5 min, sigma 0.7) |
| `round_trip` | victims -> mule A; A -> B -> C -> A (1 or 2 partial loops, 40 to 70% each) -> A -> sink | Delay LogNormal(median 7 min, sigma 0.7) |
| `structuring` | like `funnel` | Every forwarded amount capped at `U(0.85, 0.99) x structuring_cap_inr`; larger sums split into several transfers 1 to 5 min apart |
| `slow_roll` (evasive) | like `funnel` | Delay LogNormal(median 3 h, sigma 0.6); forwards in 3 to 5 chunks spaced 20 to 90 min; span up to 2 days. Designed to evade short windows; expected to be hard, report it honestly |

Labels: every campaign transaction gets `is_fraud=1`, `ring_id`, `hop`, roles. Victim legitimate activity is unlabeled (0).

#### 8.2.4 Generation algorithm (follow this order)

1. Build population and archetypes; assign opaque ids via a seeded permutation.
2. Generate **intended** background events day by day with numpy (time, src, dst, type, intended amount).
3. Plan campaigns (accounts, schedule, structure) as **intents**: fixed intents (victim sends absolute amount) and **forward intents** (`fraction`, `pool=(campaign, account)`, planned time).
4. Merge all intents, sort by `(ts, tie-break)`.
5. **One sequential balance pass** (`balances.py`): iterate in time order. For a fixed debit: if balance is insufficient, scale the amount to `balance x U(0.5, 1.0)` or skip if balance under 100. For a forward intent: `amount = round(fraction x available_pool(campaign, account), 2)`, then decrement the pool; skip if the pool is empty (upstream was skipped) or amount under 1. Credits increase the pool of the receiving account for that campaign. Update balances for tracked accounts and write `*_bal_*` fields (null for merchants, agents). Conservation: for every ring, outflow from mules never exceeds inflow.
6. Assign `event_id` in final time order. Write files (§8.2.6). Compute `world_report.json`.

#### 8.2.5 Difficulty calibration and world freeze

- Calibrate difficulty with **B0 and B1 only** (never look at G2 while tuning the world): aim for B1 recall at 1% FPR somewhere in 0.4 to 0.9 and ring txn share inside `fraud_target_share`. Tune only `noise_level`, `hard_negative_boost` and mule-profile mix.
- Record the chosen values and why in `docs/decisions.md`, then **freeze**: write SHA-256 of each world's events file into `results/world_manifest.json`. Do not regenerate worlds after seeing G2 results.

#### 8.2.6 Outputs

Per world: `data/processed/world_<preset>_seed<N>/` containing `events.csv.gz`, `accounts.csv.gz`, `rings.json` (per ring: `ring_id`, typology, accounts by role, transaction ids, first and last timestamps, hop count, total inflow and outflow), `world_report.json` (counts by type and archetype, fraud share, ring counts by typology, dwell and pass-through distributions for rings versus legitimate traders and agents), `manifest.json` (config hash, seed, file hashes).

**Demo replay world:** preset small, seed 2026, 6 days, 14 campaigns covering every typology, plus 2 `split_bill` and 1 `charity_collect` event. Written to `data/replay/demo.csv.gz`, `demo_accounts.csv.gz` and `demo_rings.json` (the world's `rings.json`; combined size under 10 MB). The replay controller fast-forwards events before `warmup_until_ts` (day 3) without sending them to the UI.

#### 8.2.7 World invariants (tested in `tests/test_world_invariants.py`)

Sorted by `(ts, event_id)`; no negative balances on tracked accounts; balance arithmetic consistent; every ring has at least 3 accounts and at least 2 hops; money conservation per ring; fraud share inside `fraud_target_share`; all five types present; ids carry no archetype information (a classifier on ids alone scores no better than chance); same seed gives byte-identical files; archetype counts match the config.

### 8.3 PaySim adapter (secondary benchmark)

- Input: the Kaggle `paysim1` CSV placed in `data/raw/paysim/` (any `*.csv` file in the folder). Read with pandas in chunks of 500,000 rows (train env only).
- Mapping: `ts = (step - 1) x 3600 + u` with `u ~ U(0, 3600)` seeded by `project.seed` (PaySim only has hour resolution); `event_id` = row index; `src = nameOrig`; `dst = nameDest`; `type` normalised to the enum (accept both `CASH-OUT` and `CASH_OUT`); balances from the four balance columns, set **null for `dst` when `nameDest` starts with `M`**; `is_fraud = isFraud`; `ring_id = -1`, `hop = -1`; roles `legit`.
- Do not use `isFlaggedFraud` as a feature. Report it separately as the simulator's own rule baseline.
- Split by step: train steps 1 to 400, validation 401 to 500, test 501 to 744; burn-in the first 72 steps.
- Only transaction-level and account-level metrics apply (no ring ground truth). Report PR-AUC, recall at the FPR points, and feature-importance share of balance fields.
- Run `scripts/profile_paysim.py` first (§3.1) and write `results/paysim_profile.json`: rows, fraud rows, unique senders and receivers, median transactions per account, types where fraud occurs, AUC of a model using only balance-derived features.
- **Expectation to test, not assume:** per-transaction models score very high on PaySim because balance fields leak the label and sender accounts have almost no history. If the profile confirms it, say so in the report and use it to motivate ChainWorld. If it does not, say that instead.
- Optional (P1): derive background amount and type marginals from PaySim legitimate rows into `configs/background_profile.json` and use quantile mapping in ChainWorld.

### 8.4 Organizer dataset adapter (only if provided)

1. Read the problem statement and data dictionary; record them in `docs/STATUS.md`.
2. If the data has sender, receiver, amount and time (any payment-graph structure): write `chainwatch/data/organizer.py` mapping it to the canonical schema, keep an `Organizer` data-provenance label, and run the same pipeline (features, models, evaluation). If labels are absent, evaluate with unsupervised proxies and say so.
3. If the data has **no account-to-account structure** (for example a single-customer tabular table), the ring approach does not apply to it. **Stop and ask the user**: options are to stay on ChainWorld and PaySim, or to pivot. Do not silently change the project.

### 8.5 Splits and leakage-related data rules

- **Time-based split** by event timestamp: train `[burn_in_days, 15)`, validation `[15, 20)`, test `[20, 30)` days (`eval.split_days`). Burn-in days build account state but are excluded from training rows and from all metrics.
- **Ring assignment to splits** by the timestamp of the ring's first transaction. Ring-level metrics use only rings whose first transaction lies inside the evaluated split. Rings that begin in one split and extend into the next contribute their transactions to transaction-level metrics only.
- Thresholds, calibration and the ring-alert threshold are fitted on validation only (rule H5).
- Never shuffle events across time. Never compute a feature using an event with a later `(ts, event_id)`.
- Mule accounts reused across campaigns are allowed (realistic), but account ids are never features.
