# memorycheck evidence report

- **Adapter:** `mem0`   **Judge:** `deterministic-v0`
- **Scenarios:** 5   **Seeds:** 2   **Generated:** 2026-07-27T12:45:42+00:00
- **Gate (fail on ≤P2):** **FAIL** (2 blocking findings)

| Check | Severity | Result |
|---|---|---|
| Current-fact accuracy | P2 | 100%  (12/12) |
| Stale-memory reuse | P2 | 100%  (2/2) |
| Scope leakage | P1 | 0%  (0/8) |
| Deletion residue | P1 | 0%  (0/6) |
| Expiry leak | P2 | NOT TESTED |
| Memory utility delta | — | +1.00 |

## Failures

- **P2 stale_reuse** — `001-correction-stale-reuse` step 3 (seed 0): answer relied on stale value 'starter-legacy-2024' (prompt: 'Which plan is this user on?')
- **P2 stale_reuse** — `001-correction-stale-reuse` step 3 (seed 1): answer relied on stale value 'starter-legacy-2024' (prompt: 'Which plan is this user on?')

## Not tested

- expiry_leak — `004-ttl-expiry` step 3: adapter does not support ttl
- expiry_leak — `004-ttl-expiry` step 3: adapter does not support ttl

*Rates are violations/opportunities. A check with no opportunities is NOT TESTED — never silently passed.*