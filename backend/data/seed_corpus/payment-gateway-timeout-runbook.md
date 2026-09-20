---
title: Payment Gateway Timeout Runbook
type: runbook
owner: Payments Team
version: 1.6
---

# Payment Gateway Timeout Runbook

## Symptom

`payments-gateway` returns 502s with log lines matching `ReadTimeout: The
read operation timed out` from `PaymentProcessorClient.charge`. Charge
requests take the full configured timeout (10 seconds) before failing.

## Likely causes, in order of frequency

1. **A recent deploy to `payments-gateway` changed how it calls the upstream
   processor** (a new retry policy, a changed timeout value, a new request
   shape the processor handles slower). Check whether a deploy occurred in
   the last 2 hours before assuming the upstream provider itself is at
   fault - this is the more common case in practice, even though it feels
   counterintuitive to blame your own deploy for a third-party timeout.
2. **The upstream payment processor is degraded.** Check the processor's own
   public status page before escalating internally; if the processor
   confirms a degradation, no internal fix will resolve this and the
   correct action is to enable the configured fallback processor, not to
   restart or roll back `payments-gateway`.
3. **Network-level packet loss between our infrastructure and the
   processor's endpoint**, distinguishable from cause 2 because it degrades
   gradually rather than affecting a fixed percentage of requests uniformly.

## Immediate mitigation

For cause 1: **roll back `payments-gateway` to the version deployed
immediately before the timeouts began.** A restart does not help here, since
the problem is in the deployed code, not a stuck process - restarting will
bring the same faulty version back up. Only roll back to a version you can
positively identify as the last known-good one; rolling back to an arbitrary
older version can reintroduce a different, already-fixed issue.

For cause 2: do not roll back or restart `payments-gateway` - the code is
not at fault. Enable the fallback payment processor via the payments
feature-flag console and notify the Payments Team channel.

For cause 3: escalate to Network Engineering; this is not resolved by any
action available to the on-call engineer for this service.

## Verification after mitigation

After a rollback, confirm the charge success rate returns above 98% within 5
minutes and p99 latency drops below 2 seconds. If timeouts persist
identically after rollback, the deploy was not the cause - re-check the
processor's status page for cause 2 before taking further action.

## Related

See the Deployment Rollback Policy for the version-identification and
approval requirements before a rollback, and the Capacity and Autoscaling
Runbook if timeouts correlate with `payments-gateway`'s own request volume
rather than upstream behaviour.
