-- scripts/dashboard_query.sql
--
-- M6 dashboard: per-stage telemetry.
--
-- Usage:
--   sudo docker exec temporal-postgresql psql -U temporal -d content_engine \
--     -v run_id="'m4-dryrun-20260828-124412'" -f scripts/dashboard_query.sql

\echo ''
\echo '  AVATAR HARNESS — Stage Telemetry Dashboard'
\echo ''

\echo '── Run Summary ──'
SELECT
    :run_id AS run_id,
    COUNT(*) AS total_stages,
    COUNT(*) FILTER (WHERE status = 'passed') AS passed,
    COUNT(*) FILTER (WHERE status != 'passed') AS failed,
    MAX(attempt) AS max_attempt,
    MIN(started_at) AS run_started,
    MAX(completed_at) AS run_completed
FROM manifest_stage_records
WHERE run_id = :run_id;

\echo ''
\echo '── Per-Stage Detail ──'
SELECT
    m.stage_id,
    m.status,
    m.attempt,
    t.provider_name,
    t.provider_model,
    t.provider_version,
    t.provider_cost,
    t.provider_latency_ms
FROM manifest_stage_records m
LEFT JOIN stage_run_records t
    ON m.run_id = t.run_id AND m.stage_id = t.stage_id AND m.attempt = t.attempt
WHERE m.run_id = :run_id
ORDER BY m.manifest_created_at;

\echo ''
\echo '── Cost Summary ──'
SELECT
    COALESCE(SUM(provider_cost), 0) AS total_cost,
    COALESCE(SUM(provider_latency_ms), 0) AS total_latency_ms,
    ROUND(COALESCE(AVG(provider_latency_ms), 0)) AS avg_latency_ms,
    MAX(provider_latency_ms) AS max_latency_ms,
    COUNT(DISTINCT provider_name) AS distinct_providers
FROM stage_run_records
WHERE run_id = :run_id;

\echo ''
\echo '── Provider Comparison (all runs) ──'
SELECT
    stage_id,
    provider_name,
    COUNT(*) AS run_count,
    ROUND(AVG(provider_latency_ms)) AS avg_latency_ms,
    ROUND(CAST(AVG(provider_cost) AS numeric), 4) AS avg_cost
FROM stage_run_records
WHERE stage_id IN ('S30', 'S40', 'G90')
GROUP BY stage_id, provider_name
ORDER BY stage_id, provider_name;