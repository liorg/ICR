-- ══════════════════════════════════════════════════════════════════════════════
-- Spine — standalone scenario orchestrator
-- ══════════════════════════════════════════════════════════════════════════════

-- Workers per phone
CREATE TABLE IF NOT EXISTS phone_workers (
    phone_id      TEXT PRIMARY KEY,
    service_name  TEXT NOT NULL UNIQUE,
    replicas      INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'pending',
    image         TEXT DEFAULT 'liorgr/worker-scenario-runtime:latest',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Calls — each scenario run, stores scenario JSON snapshot
CREATE TABLE IF NOT EXISTS spine_calls (
    call_id           TEXT PRIMARY KEY,
    scenario_id       TEXT NOT NULL,
    scenario_snapshot JSONB NOT NULL,
    phone_id          TEXT NOT NULL,
    contact_id        TEXT NOT NULL,
    contact_phone     TEXT,
    contact_name      TEXT,
    status            TEXT DEFAULT 'running',
    started_at        TIMESTAMPTZ DEFAULT NOW(),
    finished_at       TIMESTAMPTZ,
    duration_seconds  INTEGER DEFAULT 0,
    last_step_id      TEXT,
    variables         JSONB DEFAULT '{}',
    sender_count      INTEGER DEFAULT 0,
    expected_count    INTEGER DEFAULT 0,
    mismatch_count    INTEGER DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_calls_phone_contact ON spine_calls(phone_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON spine_calls(status);

-- Messages — actual WhatsApp messages through Spine
CREATE TABLE IF NOT EXISTS spine_messages (
    id              BIGSERIAL PRIMARY KEY,
    phone_id        TEXT NOT NULL,
    contact_id      TEXT NOT NULL,
    direction       BOOLEAN NOT NULL,
    content         TEXT,
    message_type    TEXT DEFAULT 'text',
    wa_message_id   TEXT,
    status          TEXT DEFAULT 'pending',
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_phone_contact ON spine_messages(phone_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_messages_wa ON spine_messages(wa_message_id);

-- Leaves — scenario steps from Workers
CREATE TABLE IF NOT EXISTS spine_leaves (
    leaf_id     TEXT PRIMARY KEY,
    call_id     TEXT NOT NULL REFERENCES spine_calls(call_id) ON DELETE CASCADE,
    step_id     TEXT,
    type        TEXT NOT NULL,
    content     TEXT,
    wa_type     TEXT,
    status      TEXT DEFAULT 'Pending',
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    meta        JSONB
);
CREATE INDEX IF NOT EXISTS idx_leaves_call ON spine_leaves(call_id, timestamp);

-- Leaf ↔ Message — many-to-many
CREATE TABLE IF NOT EXISTS spine_leaf_messages (
    id          BIGSERIAL PRIMARY KEY,
    leaf_id     TEXT NOT NULL REFERENCES spine_leaves(leaf_id) ON DELETE CASCADE,
    message_id  BIGINT NOT NULL REFERENCES spine_messages(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(leaf_id, message_id)
);

-- Runtime — active call state
CREATE TABLE IF NOT EXISTS spine_runtime (
    call_id       TEXT PRIMARY KEY REFERENCES spine_calls(call_id) ON DELETE CASCADE,
    phone_id      TEXT NOT NULL,
    contact_id    TEXT NOT NULL,
    current_step  TEXT,
    variables     JSONB DEFAULT '{}',
    status        TEXT DEFAULT 'active',
    worker_service TEXT,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_runtime_phone_contact ON spine_runtime(phone_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_runtime_status ON spine_runtime(status);

-- Events — log from Workers
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
CREATE INDEX IF NOT EXISTS idx_events_call ON spine_events(call_id);

-- Webhook registrations per phone per event type
CREATE TABLE IF NOT EXISTS spine_webhooks (
    id           BIGSERIAL PRIMARY KEY,
    phone_id     TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    callback_url TEXT NOT NULL,
    agent_url    TEXT NOT NULL,
    status       TEXT DEFAULT 'active',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(phone_id, event_type)
);

-- Notifications
CREATE TABLE IF NOT EXISTS spine_notifications (
    id          BIGSERIAL PRIMARY KEY,
    phone_id    TEXT,
    call_id     TEXT,
    contact_id  TEXT,
    type        TEXT NOT NULL,
    title       TEXT,
    body        TEXT,
    data        JSONB,
    read        BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
