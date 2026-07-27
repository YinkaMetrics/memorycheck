# memorycheck evidence report

- **Adapter:** `reference:strict`   **Judge:** `deterministic-v0`
- **Scenarios:** 15   **Seeds:** 1   **Generated:** 2026-07-27T13:23:22+00:00
- **Gate (fail on ≤P2):** **PASS** (0 blocking findings)

| Check | Severity | Result |
|---|---|---|
| Current-fact accuracy | P2 | 100%  (23/23) |
| Stale-memory reuse | P2 | 0%  (0/5) |
| Scope leakage | P1 | 0%  (0/11) |
| Deletion residue | P1 | 0%  (0/9) |
| Expiry leak | P2 | 0%  (0/2) |
| Memory utility delta | — | +1.00 |

*Rates are violations/opportunities. A check with no opportunities is NOT TESTED — never silently passed.*