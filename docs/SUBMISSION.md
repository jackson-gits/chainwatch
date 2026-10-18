# ChainWatch · Submission & Demo Dossier
**IBM Z Datathon 2026** · Track: **Theme 1 — Real-Time AI for Critical Decisions**  
**Author / Team:** Jackson Raj A (GitHub: [`jackson-gits`](https://github.com/jackson-gits))  
**Project Repository:** [`https://github.com/jackson-gits/upi-mule-ring-detection`](https://github.com/jackson-gits/upi-mule-ring-detection)

---

## 1. Submission Checklist (§20.8 Audit)

| # | Item | Status | Location / Verification |
|---|---|---|---|
| 1 | **Public Git Repository** | **DONE** | Public repository with MIT License, clean history, zero secrets, zero heavy binaries. |
| 2 | **Presentation Deck** | **DONE** | Standalone 11-slide deck in [`docs/deck.html`](deck.html), markdown source in [`docs/deck_outline.md`](deck_outline.md), print-to-PDF ready. |
| 3 | **Demo Video & Narration** | **PENDING** | `docs/demo_script.md` is the narration guide; no recorded three-minute video file is present in the project artifacts. |
| 4 | **Reachable Demo & Fallback** | **PARTIAL** | Local server instructions and offline fallback are ready; a public tunnel URL must be started manually. |
| 5 | **Screenshots Directory & Visuals** | **DONE** | High-resolution assets committed in [`docs/screenshots/`](screenshots/): `dashboard_ring.png` and `platform_badge.png`. |
| 6 | **Event Requirements Confirmed** | **PARTIAL** | Theme and human-in-the-loop approach documented; physical LinuxONE execution is still pending. |
| 7 | **Audit & Claims Integrity** | **PARTIAL** | Generated report claims pass `chainwatch check-claims`; hand-written submission materials were updated to the current evaluation. |
| 8 | **Cross-Platform Decision Parity** | **REHEARSED** | Zero decision mismatches and score delta below 1e-16 in the s390x QEMU rehearsal; no physical LinuxONE run. |
| 9 | **IBM LinuxONE Platform Evidence** | **PENDING** | QEMU artifacts and the portability dossier exist; a physical platform record and native benchmarks still require instance access. |

---

## 2. Event Requirements & Verification Summary

- **Event:** IBM Z Datathon 2026 (Global 24-Hour Hackathon).
- **Track:** Theme 1 — *Real-Time AI for Critical Decisions* ("Detect. Decide. Respond.").
- **Deadline:** Sunday, October 18, 2026, 09:00 IST.
- **Problem Statement:** Detect illicit mule account networks in real-time high-throughput UPI payment streams before funds can be withdrawn at cash-out points.
- **Dataset Policy:** No organizer dataset was supplied. The primary evaluation is five frozen medium ChainWorld worlds; the PaySim CSV is absent, so PaySim was not evaluated.
- **Platform Execution & Evidence:** Portable inference and parity were rehearsed under s390x QEMU. This is not physical IBM LinuxONE execution; native parity and performance require an instance.

---

## 3. Demo Access & Fallback Protocol

### Option A: Running the Live Interactive Server
Execute on the evaluation machine (Windows, Linux, or macOS):
```bash
# Windows PowerShell:
.\.venv\Scripts\python.exe -m chainwatch serve --compare

# Linux / macOS / Git Bash:
bash scripts/run_native.sh
```
Open **`http://localhost:8000`** in any web browser.
- Press <kbd>Space</kbd> to play/pause the streaming transactions.
- Press <kbd>I</kbd> to inject an instant funnel mule ring.
- Press <kbd>N</kbd> to advance to the next planted campaign.
- Press <kbd>C</kbd> to toggle baseline detector comparisons.

### Option B: Remote Access via SSH / Public Tunnel
To allow remote judges to inspect the live dashboard without local setup:
```bash
# Using Cloudflare Tunnel:
cloudflared tunnel --url http://localhost:8000

# Using Localtunnel:
npx localtunnel --port 8000

# Using ngrok:
ngrok http 8000
```

### Option C: Zero-Install Standalone Fallback Demo
If network firewalls, port forwarding, or Python environment setup prevents running the live server:
1. Open [`docs/demo_fallback.html`](demo_fallback.html) directly in any web browser.
2. It runs completely offline with zero server dependencies, simulating the streaming transaction feed, live Cytoscape ring topology graph, alert tiers, reason codes, and IBM LinuxONE telemetry dock.

---

## 4. Submission Portal Form Fields

### Project Title
**ChainWatch: Real-Time Mule-Ring Detection for UPI Payment Streams**

### One-Line Tagline
Streaming graph AI for mule-ring decision support, designed for portable deployment to IBM LinuxONE; physical platform validation is pending.

### Problem Statement (Background & Scale)
In September 2026, UPI handled approximately 24.07 billion transactions worth Rs 29.37 lakh crore (~802 million transactions daily). Between January and June 2026, cyber fraud complaints in India exceeded 12.71 lakh cases reporting losses over Rs 10,178.97 crore, yet only ~29% of funds were successfully held or frozen. Scammers route illicit funds through chains of borrowed or duped "mule" accounts within 5–20 minutes. Traditional transaction-by-transaction screening misses the pattern because each hop appears legitimate in isolation; banks must observe whole layering rings forming in real time.

### Proposed Solution & Innovation
ChainWatch monitors streaming payment events in strict chronological order, maintaining a bounded rolling-window graph of counterparty flows:
1. **59 Streaming Features:** Tracks FIFO money pass-through turnover, velocity, trail depth, funnel fan-in, and neighbor suspicion without IP or device tracking.
2. **Ring Grouping:** Clusters connected suspicious accounts into unified rings via streaming union-find, shifting alerts from individual payments to entire operations.
3. **Plain-Language Explainability:** Recommends `allow`, `verify`, or `hold` backed by up to 3 plain-language reasons (e.g., *"Passed on 94% of received funds within 14 min across 3 hops"*).
4. **IBM LinuxONE Architecture:** The entire serving path is pure Python. Tree models are exported as portable JSON and evaluated by a lightweight custom tree walker, avoiding big-endian byte-order corruption and compiler dependencies on s390x.

### Measured Results & Evidence
- **Five-world ChainWorld result:** G2+R caught **250/250 test rings (100%; bootstrap 95% CI 100%-100%)**, tying B1+R at 250/250 and exceeding B0 at 224/250 (89.7%).
- **Intervention timing:** G2+R caught 249/250 rings before cash-out (99.6%); pooled median catch time was 5.25 minutes, with a median of 2 hops remaining.
- **Operating-point caveat:** Thresholds were set on validation for a 0.5% FPR target. G2+R's realised test FPR ranged from 0.483% to 1.619% (mean 0.904%); some worlds were off target.
- **Robustness grid:** R1–R6 completed on two small synthetic seeds, with thresholds fixed from the medium seed-0 package. All 28 cell/variant groups had 100% ring recall; G2+R cell-mean deployed recall ranged from 93.2% to 98.7%. The 30% slow-roll mix had the lowest G2+R cell mean. This two-seed synthetic sensitivity check is not external validation.
- **Measured laptop baseline:** Engine throughput was 1,399 events/second with p50 336 microseconds and p95 1,004 microseconds (`results/bench_engine_laptop.json`). HTTP p50/p95 was 7.3/15.4 ms at concurrency 1 (`results/bench_load_laptop.json`). These measurements are not LinuxONE results.
- **s390x parity rehearsal:** QEMU produced zero decision mismatches over 20,000 events with score delta below 1e-16 (`results/parity_qemu-s390x.jsonl`). Physical LinuxONE execution and native performance remain pending.

### Limitations & Ethical Safeguards
- **Decision Support Only:** System recommendations require human review; no accounts are blocked automatically.
- **Protecting Victims:** Account holders are often tricked students or elderly victims; alerts provide factual behavior patterns rather than accusations.
- **Privacy by Design:** Operates purely on payment amounts, timestamps, and graph structure. Zero demographic, location, or biometric data.
- **Synthetic Validation:** Five medium ChainWorld test worlds were evaluated. PaySim was not evaluated because its CSV is missing. No claim is made on production bank ledgers.

---

## 5. Presentation Slide Deck Overview

The full 11-slide presentation deck is provided in [`docs/deck.html`](deck.html) (HTML/print-to-PDF ready) and [`docs/deck_outline.md`](deck_outline.md):
- **Slide 1:** Title, Track, and One-Line Promise
- **Slide 2:** UPI Fraud Scale and Single-Payment Blindspots
- **Slide 3:** Streaming Graph Architecture & Decision Tiers
- **Slide 4:** Evaluation Protocol & Leakage Controls (L1–L9)
- **Slide 5:** Five-World Results: G2+R Ties B1+R; FPR variation disclosed
- **Slide 6:** Comprehensive Model Comparison Matrix
- **Slide 7:** Typology Breakdown & Hard Cases (Slow Roll)
- **Slide 8:** s390x QEMU Rehearsal; Physical LinuxONE Pending
- **Slide 9:** Analyst Command Center & Explainability
- **Slide 10:** Responsible AI, Victim Protection & Limitations
- **Slide 11:** Reproduction Guide & Roadmap

---

## 6. Demo Video Walkthrough Guide (3 Minutes)

| Time | Visual on Screen | Narration Focus |
|---|---|---|
| **0:00 – 0:30** | README Problem Statement & UPI Metrics | Frame the problem: UPI handles 800M+ daily payments; Rs 10,000+ Cr cyber fraud; single-payment models miss mule relays. |
| **0:30 - 1:10** | Live Dashboard Replay | Show the stream and explain that measured laptop engine p50 was 336 microseconds; do not present it as a LinuxONE measurement. |
| **1:10 – 1:50** | Inject Funnel Ring (<kbd>I</kbd>) & Topology Graph | Watch the ring cluster form in Cytoscape; role colors (Entry=Blue, Relay=Purple, Exit=Orange). Point to `RING-0042`. |
| **1:50 – 2:20** | Ring Inspector & Plain-Language Reasons | Highlight the 3 human-readable reasons, hold recommendation, and 2 hops remaining before cash-out. |
| **2:20 - 2:45** | s390x Portability | Show the QEMU parity evidence and state that physical LinuxONE access is pending. |
| **2:45 - 3:00** | Results & Limitations | Summarize the five-world result: G2+R ties B1+R at 100% ring recall; mention test FPR drift and the missing PaySim evaluation. |

