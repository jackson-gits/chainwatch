"""Legitimate (background) activity for ChainWorld (spec 8.2.2), including the hard-negative archetypes.

Bulk customer traffic is generated day by day with numpy into ``PlainIntents``; archetypes whose amounts
depend on money that arrived earlier (merchant payouts, traders, family hubs, split bills, charity
collections) are ``Intent`` objects that forward a fraction of a pool.
"""

from __future__ import annotations

import numpy as np

from chainwatch.data.world.balances import (
    CASH_IN,
    CASH_OUT,
    DEBIT,
    FIXED,
    FORWARD,
    PAYMENT,
    TRANSFER,
    Intent,
    PlainIntents,
)
from chainwatch.data.world.population import DAY_S, HOUR_P, NEW_CUSTOMER, Population, lognormal

# type mix per customer event (spec 8.2.2): PAYMENT 50%, TRANSFER 35%, CASH_OUT 8%, CASH_IN 6%, DEBIT 1%
TYPE_SHARES = ((PAYMENT, 0.50), (TRANSFER, 0.35), (CASH_OUT, 0.08), (CASH_IN, 0.06), (DEBIT, 0.01))
AMOUNT_LAWS = {
    TRANSFER: (800.0, 1.1),
    PAYMENT: (250.0, 0.9),
    CASH_OUT: (2000.0, 0.8),
    CASH_IN: (3000.0, 0.8),
    DEBIT: (1200.0, 0.7),
}


def local_day_info(cfg, d: int) -> tuple[int, int]:
    """(day_of_month, weekday) of local sim day ``d``; months are 30 days counted from sim day 0 as day 1."""
    return d % 30 + 1, (int(cfg.world.t0_weekday) + d) % 7


def calendar_weight(cfg, typ: int, d: int) -> float:
    dom, wd = local_day_info(cfg, d)
    w = 1.0
    if typ in (TRANSFER, PAYMENT) and dom in (1, 2, 29, 30):
        w *= 1.5
    if typ == PAYMENT and wd >= 5:
        w *= 1.2
    return w


def sample_local_ts(rng: np.random.Generator, day: int, n: int, tz: float, hour_p=HOUR_P) -> np.ndarray:
    h = rng.choice(24, size=n, p=hour_p)
    return day * DAY_S + h * 3600.0 + rng.uniform(0.0, 3600.0, size=n) - tz


def round_some(
    rng: np.random.Generator, amount: np.ndarray, share: float, bases=(10.0, 50.0, 100.0)
) -> np.ndarray:
    amount = amount.copy()
    pick = rng.random(amount.size) < share
    base = np.asarray(bases)[rng.integers(0, len(bases), size=amount.size)]
    rounded = np.maximum(base, np.round(amount / base) * base)
    amount[pick] = rounded[pick]
    return amount


def generate_background(
    cfg, pop: Population, rng: np.random.Generator, extra_bg: list[tuple[int, float, float]]
):
    """Return ``(plain, specials)`` for all legitimate activity.

    ``extra_bg`` lists ``(account, active_from_ts, active_to_ts)`` for mule-pool accounts that need ordinary
    history before going dormant.
    """
    print("[DEBUG BG] Entering generate_background", flush=True)
    w = cfg.world
    days = int(w.days)
    end = days * DAY_S
    tz = float(cfg.project.tz_offset_s)
    cap = float(w.upi_txn_cap_inr)
    noise = float(w.noise_level)
    sig = 0.75 + 0.25 * noise
    boost = float(w.hard_negative_boost)
    C = pop.count("customer")
    a_start = pop.start("agent")
    n_agents = pop.count("agent")
    m_start = pop.start("merchant")
    n_m = pop.count("merchant")
    p_new_payee = min(0.5, 0.15 * noise)
    print(f"[DEBUG BG] days={days}, noise={noise}, boost={boost}, C={C}, len(extra_bg)={len(extra_bg)}", flush=True)

    plain = PlainIntents()
    specials: list[Intent] = []

    # ---------------- bulk background (customers, landlords, pre-dormancy mule history) ----------------
    accs = [np.arange(C)]
    lam = [np.clip(lognormal(rng, 0.6, 1.0 * sig, C), 0.02, 30.0)]
    a_from = [pop.active_start.copy()]
    a_to = [np.full(C, np.inf)]
    g_from = [pop.dormant_from.copy()]
    g_to = [pop.dormant_to.copy()]
    l_a, l_b = pop.ranges["landlord"]
    nl = l_b - l_a
    accs.append(np.arange(l_a, l_b))
    lam.append(np.full(nl, 0.4))
    a_from.append(np.zeros(nl))
    a_to.append(np.full(nl, np.inf))
    g_from.append(np.full(nl, np.inf))
    g_to.append(np.full(nl, np.inf))
    if extra_bg:
        eb = np.array([e[0] for e in extra_bg], dtype=np.int64)
        accs.append(eb)
        lam.append(np.clip(lognormal(rng, 0.6, 1.0 * sig, eb.size), 0.05, 10.0))
        a_from.append(np.array([e[1] for e in extra_bg]))
        a_to.append(np.array([e[2] for e in extra_bg]))
        g_from.append(np.full(eb.size, np.inf))
        g_to.append(np.full(eb.size, np.inf))
    accs_a = np.concatenate(accs)
    lam_a = np.concatenate(lam)
    from_a, to_a = np.concatenate(a_from), np.concatenate(a_to)
    gf_a, gt_a = np.concatenate(g_from), np.concatenate(g_to)
    is_cust = accs_a < C
    new_mask = np.zeros(accs_a.size, dtype=bool)
    new_mask[:C] = pop.cust_kind == NEW_CUSTOMER
    pos_idx = np.arange(accs_a.size)
    coff, cidx = pop.contacts_off, pop.contacts_idx

    for d in range(days + 1):
        if d % 5 == 0:
            print(f'[DEBUG BG] Processing day {d}', flush=True)
        for typ, share in TYPE_SHARES:
            rate = lam_a * (share * calendar_weight(cfg, typ, d))
            cnt = rng.poisson(rate)
            n = int(cnt.sum())
            if n == 0:
                continue
            k = np.repeat(pos_idx, cnt)
            ts = sample_local_ts(rng, d, n, tz)
            keep = (
                (ts >= 0.0)
                & (ts < end)
                & (ts >= from_a[k])
                & (ts < to_a[k])
                & ~((ts >= gf_a[k]) & (ts < gt_a[k]))
            )
            k, ts = k[keep], ts[keep]
            n = k.size
            if n == 0:
                continue
            acct = accs_a[k]
            cust = is_cust[k]
            med, s = AMOUNT_LAWS[typ]
            amount = np.clip(lognormal(rng, med, s * sig, n), 1.0, cap)
            if typ == TRANSFER:
                amount = round_some(rng, amount, 0.30)
            elif typ in (CASH_OUT, CASH_IN):
                amount = round_some(rng, amount, 0.70, bases=(100.0, 100.0, 500.0))
            young = new_mask[k] & (ts < from_a[k] + 2 * DAY_S)
            if young.any():
                amount[young] *= rng.uniform(0.1, 0.5, size=int(young.sum()))
            amount = np.clip(amount, 1.0, cap)
            if typ == PAYMENT:
                src, dst = acct, m_start + rng.choice(n_m, size=n, p=pop.merchant_p)
            elif typ == DEBIT:
                src, dst = acct, pop.billers[rng.integers(0, pop.billers.size, size=n)]
            elif typ == TRANSFER:
                dst = rng.integers(0, C, size=n)
                use_contact = cust & (rng.random(n) >= p_new_payee)
                ci = acct[use_contact]
                lo, hi = coff[ci], coff[ci + 1]
                pick = lo + (rng.random(ci.size) * (hi - lo)).astype(np.int64)
                dst[use_contact] = cidx[pick]
                clash = dst == acct
                dst[clash] = (dst[clash] + 1) % C
                src = acct
            else:  # CASH_OUT / CASH_IN with the home agent (80%) or a popular agent
                agent = a_start + rng.choice(n_agents, size=n, p=pop.agent_p)
                home = cust & (rng.random(n) < 0.8)
                agent[home] = pop.home_agent[acct[home]]
                if typ == CASH_OUT:
                    src, dst = acct, agent
                else:
                    src, dst = agent, acct
            plain.add(ts, src, dst, typ, amount)

    # ---------------- salary payers: day 1 (allowances), last day (salary), optional weekly payouts --------
    for payer, emp, amounts, weekly in pop.salary:
        for d in range(days + 1):
            dom, wd = local_day_info(cfg, d)
            if dom == 1:
                part = rng.uniform(0.1, 0.3, size=emp.size)
            elif dom == 30:
                part = np.ones(emp.size)
            else:
                part = None
            if part is not None:
                start_local = d * DAY_S + rng.uniform(10.0, 12.0) * 3600.0
                ts = start_local + rng.uniform(0.0, 5400.0, size=emp.size) - tz
                plain.add(ts, np.full(emp.size, payer), emp, TRANSFER, np.round(amounts * part, 2))
            if weekly is not None and wd == weekly[0] and dom not in (1, 30):
                sub, w_amt = weekly[1], weekly[2]
                start_local = d * DAY_S + rng.uniform(9.0, 18.0) * 3600.0
                ts = start_local + rng.uniform(0.0, 3600.0, size=sub.size) - tz
                plain.add(ts, np.full(sub.size, payer), sub, TRANSFER, np.round(w_amt, 2))

    # ---------------- landlords: rent within days 1 to 5 ----------------
    for landlord, tenants, rents, rent_days in pop.landlords:
        ts = (
            rent_days * DAY_S
            + rng.choice(24, size=tenants.size, p=HOUR_P) * 3600.0
            + rng.uniform(0, 3600.0, size=tenants.size)
            - tz
        )
        plain.add(ts, tenants, np.full(tenants.size, landlord), TRANSFER, np.round(rents, 2))

    # ---------------- merchants: one payout a day around 22:00 to the owner ----------------
    for j in range(n_m):
        m = m_start + j
        owner = int(pop.merchant_owner[j])
        for d in range(days + 1):
            ts = d * DAY_S + 22 * 3600.0 + rng.normal(0.0, 1800.0) - tz
            specials.append(
                Intent(
                    ts,
                    FORWARD,
                    m,
                    owner,
                    TRANSFER,
                    fraction=rng.uniform(0.85, 1.0),
                    src_pool=("b", m),
                    cap=cap,
                    drain=True,
                )
            )

    # ---------------- traders: business-hours inflows forwarded to stable suppliers ----------------
    for trader, clients, suppliers, pass_through, rate in pop.traders:
        sup_p = 1.0 / np.arange(1, suppliers.size + 1) ** 1.0
        sup_p /= sup_p.sum()
        for d in range(days + 1):
            _, wd = local_day_info(cfg, d)
            factor = 1.0 if wd < 5 else (0.5 if wd == 5 else 0.1)
            n = int(rng.poisson(rate * factor))
            for _ in range(n):
                ts = d * DAY_S + rng.uniform(9.0, 19.0) * 3600.0 - tz
                client = int(clients[rng.integers(0, clients.size)])
                amount = float(np.clip(lognormal(rng, 20000.0, 0.7 * sig), 5000.0, 80000.0))
                specials.append(Intent(ts, FIXED, client, trader, TRANSFER, amount=round(amount, 2)))
                delay = float(np.clip(lognormal(rng, 4500.0, 0.8), 1200.0, 21600.0))
                frac = min(1.0, pass_through * rng.uniform(0.9, 1.1))
                sup = int(suppliers[rng.choice(suppliers.size, p=sup_p)])
                specials.append(
                    Intent(
                        ts + delay,
                        FORWARD,
                        trader,
                        sup,
                        TRANSFER,
                        fraction=frac,
                        src_pool=("b", trader),
                        cap=cap,
                        drain=True,
                    )
                )

    # ---------------- family hubs: relatives send, hub forwards 50 to 90% within hours ----------------
    for hub, relatives, payees in pop.families:
        for rel in relatives:
            for d in range(days + 1):
                if rng.random() >= 0.1 * boost:
                    continue
                ts = float(sample_local_ts(rng, d, 1, tz)[0])
                amount = float(np.clip(lognormal(rng, 3000.0, 0.8 * sig), 100.0, cap))
                specials.append(
                    Intent(
                        ts, FIXED, int(rel), hub, TRANSFER, amount=round(amount, 2), credit_pool=("f", hub)
                    )
                )
                if rng.random() < 0.6:
                    delay = float(np.clip(lognormal(rng, 5 * 3600.0, 0.9), 3600.0, 3 * DAY_S))
                    payee = int(payees[rng.integers(0, payees.size)])
                    specials.append(
                        Intent(
                            ts + delay,
                            FORWARD,
                            hub,
                            payee,
                            TRANSFER,
                            fraction=rng.uniform(0.5, 0.9),
                            src_pool=("f", hub),
                            drain=True,
                        )
                    )

    # ---------------- split bills: fan-in from friends, quick forward to a stable merchant ----------------
    gid = 0
    regular = pop.regular_pool
    for d in range(days + 1):
        n_groups = int(rng.poisson(float(w.split_bill_rate) * boost * C))
        for _ in range(n_groups):
            org = int(regular[rng.integers(0, regular.size)])
            k = int(rng.integers(3, 6))
            lo, hi = int(coff[org]), int(coff[org + 1])
            cand = np.unique(cidx[lo:hi])
            cand = cand[cand != org]
            if cand.size >= k:
                friends = rng.choice(cand, size=k, replace=False)
            else:
                extra = rng.integers(0, C, size=k - cand.size)
                friends = np.concatenate([cand, extra])
                friends = friends[friends != org]
            if friends.size == 0:
                continue
            merchant = int(pop.fav_merchant[org])
            t0 = float(sample_local_ts(rng, d, 1, tz)[0])
            bill = float(np.clip(lognormal(rng, 2000.0, 0.6 * sig), 200.0, cap))
            share = bill / (friends.size + 1)
            specials.append(Intent(t0, FIXED, org, merchant, PAYMENT, amount=round(share, 2)))
            last = t0
            for f in friends:
                t = t0 + rng.uniform(300.0, 1800.0)
                last = max(last, t)
                specials.append(
                    Intent(
                        t,
                        FIXED,
                        int(f),
                        org,
                        TRANSFER,
                        amount=round(share * rng.uniform(0.95, 1.05), 2),
                        credit_pool=("s", gid),
                    )
                )
            specials.append(
                Intent(
                    last + rng.uniform(600.0, 2400.0),
                    FORWARD,
                    org,
                    merchant,
                    PAYMENT,
                    fraction=rng.uniform(0.95, 1.0),
                    src_pool=("s", gid),
                )
            )
            gid += 1

    # ---------------- charity collections: honest flash crowds ----------------
    n_char = int(round(float(w.n_charity) * boost))
    lo_day = float(w.campaign_start_min_day)
    hi_day = max(lo_day + 0.25, days - float(w.campaign_end_margin_days))
    for cid in range(n_char):
        start = rng.uniform(lo_day, hi_day) * DAY_S
        collector = int(regular[rng.integers(0, regular.size)])
        n_donors = int(rng.integers(20, 101))
        donors = rng.choice(C, size=n_donors, replace=False)
        donors = donors[donors != collector]
        offsets = rng.beta(1.2, 2.5, size=donors.size) * 7200.0
        amounts = np.clip(lognormal(rng, 500.0, 0.7 * sig, donors.size), 100.0, 2000.0)
        for dnr, off, amt in zip(donors, offsets, amounts, strict=False):
            specials.append(
                Intent(
                    start + off,
                    FIXED,
                    int(dnr),
                    collector,
                    TRANSFER,
                    amount=round(float(amt), 2),
                    credit_pool=("c", cid),
                )
            )
        last = start + float(offsets.max()) if offsets.size else start
        delay = rng.uniform(300.0, 900.0) if rng.random() < 0.2 else rng.uniform(3600.0, DAY_S)
        if rng.random() < 0.6:
            dst, typ = m_start + int(rng.choice(n_m, p=pop.merchant_p)), PAYMENT
        else:
            dst, typ = int(rng.integers(0, C)), TRANSFER
        chunks = int(rng.integers(1, 4))
        t = last + delay
        for i in range(chunks):
            frac = 1.0 / (chunks - i) if i < chunks - 1 else rng.uniform(0.9, 1.0)
            specials.append(
                Intent(t, FORWARD, collector, dst, typ, fraction=frac, src_pool=("c", cid), cap=cap)
            )
            t += rng.uniform(120.0, 1200.0)

    print('[DEBUG BG] About to return', flush=True)
    return plain, specials
