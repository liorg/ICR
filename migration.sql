-- Workers שמקושרים לטלפונים
CREATE TABLE IF NOT EXISTS phone_workers (
    id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    phone_id      TEXT NOT NULL UNIQUE,
    service_name  TEXT NOT NULL UNIQUE,
    replicas      INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'pending',
    image         TEXT DEFAULT 'liorgr/worker-scenario-runtime:latest',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- הרצות של סצנריות (Worker שולח summary כשנגמר)
CREATE TABLE IF NOT EXISTS spine_calls (
    call_id          TEXT PRIMARY KEY,
    scenario_id      TEXT,
    phone_id         TEXT NOT NULL,
    contact_id       TEXT NOT NULL,
    status           TEXT DEFAULT 'running',
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    duration_seconds INTEGER DEFAULT 0,
    last_step_id     TEXT,
    variables        JSONB DEFAULT '{}',
    sender_count     INTEGER DEFAULT 0,
    expected_count   INTEGER DEFAULT 0,
    mismatch_count   INTEGER DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_spine_calls_phone_contact ON spine_calls(phone_id, contact_id);

-- הודעות בתוך הרצה (Worker שולח כל הודעה בזמן אמת)
CREATE TABLE IF NOT EXISTS spine_leaves (
    leaf_id     TEXT PRIMARY KEY,
    call_id     TEXT NOT NULL REFERENCES spine_calls(call_id) ON DELETE CASCADE,
    step_id     TEXT,
    type        TEXT NOT NULL,
    message_id  TEXT,
    content     TEXT,
    wa_type     TEXT,
    status      TEXT DEFAULT 'Pending',
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    meta        JSONB
);
CREATE INDEX IF NOT EXISTS idx_spine_leaves_call ON spine_leaves(call_id, timestamp);

-- לוג אירועים מ-Workers
CREATE TABLE IF NOT EXISTS spine_events (
    id          BIGSERIAL PRIMARY KEY,
    call_id     TEXT,
    phone_id    TEXT,
    event_type  TEXT NOT NULL,
    step_id     TEXT,
    step_type   TEXT,
    data        JSONB,
    timestamp   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_spine_events_call ON spine_events(call_id);
