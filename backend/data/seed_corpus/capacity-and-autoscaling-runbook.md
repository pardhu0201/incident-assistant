---
title: Capacity and Autoscaling Runbook
type: runbook
owner: Platform Engineering
version: 1.9
---

# Capacity and Autoscaling Runbook

## Symptom

A service returns 429s with log lines matching `RateLimitExceeded: queue
depth exceeded configured threshold`, typically preceded by a `WARN`-level
"Traffic spike detected" log line comparing current request rate to the
trailing 7-day average.

## Likely causes, in order of frequency

1. **Genuine traffic growth exceeding current replica count** - a marketing
   campaign, a viral moment, or organic growth outpacing the last capacity
   review. This is the common case when the traffic-spike ratio (current
   rate vs. 7-day average) is between 2x and 6x and sustained rather than a
   single-minute burst.
2. **A retry storm** - a downstream failure is causing callers (internal
   services or client SDKs) to retry aggressively, multiplying real user
   traffic by the retry factor. Distinguishable from cause 1 by checking
   whether a downstream dependency was also erroring in the same window; if
   so, the traffic increase is retry-amplified, not organic.
3. **A misconfigured or malicious client** sending requests far above any
   plausible legitimate rate (typically >10x baseline from a single source).
   This is not a capacity problem and scaling will not help - it needs
   blocking at the edge/WAF layer instead.

## Immediate mitigation

For cause 1: **scale the affected service up.** As a starting point, double
the current replica count; this restores headroom quickly while a more
precise capacity calculation is done. Scaling beyond roughly 3-4x current
replica count in a single step should be treated with caution and confirmed
against downstream capacity (database connections, cache cluster) before
proceeding, since the bottleneck may not be the scaled service itself.

For cause 2: scaling the affected service provides temporary relief but does
not address the root cause - the downstream failure driving the retry storm
must also be identified and mitigated, or the retry volume will continue
once scaled capacity is itself exceeded again.

For cause 3: do not scale. Escalate to Security/Network Engineering for
edge-level blocking; adding replicas to absorb malicious traffic is not an
appropriate use of capacity.

## Verification after mitigation

After scaling, confirm the 429 rate returns to zero within 5 minutes and
queue depth returns below the configured threshold. If 429s persist after
scaling, re-check whether the bottleneck is actually downstream (database,
cache, a shared rate limiter) rather than the scaled service's own capacity.

## Related

See the Database Connection Pool Runbook if scaling a service increases
database connection usage beyond the pool's capacity, and the Payment
Gateway Timeout Runbook for a case where the traffic pattern looks similar
but the root cause is a downstream dependency, not client-side load.
