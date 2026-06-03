-- ============================================================
-- MIGRATION 013: processing_jobs worker 原子认领字段与函数
-- ============================================================

ALTER TABLE processing_jobs
    ADD COLUMN IF NOT EXISTS locked_by TEXT,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_processing_jobs_worker_claim
    ON processing_jobs(status, locked_at, created_at);

CREATE OR REPLACE FUNCTION claim_next_processing_job(
    p_worker_id TEXT,
    p_stale_after_seconds INT DEFAULT 1800
)
RETURNS SETOF processing_jobs AS $$
BEGIN
    RETURN QUERY
    WITH next_job AS (
        SELECT id
        FROM processing_jobs
        WHERE (
              status = 'queued'
              OR (
                  status = 'processing'
                  AND locked_at < NOW() - make_interval(secs => p_stale_after_seconds)
              )
          )
        ORDER BY created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE processing_jobs AS job
    SET
        status = 'processing',
        stage = 'pending',
        progress = 5,
        locked_by = p_worker_id,
        locked_at = NOW(),
        started_at = COALESCE(job.started_at, NOW()),
        attempts = job.attempts + 1,
        updated_at = NOW()
    FROM next_job
    WHERE job.id = next_job.id
    RETURNING job.*;
END;
$$ LANGUAGE plpgsql;

SELECT pg_notify('pgrst', 'reload schema');
SELECT '013: processing_jobs worker claim 函数创建完成' AS message;
