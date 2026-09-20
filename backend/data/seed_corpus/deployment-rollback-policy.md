---
title: Deployment Rollback Policy
type: policy
owner: Platform Engineering
version: 2.0
---

# Deployment Rollback Policy

## When a rollback is the correct response

Roll back when an incident began within 2 hours of a deploy to the affected
service, and the symptom is consistent with a code or configuration change
(new error type, changed latency profile, a code path that did not exist
before) rather than an external dependency or a capacity issue. If the
symptom is a resource exhaustion pattern identical to one seen before a
deploy history exists, prefer a restart or scale action per the relevant
runbook instead - a rollback that does not address the actual cause delays
recovery and adds unnecessary deployment churn.

## Version selection

Only roll back to a version that was itself stable in production - normally
the immediately preceding deployed version, recorded as the service's "last
known good" version. Rolling back further than one version without
confirming that version's own history is not itself a stability regression
requires sign-off from the service owner, since older versions may lack
fixes for issues resolved since.

## Required checks before rolling back

1. Confirm the timing correlation: the deploy timestamp must precede the
   first incident event, not follow it.
2. Confirm no other service-owned change (a feature flag flip, a
   configuration update, a dependency version bump) better explains the
   symptom - a rollback will not fix an issue actually caused by a
   configuration change made independently of the code deploy.
3. Confirm the target version is the recorded last-known-good version for
   that service, not a guess.

## After a rollback

A rollback is a mitigation, not a resolution. The change that caused the
incident must still be identified, fixed, and re-deployed through the normal
release process - do not consider the incident closed until a root-cause
fix has been merged or explicitly deferred with owner sign-off.

## Related

See the Payment Gateway Timeout Runbook and the Database Connection Pool
Runbook for the specific incident types where a rollback is one of the
listed likely mitigations.
