# memorycheck evidence report

- **Adapter:** `langgraph:memory`   **Judge:** `deterministic-v0`
- **Scenarios:** 15   **Seeds:** 2   **Generated:** 2026-07-28T02:04:16+00:00
- **Gate (fail on ≤P2):** **PASS** (0 blocking findings)

| Check | Severity | Result |
|---|---|---|
| Current-fact accuracy | P2 | 100%  (46/46) |
| Stale-memory reuse | P2 | 0%  (0/10) |
| Scope leakage | P1 | 0%  (0/22) |
| Deletion residue | P1 | 0%  (0/18) |
| Expiry leak | P2 | NOT TESTED |
| Memory utility delta | — | +1.00 |

## Not tested

- expiry_leak — `004-ttl-expiry` step 3: adapter does not support ttl
- expiry_leak — `004-ttl-expiry` step 3: adapter does not support ttl
- expiry_leak — `014-ttl-sibling-key` step 4: adapter does not support ttl
- expiry_leak — `014-ttl-sibling-key` step 4: adapter does not support ttl

*Rates are violations/opportunities. A check with no opportunities is NOT TESTED — never silently passed.*