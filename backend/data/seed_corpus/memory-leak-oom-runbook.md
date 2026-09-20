---
title: Memory Leak and OOM Runbook
type: runbook
owner: Platform Engineering
version: 1.4
---

# Memory Leak and OOM Runbook

## Symptom

A service (most often `inventory-service`) logs a climbing sequence of "Heap
usage at NMB / limit" warnings over 10-20 minutes, followed by a JVM
`OutOfMemoryError: Java heap space` and a process restart from the
supervisor. Heap usage resets to baseline after the forced restart, then
begins climbing again.

## Likely causes, in order of frequency

1. **An unbounded in-memory cache** (a `HashMap`/similar structure that is
   written to on every request but never evicted or size-capped) is the most
   common cause for `inventory-service` specifically - its reservation cache
   has no eviction policy in the current version. This produces the
   characteristic slow, steady climb this runbook describes, distinct from a
   sudden spike.
2. **A traffic spike temporarily exceeding normal memory headroom**,
   distinguishable from cause 1 because heap usage returns to a stable
   baseline once traffic subsides, rather than continuing to climb.
3. **A genuinely undersized heap configuration** for current baseline
   traffic - distinguishable from cause 1 because heap climbs immediately
   after every restart at a consistent rate proportional to request volume,
   never plateauing even briefly.

## Immediate mitigation

For cause 1 (the common case for `inventory-service`): **restart the
service.** This is a mitigation, not a fix - the leak will recur, typically
within 1-3 hours depending on traffic - but a restart is safe to perform
immediately and buys time. The actual fix requires a code change to add
cache eviction and must be tracked as a follow-up, not treated as resolved
by the restart alone.

For cause 2: restart if currently OOMing, but do not treat this as urgent
follow-up work the way cause 1 requires - once the traffic spike passes,
recurrence is unlikely without a similar spike.

For cause 3: a restart provides only brief relief. The heap limit
configuration itself must be increased and the service redeployed;
repeated restarts without a config change will not resolve this.

## Verification after mitigation

After a restart, monitor heap usage for the following 20 minutes. A slow,
steady climb resuming immediately confirms cause 1 (the unbounded cache) and
should be filed as a follow-up bug against the reservation cache
implementation, referencing this incident. A flat baseline confirms cause 2
or an unrelated transient issue.

## Related

See the Database Connection Pool Runbook for a similar "restart provides
temporary relief, underlying cause needs a code fix" pattern in a different
subsystem.
