---
title: Database Connection Pool Runbook
type: runbook
owner: Platform Engineering
version: 2.1
---

# Database Connection Pool Runbook

## Symptom

`checkout-api` (and any service sharing the primary Postgres connection pool)
begins returning 503s with log lines matching `PoolTimeout: connection pool
exhausted, timeout waiting 30000ms`. Request latency for affected endpoints
climbs to the full 30-second pool acquisition timeout before failing.

## Likely causes, in order of frequency

1. **A slow or hung query is holding connections open.** Check
   `pg_stat_activity` for queries running longer than 60 seconds. This is the
   most common cause and does not require a deploy or config change to fix -
   killing the offending query and restarting the affected service pods
   clears it in the large majority of cases.
2. **A recent deploy increased per-request connection usage** (e.g. a new
   code path opening a connection without releasing it in an error branch).
   Check the deploy history for the affected service in the last 2 hours.
3. **The pool size itself is undersized for current traffic.** This shows up
   as gradual, traffic-correlated exhaustion rather than a sudden spike, and
   is the only cause on this list that requires a config change rather than
   a restart.

## Immediate mitigation

For cause 1 or 2 (the common cases): **restart the affected service.** This
releases any connections a hung request is holding and clears the pool
within one restart cycle. Do not restart more than once within 5 minutes -
if a restart does not resolve the exhaustion, the root cause is not a stuck
connection and a second restart will not help; escalate to Platform
Engineering instead.

For cause 3: do not restart. Increase the pool size via the service's
`DB_POOL_MAX_CONNECTIONS` configuration and redeploy; a restart alone will
recreate the same undersized pool.

## How to tell which cause you're facing

Check `pg_stat_activity` first, before restarting anything:

- If there are one or more queries running longer than 60 seconds against
  tables the affected service owns, this is cause 1. Restart is the correct
  fix.
- If there is no single long-running query but active connection count is
  consistently near the configured pool maximum across many short-lived
  queries, this is cause 3. Do not restart; escalate for a pool-size config
  change.
- If the exhaustion began within 30 minutes of a deploy, treat it as cause 2
  until proven otherwise: a restart may provide temporary relief, but the
  underlying code path must be fixed and redeployed, or the same exhaustion
  will recur within hours.

## Verification after mitigation

After a restart, confirm active connection count has dropped below 70% of
the pool maximum within 2 minutes, and that the error rate for the affected
endpoints has returned to baseline. If exhaustion recurs within 15 minutes of
the restart, do not restart again - escalate immediately, since this
indicates cause 2 or 3 rather than a one-off stuck connection.

## Related

See the Capacity and Autoscaling Runbook if the pool exhaustion correlates
with an overall traffic spike rather than a specific slow query, and the
Deployment Rollback Policy if a recent deploy is implicated.
