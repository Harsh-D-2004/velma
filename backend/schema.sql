CREATE TABLE IF NOT EXISTS jobs (
  job_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','paused','done','failed')),
  article_key   text,
  retries_left  int  NOT NULL DEFAULT 1,
  resume_at     timestamptz,
  worker_id     text,
  html_url      text,
  error_message text,
  archive_key   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_ready_idx ON jobs (created_at)
  WHERE status IN ('queued','paused');
