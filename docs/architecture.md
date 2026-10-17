# ChainWatch architecture

The system has two environments that never mix (spec §5.1): a **train env** (x86 laptop, numpy,
pandas, scikit-learn, LightGBM) and a **serve env** (laptop and IBM LinuxONE s390x, pure Python
only). The interface between them is JSON: portable gradient-boosted trees plus thresholds, a
benign profile and a signed manifest.

```text
 OFFLINE (laptop / Colab, x86_64, full Python stack)            ONLINE (laptop AND IBM LinuxONE s390x, pure Python)
 +------------+   +---------------+   +--------------+  JSON   +---------------------------------------------+
 | ChainWorld |-->| FeatureEngine |-->| LightGBM     |-------->| models/*.json  (portable trees, thresholds, |
 | PaySim     |   | (stream replay|   | B0 / B1 / G2 |         |                 benign profile, feature spec) |
 | Organizer  |   |  = training)  |   +--------------+         +----------------------+----------------------+
 +------------+   +---------------+                                                      |
                                                                                    v
   Event (no labels) --> FeatureEngine --> PortableGBDT --> Tiers --> Reasons --> Decision (allow | verify | hold)
                          | windows, FIFO     score         |                          |
                          | trail, flow comps               v                          v
                          +--------------------------> RingGrouper --> RingAlert --> escalation back into Tiers
                                                             |
                                  FastAPI: /v1/score, /v1/rings, WebSocket /v1/ws, replay, metrics
                                                             |
                                                      Dashboard (static HTML + JS, cytoscape)
```

## Modules, one line each

| Module | What it does |
|---|---|
| `chainwatch/schema.py` | `Event` (no labels), `EventIn` (API input, rejects labels and unknown keys), response models |
| `chainwatch/config.py` | TOML + preset + env + `--set` overrides, validation, config hash (stdlib only) |
| `chainwatch/data/canonical.py`, `io.py` | Canonical event type normalisation, sort/validation, csv.gz round trip |
| `chainwatch/data/world/` | ChainWorld simulator: `population.py` (archetypes), `background.py` (legitimate activity, schedules), `typologies.py` (funnel, layered chain, split-merge, round trip, structuring, slow roll), `balances.py` (balance pass), `generate.py` (writes the world) |
| `chainwatch/data/paysim.py` | PaySim CSV -> canonical stream, step-based split |
| `chainwatch/features/` | `registry.py` (59 features, hashes), `state.py` (per-account state), `engine.py` (the one streaming implementation, rule E4), `matrix.py` (replay to train/val/test `.npy`) |
| `chainwatch/model/` | `train.py` (LightGBM), `export.py` (JSON trees), `portable_gbdt.py` (pure-Python evaluator), `vector_predict.py`, `thresholds.py` (quantiles on validation, ring calibration, benign profile, manifest), `profile.py` |
| `chainwatch/rings/` | `grouping.py` (streaming ring grouper with TTL and rebuilds), `scoring.py` (noisy-OR), `offline.py` (evaluation helper) |
| `chainwatch/decision/` | `tiers.py` (allow/verify/hold), `reasons.py` (plain-language reason codes) |
| `chainwatch/serving/` | `engine.py` (stateful single-threaded scorer, rule E8), `routes.py` (REST), `ws.py` (WebSocket feed), `replay.py` (time-ordered replay controller), `inject.py` (typology injection, pure Python), `metrics.py`, `app.py` (factory), `static/` (dashboard) |
| `chainwatch/eval/` | `protocol.py` (time-based splits, variants, matched FPR, aggregation, claim sentences), `metrics.py`, `ablation.py`, `robustness.py`, `leakage.py`, `parity.py`, `report.py` (templates and figures), `check_claims` in `scripts/` |
| `bench/` | `engine_bench.py` (in-process latency), `load_test.py` (HTTP), `platform_info.py` |

## Data flow per served event

1. Validate the event (pydantic; no label fields; `extra="forbid"`).
2. Idempotency by `event_id` (LRU); lateness check against the engine clock.
3. `FeatureEngine.step()` — strictly causal features from rolling windows, FIFO matching, flow
   components; bounded state with TTL eviction and caps.
4. `PortableGBDT.predict()` for G2 (plus B0/B1 shadows in compare mode).
5. Ring escalation: transactions touching a member of an alerting ring are raised to at least
   verify or hold (recommendation only; never automatic).
6. Tiers + up to three plain-language reasons; report suspicious edges to the `RingGrouper`.
7. Stage timings recorded into the metrics histogram; decision returned.

## The two environments

| | Train env | Serve env |
|---|---|---|
| Where | laptop (x86_64) | laptop and LinuxONE (s390x), Docker or venv |
| Python | 3.12 | 3.10 to 3.13 |
| Packages | numpy, pandas, scikit-learn, LightGBM, matplotlib + serve list | fastapi, pydantic, plain uvicorn, websockets, tomli (3.10 only) |
| Model format | trains LightGBM, exports JSON | reads JSON only; no pickle anywhere (rule E5) |

Everything the simulator assumes (and does not model) is listed in the data card
(`docs/data_card.md`).
