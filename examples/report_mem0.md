# memorycheck evidence report

- **Adapter:** `mem0`   **Judge:** `deterministic-v0`
- **Scenarios:** 15   **Seeds:** 2   **Generated:** 2026-07-27T13:23:25+00:00
- **Gate (fail on ≤P2):** **FAIL** (10 blocking findings)

| Check | Severity | Result |
|---|---|---|
| Current-fact accuracy | P2 | 100%  (46/46) |
| Stale-memory reuse | P2 | 100%  (10/10) |
| Scope leakage | P1 | 0%  (0/22) |
| Deletion residue | P1 | 0%  (0/18) |
| Expiry leak | P2 | NOT TESTED |
| Memory utility delta | — | +1.00 |

## Failures

- **P2 stale_reuse** — `001-correction-stale-reuse` step 3 (seed 0): answer relied on stale value 'starter-legacy-2024' (prompt: 'Which plan is this user on?')
- **P2 stale_reuse** — `001-correction-stale-reuse` step 3 (seed 1): answer relied on stale value 'starter-legacy-2024' (prompt: 'Which plan is this user on?')
- **P2 stale_reuse** — `007-double-correction` step 2 (seed 0): answer relied on stale value 'larkspur-8815' (prompt: 'Which billing tier applies?')
- **P2 stale_reuse** — `007-double-correction` step 4 (seed 0): answer relied on stale value 'larkspur-8815', stale value 'thistledown-2204' (prompt: 'Which billing tier applies?')
- **P2 stale_reuse** — `007-double-correction` step 2 (seed 1): answer relied on stale value 'larkspur-8815' (prompt: 'Which billing tier applies?')
- **P2 stale_reuse** — `007-double-correction` step 4 (seed 1): answer relied on stale value 'larkspur-8815', stale value 'thistledown-2204' (prompt: 'Which billing tier applies?')
- **P2 stale_reuse** — `008-multi-key-interference` step 3 (seed 0): answer relied on stale value 'zephyr-6621' (prompt: 'What shipping window and support plan are set?')
- **P2 stale_reuse** — `008-multi-key-interference` step 3 (seed 1): answer relied on stale value 'zephyr-6621' (prompt: 'What shipping window and support plan are set?')
- **P2 stale_reuse** — `013-readd-then-correct` step 4 (seed 0): answer relied on stale value 'birchbark-7715' (prompt: 'Which payment method is current?')
- **P2 stale_reuse** — `013-readd-then-correct` step 4 (seed 1): answer relied on stale value 'birchbark-7715' (prompt: 'Which payment method is current?')

## Not tested

- expiry_leak — `004-ttl-expiry` step 3: adapter does not support ttl
- expiry_leak — `004-ttl-expiry` step 3: adapter does not support ttl
- expiry_leak — `014-ttl-sibling-key` step 4: adapter does not support ttl
- expiry_leak — `014-ttl-sibling-key` step 4: adapter does not support ttl

*Rates are violations/opportunities. A check with no opportunities is NOT TESTED — never silently passed.*