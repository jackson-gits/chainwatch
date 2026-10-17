/* ChainWatch Dashboard: Real-Time Mule Ring & Fraud Detection
   Upgraded with UI/UX Pro Max, Antigravity Motion Design, & Modern Frontend Architecture.
   Zero external runtime dependencies, 100% offline-ready, pure vanilla ES6+. */

(function () {
  "use strict";

  const qs = new URLSearchParams(location.search);
  const API_KEY = qs.get("key") || "";
  const MAX_FEED = 250;
  const MAX_ELEMENTS = 3000;

  const REASON_LABEL = {
    RING_MEMBER_OUT: "linked group (out)",
    RING_MEMBER_IN: "linked group (in)",
    FAST_PASS_ON: "fast pass-on",
    NEW_SENDERS_BURST: "new senders burst",
    MANY_SENDERS_IN: "many senders",
    FAN_OUT_FAST: "rapid fan-out",
    DEEP_TRAIL: "deep multi-hop trail",
    NEIGHBOUR_PATTERN: "neighbour topology",
    HIGH_PASS_THROUGH: "high pass-through",
    ROUND_TRIP: "circular round-trip",
    CONNECTED_GROUP: "connected cluster",
    NEW_RECEIVER_ACCOUNT: "new receiver",
    DORMANT_RECEIVER: "dormant receiver reactivated",
    DORMANT_SENDER: "dormant sender reactivated",
    DRAIN_BALANCE: "rapid balance drain",
    NEAR_LIMIT_AMOUNT: "near-limit transaction",
    MODEL_SCORE: "overall ML risk score",
  };

  const TIER_ICON = { allow: "●", verify: "▲", hold: "■" };

  const S = {
    tz: 19800,
    info: null,
    rings: new Map(),
    allow: 0,
    reviewed: new Set(),
    selectedRing: null,
    truthRole: new Map(),
    showTruth: false,
    compare: false,
    comparison: [],
    replay: null,
    nodeRings: new Map(),
    edgeRing: new Map(),
    ringOrder: [],
    items: new Map(),
    backoff: 1000,
    ws: null,
    filterTier: "all",
    filterText: "",
    feedBuffer: [],
    rafScheduled: false,
    audioEnabled: false,
    audioCtx: null,
    lastMetrics: null,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));

  const inr = (v) => "₹ " + Math.round(Number(v) || 0).toLocaleString("en-IN");
  const us = (v) => (v == null ? "-" : v >= 1000 ? (v / 1000).toFixed(2) + " ms" : Math.round(v) + " µs");

  function simTime(ts) {
    if (ts == null) return "-";
    const local = ts + S.tz;
    const day = Math.floor(local / 86400);
    const s = local - day * 86400;
    const hh = String(Math.floor(s / 3600)).padStart(2, "0");
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    return `day ${day} ${hh}:${mm}`;
  }

  function mins(dt) {
    if (dt == null) return null;
    const m = dt / 60;
    return (m >= 0 ? "+" : "") + (Math.abs(m) < 10 ? m.toFixed(1) : Math.round(m)) + " min";
  }

  /* ------------------------------------------------------------------ Audio Synthesizer (Zero Assets) */
  function playChime(type) {
    if (!S.audioEnabled) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!S.audioCtx) S.audioCtx = new AudioCtx();
      const ctx = S.audioCtx;
      if (ctx.state === "suspended") ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      const now = ctx.currentTime;
      if (type === "hold") {
        osc.frequency.setValueAtTime(587.33, now); // D5
        osc.frequency.setValueAtTime(880.00, now + 0.08); // A5
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22);
        osc.start(now);
        osc.stop(now + 0.22);
      } else if (type === "verify") {
        osc.frequency.setValueAtTime(440.00, now);
        gain.gain.setValueAtTime(0.04, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
        osc.start(now);
        osc.stop(now + 0.12);
      }
    } catch (e) { /* ignore audio failure */ }
  }

  /* ------------------------------------------------------------------ Clipboard Copy Helper */
  function copyText(txt, label) {
    if (!navigator.clipboard) {
      toast(`Copied ${label}: ${txt}`);
      return;
    }
    navigator.clipboard.writeText(txt).then(() => {
      toast(`Copied ${label} to clipboard: ${txt}`);
    }).catch(() => {
      toast(`Copied: ${txt}`);
    });
  }

  async function api(path, method, body) {
    const headers = { "Content-Type": "application/json" };
    if (API_KEY) headers["X-API-Key"] = API_KEY;
    const r = await fetch(path, { method: method || "GET", headers, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      const msg = data && data.error ? data.error.message : r.statusText;
      toast(msg);
      throw new Error(msg);
    }
    return data;
  }

  /* ------------------------------------------------------------------ Toast Notifications */
  let toastTimer = null;
  let toastFadeTimer = null;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.style.opacity = "1";
    t.classList.remove("hidden");
    clearTimeout(toastTimer);
    clearTimeout(toastFadeTimer);
    toastTimer = setTimeout(() => {
      // Fade out over 200ms then hide
      const start = performance.now();
      const fadeDuration = 200;
      function fadeStep(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / fadeDuration, 1);
        t.style.opacity = String(1 - progress);
        if (progress < 1) {
          toastFadeTimer = requestAnimationFrame(fadeStep);
        } else {
          t.classList.add("hidden");
          t.style.opacity = "";
        }
      }
      toastFadeTimer = requestAnimationFrame(fadeStep);
    }, 3500);
  }

  /* ------------------------------------------------------------------ JSON Download Helper */
  function downloadJson(filename, obj) {
    const b = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    const u = URL.createObjectURL(b);
    const a = document.createElement("a");
    a.href = u;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(u);
    toast("Exported " + filename);
  }

  /* ------------------------------------------------------------------ Animated Counter */
  function animateCounter(el, from, to, durationMs) {
    if (!el) return;
    const start = performance.now();
    const range = to - from;
    function step(now) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / durationMs, 1);
      // ease-out quad
      const eased = 1 - (1 - progress) * (1 - progress);
      el.textContent = Math.round(from + range * eased).toLocaleString("en-IN");
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* ------------------------------------------------------------------ Cytoscape Graph Engine */
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  const cy = cytoscape({
    container: $("cy"),
    wheelSensitivity: 0.22,
    minZoom: 0.15,
    maxZoom: 3.5,
    style: [
      {
        selector: "node",
        style: {
          "background-color": css("--entry") || css("--role-entry") || "#60a5fa",
          label: "data(label)",
          color: "#f8fafc",
          "font-size": 11,
          "font-weight": "bold",
          "text-valign": "bottom",
          "text-margin-y": 5,
          width: 26,
          height: 26,
          "border-width": 2,
          "border-color": "#1e2c45",
          "text-background-color": "rgba(13, 21, 36, 0.88)",
          "text-background-opacity": 0.88,
          "text-background-padding": 3,
          "text-background-shape": "roundrectangle",
          "font-family": "ui-monospace, Consolas, monospace",
          "transition-property": "background-color, border-color, width, height, opacity",
          "transition-duration": "0.2s",
        }
      },
      {
        selector: "node.entry",
        style: {
          shape: "ellipse",
          "background-color": css("--entry") || css("--role-entry") || "#60a5fa",
          "border-color": "#93c5fd"
        }
      },
      {
        selector: "node.relay",
        style: {
          shape: "diamond",
          "background-color": css("--relay") || css("--role-relay") || "#c084fc",
          width: 30,
          height: 30,
          "border-color": "#e9d5ff"
        }
      },
      {
        selector: "node.exit",
        style: {
          shape: "round-rectangle",
          "background-color": css("--exit") || css("--role-exit") || "#fb923c",
          "border-color": "#fdba74"
        }
      },
      {
        selector: "node.truth",
        style: {
          "border-width": 3,
          "border-color": css("--truth") || css("--role-truth") || "#f43f5e",
          "border-style": "dashed"
        }
      },
      {
        selector: "node:selected",
        style: {
          "overlay-color": "#38bdf8",
          "overlay-opacity": 0.35,
          "overlay-padding": 4
        }
      },
      {
        selector: "edge",
        style: {
          width: "data(w)",
          "line-color": "#475569",
          "target-arrow-color": "#64748b",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "arrow-scale": 0.9,
          opacity: 0.92,
          "transition-property": "line-color, opacity, width",
          "transition-duration": "0.25s"
        }
      },
      {
        selector: "edge.terminal",
        style: {
          "line-style": "dashed",
          "line-color": "#f97316",
          "target-arrow-color": "#f97316"
        }
      },
      {
        selector: "edge.flash",
        style: {
          "line-color": "#fde047",
          "target-arrow-color": "#fde047",
          opacity: 1
        }
      },
      {
        selector: ".dim",
        style: {
          opacity: 0.12
        }
      },
    ],
    layout: { name: "preset" },
  });

  function edgeWidth(amount) {
    return Math.max(1.5, Math.min(8.5, Math.log10(Math.max(10, amount)) * 1.6 - 2));
  }

  function syncGraph(ring) {
    const rid = ring.ring_id;
    let added = false;
    cy.batch(() => {
      for (const a of ring.accounts) {
        let set = S.nodeRings.get(a.id);
        if (!set) { set = new Set(); S.nodeRings.set(a.id, set); }
        set.add(rid);
        let n = cy.getElementById(a.id);
        if (n.empty()) {
          n = cy.add({ group: "nodes", data: { id: a.id, label: a.id } });
          added = true;
        }
        n.removeClass("entry relay exit").addClass(a.role);
        n.toggleClass("truth", S.showTruth && S.truthRole.has(a.id));
      }
      for (const e of ring.edges) {
        const eid = "e" + e.event_id;
        if (S.edgeRing.has(eid)) continue;
        if (cy.getElementById(e.src).empty() || cy.getElementById(e.dst).empty()) continue;
        S.edgeRing.set(eid, rid);
        const el = cy.add({
          group: "edges",
          data: { id: eid, source: e.src, target: e.dst, w: edgeWidth(e.amount) }
        });
        if (e.type !== "TRANSFER") el.addClass("terminal");
        el.addClass("flash");
        setTimeout(() => el.removeClass("flash"), 1400);
        added = true;
      }
    });

    if (!S.ringOrder.includes(rid)) S.ringOrder.push(rid);
    if (added) relayout();
    pruneGraph();
    $("graph-empty").classList.toggle("hidden", cy.nodes().length > 0);
  }

  function removeRingFromGraph(rid) {
    cy.batch(() => {
      for (const [eid, r] of Array.from(S.edgeRing.entries())) {
        if (r === rid) { cy.getElementById(eid).remove(); S.edgeRing.delete(eid); }
      }
      for (const [acct, set] of Array.from(S.nodeRings.entries())) {
        if (set.delete(rid) && set.size === 0) { cy.getElementById(acct).remove(); S.nodeRings.delete(acct); }
      }
    });
    S.ringOrder = S.ringOrder.filter((r) => r !== rid);
    $("graph-empty").classList.toggle("hidden", cy.nodes().length > 0);
  }

  function pruneGraph() {
    while (cy.elements().length > MAX_ELEMENTS && S.ringOrder.length > 1) {
      removeRingFromGraph(S.ringOrder[0]);
    }
  }

  let layoutTimer = null;
  function relayout(immediate = false) {
    clearTimeout(layoutTimer);
    const run = () => {
      const roots = cy.nodes(".entry");
      cy.layout({
        name: "breadthfirst",
        directed: true,
        roots: roots.length ? roots : undefined,
        spacingFactor: 1.15,
        padding: 30,
        animate: true,
        animationDuration: 350,
        avoidOverlap: true
      }).run();
      if (S.selectedRing) focusRing(S.selectedRing, false);
      else cy.fit(undefined, 28);
    };
    if (immediate) run(); else layoutTimer = setTimeout(run, 120);
  }

  function focusRing(rid, select = true) {
    const ring = S.rings.get(rid);
    if (!ring) return;
    const ids = new Set(ring.accounts.map((a) => a.id));
    const els = cy.nodes().filter((n) => ids.has(n.id()));
    const edges = cy.edges().filter((e) => S.edgeRing.get(e.id()) === rid);

    cy.elements().addClass("dim");
    const union = els.union(edges);
    union.removeClass("dim");

    if (els.length) {
      cy.animate({
        fit: { eles: union, padding: 50 },
        duration: 350,
        easing: "ease-out-cubic"
      });
    }
    if (select) { S.selectedRing = rid; renderRings(); }
  }

  function clearSelection() {
    cy.elements().removeClass("dim");
    S.selectedRing = null;
    renderRings();
    document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel"));
  }

  cy.on("tap", (evt) => {
    if (evt.target === cy) clearSelection();
  });
  cy.on("tap", "node", (evt) => showAccount(evt.target.id()));

  // Node hover tooltip
  cy.on("mouseover", "node", (evt) => {
    const node = evt.target;
    const tt = $("graph-tooltip");
    if (!tt) return;
    const pos = node.renderedPosition();
    const acct = node.id();
    const role = node.hasClass("relay") ? "relay" : node.hasClass("exit") ? "exit" : "entry";
    const rings = Array.from(S.nodeRings.get(acct) || []).join(", ") || "None";
    tt.innerHTML = `
      <span class="tt-acct">${esc(acct)}</span>
      <span class="tt-role ${role}">${role} node</span>
      <div style="font-size:0.68rem; color:var(--muted); margin-top:2px;">
        <div><b>Rings:</b> ${esc(rings)}</div>
        <div style="margin-top:2px; color:var(--accent);">Click node to open dossier &rarr;</div>
      </div>
    `;
    tt.style.left = `${pos.x}px`;
    tt.style.top = `${pos.y + 44}px`;
    tt.classList.remove("hidden");
  });

  cy.on("mouseout", "node", () => {
    $("graph-tooltip")?.classList.add("hidden");
  });

  /* ------------------------------------------------------------------ Graph Toolbar */
  $("btn-zoom-in").addEventListener("click", () => {
    cy.animate({ zoom: cy.zoom() * 1.3, duration: 150 });
  });
  $("btn-zoom-out").addEventListener("click", () => {
    cy.animate({ zoom: cy.zoom() * 0.75, duration: 150 });
  });
  $("btn-fit").addEventListener("click", () => {
    cy.animate({ fit: { padding: 25 }, duration: 250 });
  });
  $("btn-relayout").addEventListener("click", () => relayout(true));
  $("btn-clear-selection").addEventListener("click", () => clearSelection());

  function toggleFullscreen() {
    const gp = $("graph-panel");
    if (!gp) return;
    const isFull = gp.classList.toggle("fullscreen");
    const btn = $("btn-fullscreen");
    if (btn) btn.innerHTML = isFull ? "&#10005;" : "&#x26F6;";
    if (btn) btn.title = isFull ? "Exit Fullscreen (Esc or F)" : "Toggle Fullscreen (F)";
    setTimeout(() => {
      cy.resize();
      cy.fit(undefined, 40);
    }, 60);
    toast(isFull ? "Fullscreen topology mode (press F or Esc to exit)" : "Exited fullscreen topology mode");
  }
  $("btn-fullscreen")?.addEventListener("click", toggleFullscreen);

  $("btn-export-png").addEventListener("click", () => {
    try {
      const uri = cy.png({ bg: "#060912", full: true, scale: 2 });
      const a = document.createElement("a");
      a.href = uri;
      a.download = `chainwatch-topology-${Date.now()}.png`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast("Exported topology image");
    } catch (e) {
      toast("Export failed: graph may be empty");
    }
  });

  /* ------------------------------------------------------------------ Live Feed & Filtering */
  function filterRow(row, d, ev) {
    let show = true;
    if (S.filterTier !== "all" && d.decision !== S.filterTier) show = false;
    if (show && S.filterText) {
      const q = S.filterText.toLowerCase();
      const txt = `${ev.src} ${ev.dst} ${d.event_id} ${d.ring_id || ""}`.toLowerCase();
      if (!txt.includes(q)) show = false;
    }
    row.classList.toggle("hidden", !show);
  }

  function applyFeedFilters() {
    const feed = $("feed");
    for (const row of Array.from(feed.children)) {
      const it = S.items.get(row.dataset.eid);
      if (it) filterRow(row, it.decision, it.event);
    }
  }

  function setTierFilter(tier) {
    S.filterTier = tier || "all";
    document.querySelectorAll(".pill-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.tier === S.filterTier);
    });
    $("kpi-tile-holds")?.classList.toggle("active-filter", S.filterTier === "hold");
    applyFeedFilters();
    toast(S.filterTier === "all" ? "Showing ALL decisions" : `Filtered feed to ${S.filterTier.toUpperCase()} decisions`);
  }

  const feedFilterInput = $("feed-filter");
  const clearFeedBtn = $("btn-clear-feed-filter");

  feedFilterInput?.addEventListener("input", (e) => {
    S.filterText = e.target.value.trim();
    if (clearFeedBtn) clearFeedBtn.classList.toggle("hidden", !S.filterText);
    applyFeedFilters();
  });

  clearFeedBtn?.addEventListener("click", () => {
    if (feedFilterInput) feedFilterInput.value = "";
    S.filterText = "";
    clearFeedBtn.classList.add("hidden");
    applyFeedFilters();
    feedFilterInput?.focus();
  });

  // Pill filter buttons — delegated on parent container
  document.querySelectorAll(".pill-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setTierFilter(btn.dataset.tier || "all");
    });
  });

  // Feed scroll pause listener
  const feedScrollContainer = $("feed");
  feedScrollContainer?.addEventListener("scroll", () => {
    const pill = $("feed-scroll-pill");
    if (!pill) return;
    if (feedScrollContainer.scrollTop > 35) {
      pill.classList.remove("hidden");
    } else {
      pill.classList.add("hidden");
    }
  });

  $("btn-resume-feed")?.addEventListener("click", () => {
    feedScrollContainer?.scrollTo({ top: 0, behavior: "smooth" });
    $("feed-scroll-pill")?.classList.add("hidden");
  });

  /* ------------------------------------------------------------------ RAF Feed Batching (60fps) */
  function addItems(items) {
    S.feedBuffer.push(...items);
    if (!S.rafScheduled) {
      S.rafScheduled = true;
      requestAnimationFrame(flushFeedBuffer);
    }
  }

  function flushFeedBuffer() {
    S.rafScheduled = false;
    if (S.feedBuffer.length === 0) return;

    const feed = $("feed");
    const frag = document.createDocumentFragment();
    const batch = S.feedBuffer.splice(0, S.feedBuffer.length);
    const skipAnim = batch.length > 8; // stay at 60fps for large bursts

    let allowDelta = 0;
    let hasHold = false;
    let hasVerify = false;

    for (const it of batch) {
      const d = it.decision, ev = it.event, tr = it.truth;
      if (tr && tr.is_fraud) {
        for (const [acct, role] of [[ev.src, tr.src_role], [ev.dst, tr.dst_role]]) {
          if (role === "mule" || role === "collector") S.truthRole.set(acct, role);
        }
      }
      if (d.decision === "allow") {
        allowDelta += 1;
        continue;
      }
      if (d.decision === "hold") hasHold = true;
      else if (d.decision === "verify") hasVerify = true;

      S.items.set(String(d.event_id), it);
      const row = document.createElement("div");
      row.className = skipAnim ? "row" : "row new";
      row.dataset.eid = d.event_id;
      row.dataset.decision = d.decision; // CSS left-accent stripe
      if (d.ring_id) row.dataset.rid = d.ring_id;

      const chips = d.reasons.map((r) =>
        `<span class="chip" title="${esc(r.text)}">${esc(REASON_LABEL[r.code] || r.code)}</span>`
      ).join("");

      const ringChip = d.ring_id ? `<span class="chip ring">${esc(d.ring_id)}</span>` : "";
      const truthChip = S.showTruth && tr && tr.is_fraud ?
        `<span class="chip truth">planted ${esc(tr.ring_id)} hop ${esc(tr.hop)}</span>` : "";
      const riskClass = d.risk >= 75 ? "high" : d.risk >= 45 ? "med" : "low";

      row.innerHTML =
        `<span class="tier-badge ${d.decision}">${TIER_ICON[d.decision]} ${d.decision}</span>` +
        `<span class="ids">${esc(ev.src)}<span class="arrow" aria-hidden="true">→</span>${esc(ev.dst)} <span class="muted">(${esc(ev.type)})</span></span>` +
        `<span class="amt tabular-nums">${inr(ev.amount)}</span>` +
        `<span class="meta">` +
          `<span class="risk-pill ${riskClass} tabular-nums">risk ${d.risk}</span>` +
          `<span class="muted tabular-nums" style="font-size:0.75rem">${simTime(ev.ts)}</span>` +
          chips + ringChip + truthChip +
        `</span>`;

      filterRow(row, d, ev);
      frag.prepend(row);
    }

    feed.prepend(frag);

    // Audio chime on new critical decisions
    if (hasHold) playChime("hold");
    else if (hasVerify) playChime("verify");

    // Fixed ring buffer pruning
    while (feed.children.length > MAX_FEED) {
      const last = feed.lastChild;
      S.items.delete(last.dataset.eid);
      feed.removeChild(last);
    }

    // Animated allow counter
    if (allowDelta > 0) {
      const el = $("allow-count");
      const oldVal = S.allow;
      S.allow += allowDelta;
      animateCounter(el, oldVal, S.allow, 400);
    }
  }

  /* Feed row click — delegated on feed container */
  $("feed").addEventListener("click", (e) => {
    const row = e.target.closest(".row");
    if (!row) return;
    document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel"));
    row.classList.add("sel");
    const it = S.items.get(row.dataset.eid);
    // If this item has a ring_id, highlight it in graph without switching tab
    if (row.dataset.rid) focusRing(row.dataset.rid, false);
    showDecision(it);
  });

  /* ------------------------------------------------------------------ Decision Dossier */
  function showDecision(it) {
    if (!it) return;
    const d = it.decision, ev = it.event;
    const shadow = d.shadow ? Object.entries(d.shadow).map(([k, v]) =>
      `<span class="k">${esc(k)}</span><span class="tabular-nums">${esc(v.decision)} (risk ${Math.round(v.score * 100)})</span>`).join("") : "";

    $("tab-detail").innerHTML = `<div class="detail">
      <h3><span class="tier-badge ${d.decision}">${TIER_ICON[d.decision]} ${d.decision}</span> Transaction #${esc(d.event_id)}</h3>

      <div class="risk-gauge-container">
        <div class="risk-gauge-header">
          <span style="font-weight:600; color:var(--text-bright)">Risk Assessment: <b class="tabular-nums">${d.risk}</b> / 100</span>
          <span class="tier-badge ${d.decision}">${TIER_ICON[d.decision]} ${d.decision.toUpperCase()}</span>
        </div>
        <div class="risk-gauge-track">
          <div class="risk-gauge-fill ${d.decision}" style="width: ${d.risk}%"></div>
        </div>
      </div>

      <p style="font-size:0.88rem; line-height:1.45; color:#f1f5f9; margin-bottom:12px;">${esc(d.recommended_action)}</p>

      <div class="kv">
        <span class="k">From Account</span><span><a href="#" data-acct="${esc(ev.src)}">${esc(ev.src)}</a></span>
        <span class="k">To Account</span><span><a href="#" data-acct="${esc(ev.dst)}">${esc(ev.dst)}</a></span>
        <span class="k">Amount</span><span class="tabular-nums">${inr(ev.amount)} <span class="muted">(${esc(ev.type)})</span></span>
        <span class="k">Timestamp</span><span class="tabular-nums">${simTime(ev.ts)}</span>
        <span class="k">Risk Score</span><span class="tabular-nums"><b>${d.risk}</b> / 100 <span class="muted">(ranking score)</span></span>
        <span class="k">Detector</span><span>${esc(d.detector)}</span>
        <span class="k">Latency</span><span class="tabular-nums">${us(d.latency_us.total)}</span>
        ${d.ring_id ? `<span class="k">Linked Group</span><span><b style="color:var(--relay, var(--role-relay))">${esc(d.ring_id)}</b></span>` : ""}
        ${shadow}
      </div>

      <div style="margin-top:12px; display:flex; flex-wrap:wrap; gap:8px;">
        <button class="btn mini primary" id="btn-export-dossier">&#128190; Export JSON</button>
        <button class="btn mini" id="btn-copy-tx">&#9112; Copy TX ID</button>
        ${d.ring_id ? `<button class="btn mini warn" id="btn-view-ring-tx">&#128269; View Ring ${esc(d.ring_id)}</button>` : ""}
      </div>

      <h3>Observed Reasons</h3>
      <ul class="reasons">${d.reasons.map((r) => `<li>${esc(r.text)}</li>`).join("")}</ul>
      ${it.truth && S.showTruth ? `<p class="muted" style="margin-top:10px">Ground truth (demo only): ${it.truth.is_fraud ? "part of planted ring " + esc(it.truth.ring_id) : "legitimate"}</p>` : ""}
    </div>`;

    $("btn-export-dossier")?.addEventListener("click", () => {
      downloadJson(`dossier-tx-${d.event_id}.json`, it);
    });
    $("btn-copy-tx")?.addEventListener("click", () => {
      copyText(String(d.event_id), "Transaction ID");
    });
    $("btn-view-ring-tx")?.addEventListener("click", () => {
      focusRing(d.ring_id, true);
      switchTab("rings");
    });

    switchTab("detail");
  }

  $("tab-detail").addEventListener("click", (e) => {
    const a = e.target.closest("a[data-acct]");
    if (a) { e.preventDefault(); showAccount(a.dataset.acct); }
  });

  /* ------------------------------------------------------------------ Account Dossier */
  async function showAccount(acct) {
    try {
      const v = await api("/v1/accounts/" + encodeURIComponent(acct));
      const inAmt = v.in_amt_24h || 0;
      const outAmt = v.out_amt_24h || 0;
      const totalFlow = inAmt + outAmt;
      const inPct = totalFlow > 0 ? Math.round((inAmt / totalFlow) * 100) : 50;
      const outPct = totalFlow > 0 ? 100 - inPct : 50;

      const rows = (v.recent_decisions || []).slice(0, 10).map((r) => {
        const isOut = r.src === acct;
        const arrow = isOut ? `<span style="color:#f97316;">&uarr; out</span> &rarr; ${esc(r.dst)}` : `<span style="color:#10b981;">&darr; in</span> &larr; ${esc(r.src)}`;
        return `<tr><td class="tabular-nums">${simTime(r.ts)}</td><td>${arrow}</td><td class="tabular-nums">${inr(r.amount)}</td><td><span class="tier-badge ${r.decision}">${r.decision}</span></td></tr>`;
      }).join("");

      $("tab-detail").innerHTML = `<div class="detail">
        <h3>Account ${esc(acct)}</h3>
        <p class="muted" style="font-size:0.85rem">This account ${v.suspicious ? "currently shows a relay-like pattern" : "shows no relay-like pattern right now"}. Holders of entry accounts may have been tricked.</p>

        <div class="flow-ratio-container">
          <div class="flow-ratio-header">
            <span>24h Inflow: <b class="tabular-nums" style="color:#6ee7b7">${inr(inAmt)}</b> (${inPct}%)</span>
            <span>24h Outflow: <b class="tabular-nums" style="color:#fdba74">${inr(outAmt)}</b> (${outPct}%)</span>
          </div>
          <div class="flow-ratio-bar">
            <div class="flow-in" style="width: ${inPct}%" title="Inflow: ${inr(inAmt)}"></div>
            <div class="flow-out" style="width: ${outPct}%" title="Outflow: ${inr(outAmt)}"></div>
          </div>
        </div>

        <div class="kv">
          <span class="k">First Seen</span><span class="tabular-nums">${simTime(v.first_seen)}</span>
          <span class="k">Last Seen</span><span class="tabular-nums">${simTime(v.last_seen)}</span>
          <span class="k">Linked Ring</span><span>${v.ring_id ? `<b style="color:var(--relay, var(--role-relay))">${esc(v.ring_id)}</b>` : "-"}</span>
          <span class="k">In (1h / 24h)</span><span class="tabular-nums">${v.in_cnt_1h ?? "-"} / ${v.in_cnt_24h ?? "-"} (${inr(v.in_amt_24h || 0)})</span>
          <span class="k">Out (1h / 24h)</span><span class="tabular-nums">${v.out_cnt_1h ?? "-"} / ${v.out_cnt_24h ?? "-"} (${inr(v.out_amt_24h || 0)})</span>
          <span class="k">Distinct Payers</span><span class="tabular-nums">${v.in_uniq_1h ?? "-"}</span>
          <span class="k">Unspent Credits</span><span class="tabular-nums">${v.unspent_credits ?? "-"}</span>
        </div>
        <div style="margin-top:12px; display:flex; flex-wrap:wrap; gap:8px;">
          <button class="btn mini primary" id="btn-focus-acct" data-acct="${esc(acct)}">&#128269; Locate in Graph</button>
          <button class="btn mini" id="btn-copy-acct">&#9112; Copy Account ID</button>
          <button class="btn mini" id="btn-export-acct">&#128190; Export Account JSON</button>
        </div>
        <h3>Recent Transactions</h3>
        <table class="cmp"><tbody>${rows || '<tr><td class="muted">none</td></tr>'}</tbody></table>
      </div>`;

      switchTab("detail");

      $("btn-focus-acct")?.addEventListener("click", () => {
        const n = cy.getElementById(acct);
        if (!n.empty()) {
          cy.elements().addClass("dim");
          n.removeClass("dim");
          cy.animate({
            center: { eles: n },
            zoom: 1.8,
            duration: 320,
            easing: "ease-out-cubic"
          });
        } else {
          toast(`Account ${acct} is not in an active ring graph`);
        }
      });

      $("btn-copy-acct")?.addEventListener("click", () => {
        copyText(acct, "Account ID");
      });

      $("btn-export-acct")?.addEventListener("click", () => {
        downloadJson(`account-${acct}.json`, v);
      });
    } catch (e) { /* toast already shown */ }
  }

  /* ------------------------------------------------------------------ Mule Rings & Analyst Actions */
  function upsertRing(ring) {
    if (!ring) return;
    if (ring.status === "closed") {
      S.rings.delete(ring.ring_id);
      removeRingFromGraph(ring.ring_id);
    } else {
      S.rings.set(ring.ring_id, ring);
      for (const m of ring.merged_from || []) {
        if (S.rings.has(m) && m !== ring.ring_id) {
          S.rings.delete(m);
          removeRingFromGraph(m);
        }
      }
      syncGraph(ring);
    }
  }

  function renderRings() {
    const list = Array.from(S.rings.values()).sort((a, b) =>
      (a.status === "alert" ? 0 : 1) - (b.status === "alert" ? 0 : 1) || b.ring_score - a.ring_score
    );
    $("ring-count").textContent = list.length;

    if (!list.length) {
      $("tab-rings").innerHTML = '<div class="empty-state"><div class="empty-icon">&#x2B21;</div><p class="muted">No linked groups. Start replay or inject a scenario.</p></div>';
      return;
    }

    $("tab-rings").innerHTML = list.map((r) => {
      const truth = S.showTruth && r.truth ?
        `<div class="muted" style="font-size:.78rem; margin-top:4px;">Ground truth (demo only): ${r.truth.ring_id ? "planted ring " + esc(r.truth.ring_id) + " (" + esc(r.truth.typology || "?") + ")" : "no planted ring"}, purity ${Math.round((r.truth.purity || 0) * 100)}%</div>` : "";

      return `<div class="ring-card ${r.status} ${S.selectedRing === r.ring_id ? "sel" : ""} ${S.reviewed.has(r.ring_id) ? "reviewed" : ""}" data-rid="${esc(r.ring_id)}">
        <div class="ring-top">
          <span class="ring-id">${esc(r.ring_id)}</span>
          <span class="status ${r.status}">${r.status === "alert" ? "■ hold recommended" : "▲ watching"}</span>
        </div>
        <div class="bar" title="ring score ${r.ring_score.toFixed(3)}">
          <i style="width:${Math.min(100, Math.round(r.ring_score * 100))}%"></i>
        </div>
        <div class="facts tabular-nums">
          <span>${r.n_accounts} accounts</span><span>${r.n_relays} relay${r.n_relays === 1 ? "" : "s"}</span>
          <span>longest chain: ${r.longest_chain}</span><span>exit: ${inr(r.exit_amount)}</span>
          <span>first: ${simTime(r.first_ts)}</span><span>${r.alert_ts != null ? "alert: " + simTime(r.alert_ts) : "no alert yet"}</span>
        </div>
        <ul class="reasons">${(r.reasons || []).map((x) => `<li>${esc(x.text)}</li>`).join("")}</ul>
        ${truth}
        <div class="ring-actions">
          <span>Action: review by analyst</span>
          <div style="display:flex; gap:6px;">
            <button class="btn mini" data-export-ring="${esc(r.ring_id)}" title="Export forensic JSON for this ring">&#128190; Export</button>
            <button class="btn mini" data-review="${esc(r.ring_id)}" title="Mark as reviewed locally">
              ${S.reviewed.has(r.ring_id) ? "✓ Reviewed" : "Mark reviewed"}
            </button>
          </div>
        </div>
      </div>`;
    }).join("");
  }

  // Delegated click handler for ring cards
  $("tab-rings").addEventListener("click", (e) => {
    const exp = e.target.closest("[data-export-ring]");
    if (exp) {
      e.stopPropagation();
      const id = exp.dataset.exportRing;
      const ring = S.rings.get(id);
      if (ring) downloadJson(`ring-${id}.json`, ring);
      return;
    }
    const rb = e.target.closest("[data-review]");
    if (rb) {
      const id = rb.dataset.review;
      S.reviewed.has(id) ? S.reviewed.delete(id) : S.reviewed.add(id);
      renderRings();
      return;
    }
    const card = e.target.closest(".ring-card");
    if (card) focusRing(card.dataset.rid);
  });

  /* ------------------------------------------------------------------ Baseline Comparison */
  function renderComparison() {
    const earlierCount = S.comparison.filter((r) => r.g2r_ts != null && r.exit_ts != null && r.g2r_ts < r.exit_ts).length;
    const totalCount = S.comparison.length;

    const summaryCards = totalCount > 0 ? `
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-bottom:12px;">
        <div style="background:rgba(6,182,212,0.08); border:1px solid rgba(6,182,212,0.25); border-radius:var(--radius-sm); padding:8px 10px;">
          <div style="font-size:0.62rem; text-transform:uppercase; color:var(--muted); font-weight:700; letter-spacing:0.06em;">Pre-Exit Interception</div>
          <div style="font-size:1.1rem; font-weight:700; color:var(--accent); font-family:ui-monospace, monospace; margin-top:2px;">${earlierCount} / ${totalCount} Rings</div>
        </div>
        <div style="background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.25); border-radius:var(--radius-sm); padding:8px 10px;">
          <div style="font-size:0.62rem; text-transform:uppercase; color:var(--muted); font-weight:700; letter-spacing:0.06em;">Detection Engine</div>
          <div style="font-size:1.1rem; font-weight:700; color:#6ee7b7; font-family:ui-monospace, monospace; margin-top:2px;">G2+R Early Lead</div>
        </div>
      </div>` : "";

    const rows = S.comparison.map((r) => {
      const b0 = r.b0_ts != null ? mins(r.b0_ts - r.first_ts) : "not flagged";
      const g2 = r.g2r_ts != null ? mins(r.g2r_ts - r.first_ts) : "not flagged";
      const ex = r.exit_ts != null ? mins(r.exit_ts - r.first_ts) : "-";
      const g2cls = r.g2r_ts != null && r.exit_ts != null ? (r.g2r_ts < r.exit_ts ? "early" : "late") : "";

      return `<tr>
        <td><b>${esc(r.ring_id)}</b><br><span class="muted">${esc(r.typology)}${r.injected ? " (injected)" : ""}</span></td>
        <td class="tabular-nums">${S.compare ? esc(b0) : '<span class="muted">compare off</span>'}</td>
        <td class="${g2cls} tabular-nums">${esc(g2)}</td>
        <td class="tabular-nums">${esc(ex)}</td>
      </tr>`;
    }).join("");

    $("tab-compare").innerHTML = `
      ${summaryCards}
      <p class="muted" style="font-size:0.80rem; margin-bottom:10px; line-height:1.4;">In this synthetic replay: elapsed time from a planted ring's first payment until two of its relay accounts received verify/hold actions.</p>
      ${S.compare ? "" : '<p style="color:var(--accent); font-size:0.84rem; margin-bottom:8px;">Turn on <b>Compare</b> to score every payment with the one-payment-at-a-time baseline model (B0).</p>'}
      <table class="cmp">
        <thead>
          <tr>
            <th>Planted Ring</th>
            <th>Single-Tx (B0)</th>
            <th>Ring-Aware (G2+R)</th>
            <th>First Cash-Out</th>
          </tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="4" class="muted">No planted ring has started yet.</td></tr>'}</tbody>
      </table>`;
  }

  /* ------------------------------------------------------------------ Telemetry & Metrics Sparkline */
  function renderMetrics(m) {
    if (!m) return;
    S.lastMetrics = m;
    const t = m.latency_us.total;
    $("m-p50").textContent = us(t.p50);
    $("m-p95").textContent = us(t.p95);
    $("m-p95-box").title = ["features", "model", "decision"].map((k) =>
      `${k}: p50 ${us(m.latency_us[k].p50)}, p95 ${us(m.latency_us[k].p95)}`).join("\n");
    $("m-eps").textContent = m.throughput.events_per_s_10s;
    $("m-verify").textContent = (m.decisions.verify || 0).toLocaleString("en-IN");
    $("m-hold").textContent = (m.decisions.hold || 0).toLocaleString("en-IN");
    $("m-rings").textContent = `${m.rings.open} / ${m.rings.alert}`;
    $("m-mem").textContent = m.memory.rss_mb != null ? m.memory.rss_mb + " MB" : "-";

    // KPI tiles
    const kpiEps = $("kpi-eps");
    if (kpiEps) kpiEps.textContent = m.throughput.events_per_s_10s;
    const kpiHolds = $("kpi-holds");
    if (kpiHolds) kpiHolds.textContent = (m.decisions.hold || 0).toLocaleString("en-IN");
    const kpiRings = $("kpi-rings");
    if (kpiRings) kpiRings.textContent = m.rings.alert;
    const kpiP95 = $("kpi-p95");
    if (kpiP95) kpiP95.textContent = us(t.p95);

    drawSpark(m.p95_series_us || []);
  }

  function drawSpark(series) {
    const c = $("spark"), g = c.getContext("2d");
    g.clearRect(0, 0, c.width, c.height);
    if (series.length < 2) return;
    const max = Math.max(...series) * 1.1, min = 0;

    // Gradient area fill
    const grad = g.createLinearGradient(0, 0, 0, c.height);
    grad.addColorStop(0, "rgba(56, 189, 248, 0.4)");
    grad.addColorStop(1, "rgba(56, 189, 248, 0.0)");

    g.beginPath();
    series.forEach((v, i) => {
      const x = (i / (series.length - 1)) * (c.width - 2) + 1;
      const y = c.height - 2 - ((v - min) / (max - min || 1)) * (c.height - 4);
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.lineTo(c.width - 1, c.height);
    g.lineTo(1, c.height);
    g.closePath();
    g.fillStyle = grad;
    g.fill();

    // Line stroke
    g.strokeStyle = css("--accent") || "#38bdf8";
    g.lineWidth = 1.8;
    g.beginPath();
    series.forEach((v, i) => {
      const x = (i / (series.length - 1)) * (c.width - 2) + 1;
      const y = c.height - 2 - ((v - min) / (max - min || 1)) * (c.height - 4);
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.stroke();

    // Latest value readout
    g.fillStyle = css("--text-muted") || "#94a3b8";
    g.font = "10px ui-monospace, Consolas, monospace";
    g.fillText(us(series[series.length - 1]), c.width - 64, 11);
  }

  /* ------------------------------------------------------------------ Replay Status */
  function renderReplay(r) {
    if (!r) return;
    S.replay = r;
    $("sim-clock").textContent = simTime(r.sim_ts);

    const b = $("btn-play");
    b.querySelector(".btn-icon").innerHTML = r.state === "running" ? "&#10074;&#10074;" : "&#9654;";
    b.querySelector(".btn-text").textContent = r.state === "running" ? "Pause" : "Play";

    const dot = $("replay-dot");
    if (dot) {
      dot.className = "replay-status-dot " + (r.state === "running" ? "running" : r.state === "paused" ? "paused" : "idle");
    }
    document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", Number(x.dataset.speed) === r.speed));
  }

  /* ------------------------------------------------------------------ Platform Info */
  function renderInfo(info) {
    if (!info) return;
    S.info = info;
    S.tz = info.tz_offset_s != null ? info.tz_offset_s : S.tz;
    const p = info.platform;
    const pb = $("plat-badge");
    pb.textContent = `${p.machine}, ${p.byteorder}-endian, Python ${p.python}`;
    pb.classList.toggle("s390x", p.machine === "s390x");
    $("prov-badge").textContent = info.model.provenance === "ChainWorld" ? "Synthetic data (ChainWorld)" : `Data: ${info.model.provenance}`;
    $("model-line").textContent = `model ${info.model.production} · trained on ${info.model.trained_on} · v${info.model.model_version}`;
    S.compare = !!info.compare_mode;
    $("chk-compare").checked = S.compare;
  }

  /* ------------------------------------------------------------------ Resilient WebSocket Engine */
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/v1/ws${API_KEY ? "?key=" + encodeURIComponent(API_KEY) : ""}`);
    S.ws = ws;
    const dot = $("conn-dot"), txt = $("conn-text");

    ws.onopen = () => {
      S.backoff = 1000;
      dot.className = "dot ok";
      txt.textContent = "live";
    };

    ws.onclose = () => {
      dot.className = "dot bad";
      txt.textContent = "reconnecting";
      setTimeout(connect, S.backoff);
      S.backoff = Math.min(15000, S.backoff * 1.6);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (m) => {
      let msg;
      try { msg = JSON.parse(m.data); } catch (e) { return; }

      if (msg.type === "snapshot") {
        renderInfo(msg.info);
        S.rings.clear();
        cy.elements().remove();
        S.nodeRings.clear();
        S.edgeRing.clear();
        S.ringOrder = [];
        (msg.rings || []).forEach(upsertRing);
        renderRings();
        addItems(msg.recent || []);
        renderReplay(msg.replay);
        renderMetrics(msg.metrics);
        if (msg.comparison) { S.comparison = msg.comparison; renderComparison(); }
      } else if (msg.type === "batch") {
        addItems(msg.items || []);
        for (const re of msg.ring_events || []) upsertRing(re.ring);
        if ((msg.ring_events || []).length) renderRings();
        renderReplay(msg.replay);
        renderMetrics(msg.metrics);
        if (msg.comparison) { S.comparison = msg.comparison; renderComparison(); }
        if (msg.dropped) toast(`${msg.dropped} updates were skipped to keep browser responsive`);
      } else if (msg.type === "fastforward") {
        toast(`Fast-forwarded ${msg.skipped.toLocaleString("en-IN")} payments to ${simTime(msg.to_ts)}`);
        S.rings.clear();
        cy.elements().remove();
        S.nodeRings.clear();
        S.edgeRing.clear();
        S.ringOrder = [];
        (msg.rings || []).forEach(upsertRing);
        renderRings();
        renderReplay(msg.replay);
      }
    };
  }

  /* ------------------------------------------------------------------ UI Tabs */
  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      const active = t.dataset.tab === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    ["rings", "compare", "detail"].forEach((n) => $("tab-" + n).classList.toggle("hidden", n !== name));
    if (name === "compare") renderComparison();
  }

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  /* ------------------------------------------------------------------ Replay Controls */
  async function playPause() {
    const st = S.replay ? S.replay.state : "idle";
    const speed = Number((document.querySelector(".seg-btn.active") || {}).dataset?.speed || 300);
    if (st === "running") renderReplay(await api("/v1/replay/pause", "POST"));
    else if (st === "paused") renderReplay(await api("/v1/replay/resume", "POST"));
    else {
      toast("Starting replay (warm-up initializing)");
      renderReplay(await api("/v1/replay/start", "POST", { speed }));
    }
  }

  async function nextCampaign() {
    const r = await api("/v1/replay/next_campaign", "POST");
    renderReplay(r);
  }

  async function inject() {
    const sel = $("sel-typology");
    const typ = sel ? sel.value : "funnel";
    const btn = $("btn-inject");
    const origIcon = btn?.querySelector(".btn-icon")?.innerHTML || "&#9889;";
    const origText = btn?.querySelector(".btn-text")?.textContent || "Inject";

    if (btn) {
      btn.classList.add("injecting");
      const iconSpan = btn.querySelector(".btn-icon");
      const textSpan = btn.querySelector(".btn-text");
      if (iconSpan) iconSpan.innerHTML = "&#8987;";
      if (textSpan) textSpan.textContent = "Injecting...";
      btn.disabled = true;
    }

    try {
      // 1. If replay is idle, auto-start it so injection doesn't 409
      const currentState = S.replay?.state || "idle";
      if (currentState === "idle") {
        toast("Initializing replay engine for injection...");
        const speed = Number((document.querySelector(".seg-btn.active") || {}).dataset?.speed || 300);
        const rep = await api("/v1/replay/start", "POST", { speed });
        renderReplay(rep);
      }

      // 2. Post injection request
      const r = await api("/v1/inject/scenario", "POST", {
        typology: typ,
        seed: Math.floor(Math.random() * 1e6)
      });

      // 3. If replay was paused, resume it so sim timestamp advances and events flow
      if (S.replay?.state === "paused") {
        toast("Resuming stream to process injected campaign...");
        const rep = await api("/v1/replay/resume", "POST");
        renderReplay(rep);
      }

      // 4. Switch to Rings tab so user immediately sees rings being detected
      switchTab("rings");

      // 5. Button success feedback
      if (btn) {
        btn.classList.remove("injecting");
        btn.classList.add("injected");
        const iconSpan = btn.querySelector(".btn-icon");
        const textSpan = btn.querySelector(".btn-text");
        if (iconSpan) iconSpan.innerHTML = "&#10003;";
        if (textSpan) textSpan.textContent = "Injected!";
      }

      const label = sel?.options[sel.selectedIndex]?.text || typ;
      toast(`Injected ${label} campaign ${r.ring_id} (${r.n_events} payments scheduled)`);

      setTimeout(() => {
        if (btn) {
          btn.classList.remove("injected");
          const iconSpan = btn.querySelector(".btn-icon");
          const textSpan = btn.querySelector(".btn-text");
          if (iconSpan) iconSpan.innerHTML = origIcon;
          if (textSpan) textSpan.textContent = origText;
          btn.disabled = false;
        }
      }, 1800);

    } catch (err) {
      if (btn) {
        btn.classList.remove("injecting");
        btn.classList.remove("injected");
        const iconSpan = btn.querySelector(".btn-icon");
        const textSpan = btn.querySelector(".btn-text");
        if (iconSpan) iconSpan.innerHTML = origIcon;
        if (textSpan) textSpan.textContent = origText;
        btn.disabled = false;
      }
    }
  }

  $("btn-play").addEventListener("click", () => playPause().catch(() => {}));
  $("btn-next").addEventListener("click", () => nextCampaign().catch(() => {}));
  $("btn-inject").addEventListener("click", () => inject().catch(() => {}));

  $("sel-typology")?.addEventListener("change", (e) => {
    const sel = e.target;
    const txt = sel.options[sel.selectedIndex]?.text || sel.value;
    toast(`Typology selected: ${txt} — Click "Inject" to simulate`);
  });

  $("btn-reset").addEventListener("click", async () => {
    try {
      renderReplay(await api("/v1/replay/reset", "POST"));
      S.rings.clear();
      cy.elements().remove();
      S.nodeRings.clear();
      S.edgeRing.clear();
      S.ringOrder = [];
      S.allow = 0;
      $("feed").innerHTML = "";
      S.comparison = [];
      renderRings();
      renderComparison();
      toast("Engine state rebuilt; press Play to restart stream");
    } catch (e) { /* toast shown */ }
  });

  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", async () => {
    try {
      renderReplay(await api("/v1/replay/speed", "POST", { speed: Number(b.dataset.speed) }));
    } catch (e) { /* */ }
  }));

  $("chk-compare").addEventListener("change", async (e) => {
    try {
      const enabled = e.target.checked;
      const r = await api("/v1/compare", "POST", { enabled });
      S.compare = r.compare_mode;
      renderComparison();
      if (enabled) {
        switchTab("compare");
        toast("Compare mode active: evaluating against B0 baseline");
      } else {
        toast("Compare mode disabled");
      }
    } catch (err) {
      e.target.checked = !e.target.checked;
    }
  });

  $("chk-truth").addEventListener("change", (e) => {
    S.showTruth = e.target.checked;
    cy.nodes().forEach((n) => n.toggleClass("truth", S.showTruth && S.truthRole.has(n.id())));
    renderRings();
  });

  /* ------------------------------------------------------------------ Sound Alert Toggle */
  $("btn-sound-toggle")?.addEventListener("click", () => {
    S.audioEnabled = !S.audioEnabled;
    const btn = $("btn-sound-toggle");
    if (btn) {
      btn.classList.toggle("active", S.audioEnabled);
      const icon = btn.querySelector(".btn-icon");
      if (icon) icon.innerHTML = S.audioEnabled ? "&#128266;" : "&#128263;";
      btn.title = S.audioEnabled ? "Audio alerts enabled (Click to mute)" : "Toggle audio chime on hold alerts";
    }
    toast(S.audioEnabled ? "Audio chime alerts enabled for Hold / Verify" : "Audio alerts muted");
    if (S.audioEnabled) playChime("verify");
  });

  /* ------------------------------------------------------------------ Interactive KPI Tiles */
  $("kpi-tile-eps")?.addEventListener("click", () => setTierFilter("all"));
  $("kpi-tile-holds")?.addEventListener("click", () => {
    setTierFilter(S.filterTier === "hold" ? "all" : "hold");
  });
  $("kpi-tile-rings")?.addEventListener("click", () => switchTab("rings"));
  $("kpi-tile-p95")?.addEventListener("click", () => {
    const m = S.lastMetrics;
    if (m && m.latency_us) {
      toast(`Latency Breakdown: Features ${us(m.latency_us.features?.p95)} | Model ${us(m.latency_us.model?.p95)} | Grouping ${us(m.latency_us.decision?.p95)}`);
    } else {
      toast("Pure Python streaming inference latency under target SLO");
    }
  });

  /* ------------------------------------------------------------------ Feed Keyboard Navigation */
  function navigateFeed(delta) {
    const feed = $("feed");
    const visible = Array.from(feed.querySelectorAll(".row:not(.hidden)"));
    if (visible.length === 0) return;
    const current = feed.querySelector(".row.sel");
    let idx = visible.indexOf(current);
    if (idx === -1) {
      idx = delta > 0 ? 0 : visible.length - 1;
    } else {
      idx = Math.max(0, Math.min(visible.length - 1, idx + delta));
    }
    const target = visible[idx];
    if (target) {
      document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel"));
      target.classList.add("sel");
      target.scrollIntoView({ block: "nearest", behavior: "smooth" });
      const it = S.items.get(target.dataset.eid);
      if (target.dataset.rid) focusRing(target.dataset.rid, false);
      showDecision(it);
    }
  }

  /* ------------------------------------------------------------------ Modal */
  $("btn-help")?.addEventListener("click", () => $("help-modal")?.classList.remove("hidden"));
  $("btn-close-help")?.addEventListener("click", () => $("help-modal")?.classList.add("hidden"));
  $("btn-close-help-x")?.addEventListener("click", () => $("help-modal")?.classList.add("hidden"));
  $("help-modal")?.addEventListener("click", (e) => {
    if (e.target === $("help-modal")) $("help-modal").classList.add("hidden");
  });

  /* ------------------------------------------------------------------ Keyboard Accelerators */
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.code === "Space") {
      e.preventDefault();
      playPause().catch(() => {});
    } else if (e.key === "n" || e.key === "N") {
      nextCampaign().catch(() => {});
    } else if (e.key === "i" || e.key === "I") {
      inject().catch(() => {});
    } else if (e.key === "c" || e.key === "C") {
      $("chk-compare").checked = !$("chk-compare").checked;
      $("chk-compare").dispatchEvent(new Event("change"));
    } else if (e.key === "g" || e.key === "G") {
      $("chk-truth").checked = !$("chk-truth").checked;
      $("chk-truth").dispatchEvent(new Event("change"));
    } else if (e.key === "f" || e.key === "F") {
      toggleFullscreen();
    } else if (e.key === "1") {
      switchTab("rings");
    } else if (e.key === "2") {
      switchTab("compare");
    } else if (e.key === "3") {
      switchTab("detail");
    } else if (e.key === "/" || (e.key === "k" && (e.ctrlKey || e.metaKey))) {
      e.preventDefault();
      const inp = $("feed-filter");
      if (inp) { inp.focus(); inp.select(); }
    } else if (e.key === "h" || e.key === "H") {
      setTierFilter(S.filterTier === "hold" ? "all" : "hold");
    } else if (e.key === "a" || e.key === "A") {
      setTierFilter("all");
    } else if (e.key === "j" || e.key === "J" || e.key === "ArrowDown") {
      e.preventDefault();
      navigateFeed(1);
    } else if (e.key === "k" || e.key === "K" || e.key === "ArrowUp") {
      e.preventDefault();
      navigateFeed(-1);
    } else if (e.key === "Escape") {
      if ($("graph-panel")?.classList.contains("fullscreen")) {
        toggleFullscreen();
      } else if (!document.querySelector(".modal-overlay")?.classList.contains("hidden")) {
        $("help-modal")?.classList.add("hidden");
      } else {
        clearSelection();
      }
    } else if (e.key === "?") {
      $("help-modal")?.classList.toggle("hidden");
    }
  });

  renderComparison();
  connect();
})();
