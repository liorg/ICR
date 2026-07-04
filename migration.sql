-- ══════════════════════════════════════════════════════════════════════════════
-- Spine v3 — minimal additions to existing schema
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. calls — add scenario snapshot
ALTER TABLE calls ADD COLUMN IF NOT EXISTS scenario_snapshot JSONB;

-- 2. messages — link to call (N messages : 1 call)
ALTER TABLE messages ADD COLUMN IF NOT EXISTS call_id TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_call ON messages(call_id);

-- 3. phone_workers — Worker per phone
CREATE TABLE IF NOT EXISTS phone_workers (
    phone_id      TEXT PRIMARY KEY,
    service_name  TEXT NOT NULL UNIQUE,
    replicas      INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'pending',
    image         TEXT DEFAULT 'liorgr/worker-scenario-runtime:latest',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 4. spine_leaves — scenario steps
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

-- 5. spine_leaf_messages — N:N leaf ↔ message
CREATE TABLE IF NOT EXISTS spine_leaf_messages (
    id          BIGSERIAL PRIMARY KEY,
    leaf_id     TEXT NOT NULL REFERENCES spine_leaves(leaf_id) ON DELETE CASCADE,
    message_id  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(leaf_id, message_id)
);

-- 6. spine_events — log
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

-- 7. spine_webhooks — agent registrations
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

-- 8. spine_notifications — normalized, linked to contacts/calls
CREATE TABLE IF NOT EXISTS spine_notifications (
    id          BIGSERIAL PRIMARY KEY,
    phone_id    TEXT NOT NULL,
    contact_id  TEXT REFERENCES contacts(id) ON DELETE SET NULL,
    call_id     TEXT,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    read        BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notif_phone ON spine_notifications(phone_id, read);
CREATE INDEX IF NOT EXISTS idx_notif_contact ON spine_notifications(contact_id);
