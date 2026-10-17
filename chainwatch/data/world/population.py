"""ChainWorld population: accounts, archetypes and the stable relationships between them (spec 8.2.1).

Accounts are integer indices internally. Opaque string ids (``A`` + 8 digits) come from a seeded random
sample, so nothing in an id reveals archetype, role or mule status (rule D1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Local hour-of-day weights (00:00 ... 23:00), normalised (spec 8.2.2).
HOUR_WEIGHTS = np.array(
    [
        0.20,
        0.10,
        0.08,
        0.06,
        0.08,
        0.20,
        0.50,
        0.90,
        1.30,
        1.50,
        1.60,
        1.70,
        1.70,
        1.50,
        1.40,
        1.50,
        1.60,
        1.80,
        2.00,
        2.10,
        1.90,
        1.40,
        0.90,
        0.50,
    ]
)
HOUR_P = HOUR_WEIGHTS / HOUR_WEIGHTS.sum()
DAY_S = 86400.0

# Customer sub-kinds
REGULAR, FAMILY_HUB, NEW_CUSTOMER, DORMANT_CUSTOMER = 0, 1, 2, 3
CUSTOMER_KIND_NAMES = {
    REGULAR: "customer",
    FAMILY_HUB: "family_hub",
    NEW_CUSTOMER: "new_customer",
    DORMANT_CUSTOMER: "dormant_customer",
}


def lognormal(rng: np.random.Generator, median: float, sigma: float, size=None) -> np.ndarray:
    """LogNormal parameterised by its median."""
    return median * np.exp(sigma * rng.standard_normal(size))


def zipf_probs(n: int, a: float) -> np.ndarray:
    w = 1.0 / np.arange(1, n + 1, dtype=float) ** a
    return w / w.sum()


@dataclass
class Population:
    n: int
    ids: list[str]
    archetype: list[str]
    tracked: np.ndarray  # bool: balance tracked (null for merchants and agents)
    balance0: np.ndarray  # initial balance (0 where untracked)
    ranges: dict[str, tuple[int, int]]
    cust_kind: np.ndarray  # per customer (index < n_customers)
    active_start: np.ndarray  # per customer: first moment it may act (ts)
    dormant_from: np.ndarray  # per customer: start of dormancy (inf = none)
    dormant_to: np.ndarray  # per customer: end of dormancy
    contacts_off: np.ndarray
    contacts_idx: np.ndarray
    agent_p: np.ndarray  # agent popularity
    home_agent: np.ndarray  # per customer (global index)
    merchant_p: np.ndarray  # merchant popularity (Zipf 1.1), over merchant offsets
    merchant_owner: np.ndarray  # per merchant: owning customer (global index)
    billers: np.ndarray  # global indices of merchants that receive DEBITs
    fav_merchant: np.ndarray  # per customer: stable merchant used by split-bill groups
    regular_pool: np.ndarray  # global indices of ordinary customers (victims, active mules, organisers)
    business_clients: np.ndarray  # customers with larger balances who pay traders
    salary: list = field(default_factory=list)  # (payer, employees, amounts, pay_day)
    landlords: list = field(default_factory=list)  # (landlord, tenants, rents, days)
    traders: list = field(default_factory=list)  # (trader, clients, suppliers, pass_through, rate)
    families: list = field(default_factory=list)  # (hub, relatives, payees)
    # filled by campaign planning
    is_mule: np.ndarray | None = None
    is_unwitting: np.ndarray | None = None
    first_ring: np.ndarray | None = None
    mule_kind: dict = field(default_factory=dict)  # account -> "new" | "dormant" | "active"
    used: np.ndarray | None = None  # account appears in accounts.csv

    def start(self, name: str) -> int:
        return self.ranges[name][0]

    def count(self, name: str) -> int:
        a, b = self.ranges[name]
        return b - a


def mule_pool_size(n_campaigns: int) -> int:
    return max(64, 14 * n_campaigns)


def build_population(cfg, rng: np.random.Generator) -> Population:
    """Build accounts, archetypes and relationships (spec 8.2.4 step 1)."""
    w = cfg.world
    days = float(w.days)
    boost = float(w.hard_negative_boost)
    noise = float(w.noise_level)
    sig = 0.75 + 0.25 * noise

    C = int(w.n_customers)
    sizes = [
        ("customer", C),
        ("merchant", int(w.n_merchants)),
        ("trader", int(w.n_traders)),
        ("salary_payer", int(w.n_salary_payers)),
        ("landlord", int(w.n_landlords)),
        ("agent", int(w.n_agents)),
        ("mule_pool", mule_pool_size(int(w.n_campaigns))),
    ]
    ranges: dict[str, tuple[int, int]] = {}
    pos = 0
    for name, k in sizes:
        ranges[name] = (pos, pos + k)
        pos += k
    n = pos

    # opaque ids from a seeded sample of 8-digit numbers
    nums = rng.choice(100_000_000, size=n, replace=False)
    ids = [f"A{int(x):08d}" for x in nums]

    archetype = [""] * n
    for name, (a, b) in ranges.items():
        label = "mule_account" if name == "mule_pool" else name
        for i in range(a, b):
            archetype[i] = label

    # customer sub-kinds
    perm = rng.permutation(C)
    k_fam = int(round(C * float(w.family_hub_share) * boost))
    k_new = int(round(C * float(w.new_customer_share)))
    k_dorm = int(round(C * float(w.dormant_customer_share)))
    cust_kind = np.zeros(C, dtype=np.int8)
    cust_kind[perm[:k_fam]] = FAMILY_HUB
    cust_kind[perm[k_fam : k_fam + k_new]] = NEW_CUSTOMER
    cust_kind[perm[k_fam + k_new : k_fam + k_new + k_dorm]] = DORMANT_CUSTOMER
    for i in range(C):
        archetype[i] = CUSTOMER_KIND_NAMES[int(cust_kind[i])]

    active_start = np.zeros(C)
    new_idx = np.flatnonzero(cust_kind == NEW_CUSTOMER)
    hi = max(1.5, days - 3.0)
    active_start[new_idx] = rng.uniform(1.0, hi, size=new_idx.size) * DAY_S
    dormant_from = np.full(C, np.inf)
    dormant_to = np.full(C, np.inf)
    dorm_idx = np.flatnonzero(cust_kind == DORMANT_CUSTOMER)
    if days >= 16:
        d0 = rng.uniform(0.0, days - 16.0, size=dorm_idx.size)
        length = rng.uniform(14.0, 20.0, size=dorm_idx.size)
        length = np.minimum(length, days - 1.0 - d0)
        dormant_from[dorm_idx] = d0 * DAY_S
        dormant_to[dorm_idx] = (d0 + length) * DAY_S
    else:  # short worlds: dormant before the window, first seen near the end
        dormant_from[dorm_idx] = 0.0
        dormant_to[dorm_idx] = (
            rng.uniform(max(0.5, days - 2.0), max(1.0, days - 0.5), size=dorm_idx.size) * DAY_S
        )

    tracked = np.ones(n, dtype=bool)
    for name in ("merchant", "agent"):
        a, b = ranges[name]
        tracked[a:b] = False
    balance0 = lognormal(rng, 5000.0, 1.0 * sig, n)
    balance0[~tracked] = 0.0

    # contacts: 3 + Geometric(0.3) (failures before success) per customer
    counts = rng.geometric(0.3, size=C) + 2
    off = np.zeros(C + 1, dtype=np.int64)
    np.cumsum(counts, out=off[1:])
    owner = np.repeat(np.arange(C), counts)
    cidx = rng.integers(0, C, size=int(off[-1]))
    same = cidx == owner
    cidx[same] = (cidx[same] + 1) % C

    a_start, a_end = ranges["agent"]
    n_agents = a_end - a_start
    agent_p = zipf_probs(n_agents, 0.9)[rng.permutation(n_agents)]
    home_agent = a_start + rng.choice(n_agents, size=C, p=agent_p)

    m_start, m_end = ranges["merchant"]
    n_m = m_end - m_start
    merchant_p = zipf_probs(n_m, 1.1)[rng.permutation(n_m)]
    merchant_owner = rng.integers(0, C, size=n_m)
    n_billers = max(3, int(round(0.05 * n_m)))
    billers = m_start + rng.choice(n_m, size=n_billers, replace=False)
    fav_merchant = m_start + rng.choice(n_m, size=C, p=merchant_p)

    regular_pool = np.flatnonzero(cust_kind == REGULAR)

    # business clients of traders: a slice of ordinary customers with larger balances
    n_tr = ranges["trader"][1] - ranges["trader"][0]
    n_bc = min(regular_pool.size // 4, max(10, n_tr * 25))
    business_clients = rng.choice(regular_pool, size=n_bc, replace=False)
    business_clients.sort()
    balance0[business_clients] = lognormal(rng, 60000.0, 0.8 * sig, n_bc)

    pop = Population(
        n=n,
        ids=ids,
        archetype=archetype,
        tracked=tracked,
        balance0=balance0,
        ranges=ranges,
        cust_kind=cust_kind,
        active_start=active_start,
        dormant_from=dormant_from,
        dormant_to=dormant_to,
        contacts_off=off,
        contacts_idx=cidx,
        agent_p=agent_p,
        home_agent=home_agent,
        merchant_p=merchant_p,
        merchant_owner=merchant_owner,
        billers=billers,
        fav_merchant=fav_merchant,
        regular_pool=regular_pool,
        business_clients=business_clients,
    )

    # salary payers: 50 to 400 employees each; bursts on day-of-month 1 (allowances) and the last day
    # (salary).
    # Half of the payers also run weekly payouts to a subset of payees (gig-platform style) so that payroll
    # fan-out appears inside the training window of a 30-day world (decision logged in docs/decisions.md).
    s_start, s_end = ranges["salary_payer"]
    for p in range(s_start, s_end):
        k = int(rng.integers(50, 401))
        k = min(k, C)
        emp = rng.choice(C, size=k, replace=False)
        amounts = np.clip(lognormal(rng, 18000.0, 0.5 * sig, k), 5000.0, float(w.upi_txn_cap_inr))
        weekly = None
        if rng.random() < 0.5:
            share = rng.uniform(0.3, 0.7)
            sub = emp[rng.random(k) < share]
            weekly = (
                int(rng.integers(0, 7)),
                sub,
                np.clip(
                    amounts[: sub.size] / 4.0 * rng.uniform(0.5, 1.5, sub.size),
                    200.0,
                    float(w.upi_txn_cap_inr),
                ),
            )
        pop.salary.append((p, emp, amounts, weekly))
        balance0[p] = float(amounts.sum()) * 6.0

    # landlords: 3 to 10 tenants paying rent within days 1 to 5 of the month
    l_start, l_end = ranges["landlord"]
    for ll in range(l_start, l_end):
        k = int(rng.integers(3, 11))
        tenants = rng.choice(C, size=k, replace=False)
        rents = np.clip(lognormal(rng, 12000.0, 0.4 * sig, k), 2000.0, float(w.upi_txn_cap_inr))
        rent_days = rng.integers(0, 5, size=k)
        pop.landlords.append((ll, tenants, rents, rent_days))

    # traders: business-hours in and out, stable clients and suppliers
    t_start, t_end = ranges["trader"]
    for t in range(t_start, t_end):
        n_cl = int(rng.integers(20, 61))
        clients = rng.choice(business_clients, size=min(n_cl, business_clients.size), replace=False)
        n_sup = int(rng.integers(5, 16))
        sup = []
        for _ in range(n_sup):
            if n_tr > 1 and rng.random() < 0.3:
                other = t_start + int(rng.integers(0, n_tr))
                sup.append(other if other != t else int(rng.integers(0, C)))
            else:
                sup.append(int(rng.integers(0, C)))
        pass_through = float(rng.uniform(0.6, 0.95))
        rate = float(lognormal(rng, 4.0, 0.6)) * boost
        pop.traders.append((t, clients, np.array(sup), pass_through, rate))
        balance0[t] = float(lognormal(rng, 50000.0, 0.7 * sig))

    # family hubs: 3 to 6 relatives, 1 to 3 stable payees
    for hub in np.flatnonzero(cust_kind == FAMILY_HUB):
        rel = rng.choice(C, size=int(rng.integers(3, 7)), replace=False)
        rel = rel[rel != hub]
        pay = rng.choice(C, size=int(rng.integers(1, 4)), replace=False)
        pay = pay[pay != hub]
        if rel.size and pay.size:
            pop.families.append((int(hub), rel, pay))

    pop.is_mule = np.zeros(n, dtype=np.int8)
    pop.is_unwitting = np.zeros(n, dtype=np.int8)
    pop.first_ring = np.full(n, -1, dtype=np.int64)
    pop.used = np.ones(n, dtype=bool)
    mp_a, mp_b = ranges["mule_pool"]
    pop.used[mp_a:mp_b] = False  # pool accounts only exist once a campaign uses them
    balance0[mp_a:mp_b] = 0.0
    return pop
