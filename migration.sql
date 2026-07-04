-- calls — snapshot
ALTER TABLE calls ADD COLUMN IF NOT EXISTS scenario_snapshot JSONB;

-- messages — link to call (N:1)
ALTER TABLE messages ADD COLUMN IF NOT EXISTS call_id TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_call ON messages(call_id);

-- phone_workers
CREATE TABLE IF NOT EXISTS phone_workers (
    phone_id      TEXT PRIMARY KEY,
    service_name  TEXT NOT NULL UNIQUE,
    replicas      INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'pending',
    image         TEXT DEFAULT 'liorgr/worker-scenario-runtime:latest',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- spine_leaves
CREATE TABLE IF NOT EXISTS spine_leaves (
    leaf_id     TEXT PRIMARY KEY,
    call_id     TEXT NOT NULL,
    step_id     TEXT,
    type        TEXT NOT NULL,
    content     TEXT,
    wa_type     TEXT,
    status      TEXT DEFAULT 'Pending',
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    meta        JSONB
);
CREATE INDEX IF NOT EXISTS idx_leaves_call ON spine_leaves(call_id, timestamp);

-- spine_leaf_messages (N:N)
CREATE TABLE IF NOT EXISTS spine_leaf_messages (
    id          BIGSERIAL PRIMARY KEY,
    leaf_id     TEXT NOT NULL REFERENCES spine_leaves(leaf_id) ON DELETE CASCADE,
    message_id  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(leaf_id, message_id)
);

-- spine_events
CREATE TABLE IF NOT EXISTS spine_events (
    id          BIGSERIAL PRIMARY KEY,
    call_id     TEXT,
    phone_id    TEXT,
    event_type  TEXT NOT NULL,
    step_id     TEXT,
    data        JSONB,
    timestamp   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_call ON spine_events(call_id);
