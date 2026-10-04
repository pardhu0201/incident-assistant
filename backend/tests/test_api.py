"""HTTP surface, including the full log-ingestion -> incident -> approval
round trip."""

from __future__ import annotations

from app.db.models import ServiceState


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["documents"] >= 5
    assert body["chunks"] > 20
    assert body["llm_mode"] == "demo"


def test_graph_and_tools_and_services_are_exposed(client):
    topology = client.get("/api/graph").json()
    assert len(topology["nodes"]) == 7

    tools = client.get("/api/tools").json()
    names = {t["name"] for t in tools}
    assert {"restart_service", "rollback_deployment", "scale_service"} <= names

    services = client.get("/api/services").json()
    assert len(services) >= 3
    assert all(s["replica_count"] > 0 for s in services)


def test_log_ingest_creates_an_incident(client):
    response = client.post(
        "/api/logs/ingest",
        json={
            "records": [
                {
                    "timestamp": "2026-02-01T10:00:00Z",
                    "service": "api-test-service",
                    "level": "ERROR",
                    "message": "Database connection pool exhausted after 29500ms",
                    "stack_trace": "PoolTimeout: x",
                },
                {
                    "timestamp": "2026-02-01T10:00:30Z",
                    "service": "api-test-service",
                    "level": "ERROR",
                    "message": "Database connection pool exhausted after 28900ms",
                    "stack_trace": "PoolTimeout: x",
                },
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["events_ingested"] == 2
    assert body["incidents_opened"] == 1
    assert len(body["incident_ids"]) == 1


def test_incident_list_and_detail(client):
    incidents = client.get("/api/incidents").json()
    assert incidents
    incident_id = incidents[0]["id"]
    detail = client.get(f"/api/incidents/{incident_id}").json()
    assert detail["id"] == incident_id
    assert "runs" in detail


def test_analyze_and_approve_round_trip(client, db):
    if db.get(ServiceState, "api-test-service-2") is None:
        db.add(ServiceState(name="api-test-service-2"))
        db.commit()

    ingest = client.post(
        "/api/logs/ingest",
        json={
            "records": [
                {
                    "timestamp": "2026-02-02T10:00:00Z",
                    "service": "api-test-service-2",
                    "level": "ERROR",
                    "message": "Database connection pool exhausted after 29500ms",
                    "stack_trace": "psycopg.pool.PoolTimeout: connection pool exhausted, timeout waiting 30000ms",
                }
                for _ in range(3)
            ]
        },
    ).json()
    incident_id = ingest["incident_ids"][0]

    run = client.post(f"/api/incidents/{incident_id}/analyze").json()
    assert run["citations"]
    assert run["proposed_fix"]["tool_name"] == "restart_service"
    assert run["test_gate"]["passed"] is True
    assert run["requires_approval"] is True
    approval_id = run["approval_id"]
    assert approval_id

    pending = client.get("/api/approvals?status=pending").json()
    assert any(a["id"] == approval_id for a in pending)

    decision = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "decided_by": "sre@example.com"},
    )
    assert decision.status_code == 200
    body = decision.json()
    assert body["status"] == "approved"
    assert body["execution_result"]["action"] == "restart_service"

    again = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approve"})
    assert again.status_code == 409

    audit = client.get("/api/audit").json()
    assert any(e["action"] == "incident.fix_applied" for e in audit)

    incident = client.get(f"/api/incidents/{incident_id}").json()
    assert incident["status"] == "resolved"


def test_rejection_does_not_execute(client, db):
    if db.get(ServiceState, "api-test-service-3") is None:
        db.add(ServiceState(name="api-test-service-3"))
        db.commit()

    ingest = client.post(
        "/api/logs/ingest",
        json={
            "records": [
                {
                    "timestamp": "2026-02-03T10:00:00Z",
                    "service": "api-test-service-3",
                    "level": "ERROR",
                    "message": "Database connection pool exhausted after 29500ms",
                    "stack_trace": "PoolTimeout: x",
                }
            ]
        },
    ).json()
    run = client.post(f"/api/incidents/{ingest['incident_ids'][0]}/analyze").json()
    assert run["approval_id"]

    body = client.post(
        f"/api/approvals/{run['approval_id']}/decision",
        json={"decision": "reject", "note": "Investigating manually first"},
    ).json()
    assert body["status"] == "rejected"
    assert body["execution_result"] is None


def test_dashboard_reflects_runs(client):
    body = client.get("/api/dashboard").json()
    assert body["runs_total"] > 0
    assert isinstance(body["flag_counts"], dict)
    assert 0.0 <= body["test_gate_pass_rate"] <= 1.0
    assert isinstance(body["span_summary"], list)


def test_decision_validation_and_admin_token(client, monkeypatch):
    from app.config import settings

    url = "/api/approvals/missing/decision"
    assert client.post(url, json={"decision": "approve"}).status_code == 404
    assert client.post(url, json={"decision": "maybe"}).status_code == 422

    monkeypatch.setattr(settings, "admin_token", "s3cret")
    assert client.post(url, json={"decision": "approve"}).status_code == 401
    good = client.post(url, json={"decision": "approve"}, headers={"X-Admin-Token": "s3cret"})
    assert good.status_code == 404
    # Proposing stays open - only executing a fix is guarded.
    assert client.post("/api/logs/replay-sample").status_code == 200
