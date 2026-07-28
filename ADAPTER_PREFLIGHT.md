# Adapter preflight

**Run this before any scenario executes against a new provider.** Not after a
first run, not "if something looks odd" — before. Each item below exists
because skipping it already produced a false result or a wasted run.

The purpose is not to check the provider works. It is to find out where the
provider and this harness disagree about reality, while that disagreement is
still cheap. Every item is answered by **observation against the live service**,
never by reading documentation.

Record answers in `HANDOFF.md` before the first scenario. An adapter keeps
`unverified = True` until every item has an observed answer.

---

## 1. Quota and rate model — from live response headers

Read the headers on a real request. Vendor pricing pages have been wrong or
incomplete every time so far.

- [ ] Which operations are metered, and are read and write metered separately?
- [ ] What are the limits and the reset period?
- [ ] What does one full 15 × 2 run cost against each counter?
- [ ] What happens at the limit — hard error, degraded response, or silence?
- [ ] What is the request rate limit, and does confirmation polling threaten it?

*Why:* Mem0 meters `SEARCH` (1,000/period) separately from `ADD`
(10,000/period). A full run costs ~106 SEARCH — about a tenth of the period —
and confirmation reads are most of it. Zep meters ingestion credits instead, so
the same polling is free but a request-rate ceiling applies. Two providers,
opposite constraints; neither was guessable.

## 2. Write confirmation semantics

- [ ] After a successful write, what read proves the value is retrievable?
- [ ] Is that read the same layer a query uses? *(If a query reads a derived
      layer, confirming the raw layer proves nothing.)*
- [ ] Does the write API return an id that can be polled directly?
- [ ] Do list endpoints hide records in an intermediate state?

*Why:* Zep queries read extracted **edges**, while writes land as **episodes**;
confirming the episode would leave every query racing the extractor. And
`episode.get_by_graph_id` does not list unprocessed episodes, which made a
successful write look like a lost one for an entire investigation.

## 3. Extraction or indexing latency

- [ ] Measure write → retrievable, several times, not once.
- [ ] Is it stable, or does it vary by orders of magnitude?
- [ ] Set timeouts from the measurements, with headroom — never by analogy to
      another provider.
- [ ] Estimate total wall clock for a full run, and check it is acceptable
      before committing to it.

*Why:* Mem0 is sub-second for verbatim writes and ~15s through its extraction
pipeline. Zep ranged from under 30s to never. `_EXTRACTION_TIMEOUT` was first
set to 120s by analogy to Mem0 — wrong by roughly 3x, and every write would
have aborted. Latency was then re-characterised twice more on further evidence.
One measurement is not a measurement.

## 4. Silent discard

- [ ] Does every accepted write become retrievable, or can one vanish?
- [ ] Does the outcome depend on content?
- [ ] Is there any signal distinguishing stored from discarded?
- [ ] Probe with the **actual scenario values**, not values invented for the
      probe.

*Why:* On Zep a write can return success, reach `processed=True`, and produce
nothing retrievable, with no signal separating that from a stored fact. Some of
the pack's values do this and some do not. Had the full pack run first, those
scenarios would have reported `missing_current_fact` and been read as a
catastrophic provider result. This is the single most dangerous item on the
list, because it fails in the direction of a false accusation.

## 5. Reset and pagination

- [ ] Does `reset()` enumerate **everything** it must clear, across pagination?
- [ ] How many objects does one full run create, and when does that cross a
      page boundary?
- [ ] Are deletes synchronous, or can an in-flight delete reap a later write?
- [ ] Does reset leave residue that a later run would attribute to the
      provider?

*Why:* Mem0 applies `delete_all` asynchronously — measured at 6/14 writes lost
when a delete preceded them — which produced phantom `missing_current_fact`
failures blaming the provider for facts the harness had deleted itself. Zep
paginates graph listing at 100 rows while one run creates ~45 graphs, so
`reset()` would have started missing graphs within a few runs and scored the
residue as leakage.

---

## Standing rules

- **Assume eventual consistency until measured otherwise** (invariant 10).
- **Confirm your own writes and deletes; never poll a query until an expected
  value appears** — that launders a real failure into a pass.
- **A false FAIL costs the gate as much as a false PASS.** When a result looks
  dramatic, suspect the instrument first. Every dramatic early result in this
  project so far has been ours, not the provider's.
- **Record what you could not test.** An untested assumption is not a passed
  one; say so in `HANDOFF.md` and keep `unverified = True`.
