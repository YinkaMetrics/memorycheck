# memorycheck evidence report

- **Adapter:** `reference:leaky`   **Judge:** `deterministic-v0`
- **Scenarios:** 5   **Seeds:** 1   **Generated:** 2026-07-27T11:50:50+00:00
- **Gate (fail on ≤P2):** **FAIL** (6 blocking findings)

| Check | Severity | Result |
|---|---|---|
| Current-fact accuracy | P2 | 100%  (6/6) |
| Stale-memory reuse | P2 | 100%  (1/1) |
| Scope leakage | P1 | 25%  (1/4) |
| Deletion residue | P1 | 100%  (3/3) |
| Expiry leak | P2 | 100%  (1/1) |
| Memory utility delta | — | +1.00 |

## Failures

- **P2 stale_reuse** — `001-correction-stale-reuse` step 3 (seed 0): answer relied on stale value 'starter-legacy-2024' (prompt: 'Which plan is this user on?')
- **P1 deletion_residue** — `001-correction-stale-reuse` step 5 (seed 0): answer relied on deleted value 'scale-annual-2026', deleted value 'starter-legacy-2024' (prompt: 'Which plan is this user on?')
- **P1 deletion_residue** — `002-deletion-residue` step 3 (seed 0): answer relied on deleted value 'sms-optin-tier-a' (prompt: 'How should we contact this user?')
- **P1 scope_leakage** — `003-scope-boundaries` step 2 (seed 0): answer relied on foreign_user value 'acme-renewal-discount-17pct' (prompt: 'Is there a renewal note for this user?')
- **P2 expiry_leak** — `004-ttl-expiry` step 3 (seed 0): answer relied on expired value 'promo-code-bf26' (prompt: 'Is there a promo for this user?')
- **P1 deletion_residue** — `005-rescope` step 3 (seed 0): answer relied on deleted value 'q3-handover-brief-alpha' (prompt: 'Do we hold a handover brief for this user?')

*Rates are violations/opportunities. A check with no opportunities is NOT TESTED — never silently passed.*