# Data Spine — ERD

סכמת ה-DB כפי שהיא **בפועל** (אומת מול `information_schema`), לא כפי שהקוד הניח.

---

## דיאגרמה

```mermaid
erDiagram
  CONTACTS  ||--o{ CALLS      : "has"
  SCENARIOS ||--o{ CALLS      : "runs as"
  SCHEDULES ||--o{ CALLS      : "fires"
  CONTACTS  ||--o{ SCENARIOS  : "bound to"
  SCENARIOS ||--o{ SCHEDULES  : "wrapped by"

  CALLS ||--o{ MESSAGES     : "produces"
  CALLS ||--o{ SPINE_LEAVES : "emits"
  CALLS ||--o{ SPINE_EVENTS : "logs"

  SPINE_LEAVES ||--o{ SPINE_LEAF_MESSAGES : "links"
  MESSAGES     ||--o{ SPINE_LEAF_MESSAGES : "links"

  CONTACTS {
    uuid id PK
    uuid phone_id
    text number "NOT phone"
    text lid "WhatsApp id"
    text name
    text whatsapp_name
    text tag "active | draft"
    bool is_bot
    uuid parent_contact_id
  }

  SCENARIOS {
    uuid id PK
    uuid phone_id
    uuid contact_id FK
    text name
    text status "draft | active"
    text event_type "trigger | scheduler"
    int priority "default 15"
    jsonb config
    interval estimated_duration_minutes
  }

  SCHEDULES {
    uuid id PK
    uuid phone_id
    uuid contact_id
    uuid scenario_id FK
    text schedule_name
    text schedule_type "hourly daily weekly monthly once"
    text status "ready | running | disabled"
    text cron_expr "JSON string"
    timestamp next_run "NEVER COMPUTED"
    timestamp last_run
  }

  CALLS {
    uuid id PK
    uuid phone_id
    uuid contact_id FK
    uuid scenario_id FK
    uuid schedule_id FK
    text status "running queued completed"
    text source "trigger scheduler api"
    int priority
    jsonb scenario_snapshot
    timestamp started_at
    timestamp ended_at
    int duration_seconds
    text last_step_id
    jsonb variables
    int mismatch_count
  }

  MESSAGES {
    uuid id PK
    uuid call_id FK
    uuid contact_id
    uuid phone_id
    bool direction "TRUE = incoming"
    text content
    text message_type
    text whatsapp_message_id
    text status
  }

  SPINE_LEAVES {
    text leaf_id PK
    uuid call_id FK
    text step_id
    text type
    text wa_type
    text status "Sent Failed Pending"
    text content
    jsonb meta
  }

  SPINE_EVENTS {
    bigint id PK
    uuid call_id FK
    uuid phone_id
    text event_type
    text step_id
    jsonb data
  }

  SPINE_LEAF_MESSAGES {
    bigint id PK
    text leaf_id FK
    uuid message_id FK
  }
```

---

## טבלאות קונפיגורציה (לא מקושרות — בכוונה)

אלה **ספרי הכתובות** של שני המישורים. הן לא דאטה, ולכן אין להן FK.

```mermaid
erDiagram
  PHONE_WORKERS {
    uuid phone_id
    text service_name "Swarm DNS"
    text status "running"
    timestamp updated_at
  }
  WEBHOOK_REGISTRATIONS {
    text callback_url UK
    text type UK "trigger"
    bool is_active
  }
```

| טבלה | מישור | מי כותב | מי קורא |
|------|-------|---------|---------|
| `phone_workers` | Spine → Worker | Provisioner + heartbeat | `_worker_url()` |
| `webhook_registrations` | HostAgent → Spine | **ה-Spine** (`registration_loop`) | `GetActiveWebhooksByTypeAsync` |

`unique (callback_url, type)` — עליו נשען ה-upsert העמיד ל-race.

---

## שלוש תובנות מה-ERD

**1. `calls` היא הצומת המרכזית.**
ארבעה FK נכנסים (`contact`, `scenario`, `schedule`, `phone`), שלוש טבלאות תלויות (`messages`, `spine_leaves`, `spine_events`).

זו הסיבה ש-`spine_calls` הייתה טעות — היא פיצלה את הצומת לשתיים, עם `status`/`phone_id`/`contact_id` כפולים ותלויים בסנכרון. **שדות ה-summary נכנסו ישירות ל-`calls`.**

**2. `contact_id` מופיע בשלוש טבלאות במקביל.**
`scenarios` · `schedules` · `calls` — והן חייבות להיות עקביות.
אם `schedules.contact_id ≠ scenarios.contact_id`, ה-scheduler ייצור call לאיש קשר שהתרחיש לא מיועד לו.

ה-`CreateModal` מונע את זה בצד ה-UI (`contactId: sc?.contact_id`), אבל **אין אילוץ ב-DB**. שווה לשקול:
```sql
-- אופציונלי: לאכוף שהתזמון תואם את התרחיש
alter table schedules add constraint schedules_contact_matches_scenario
  check (true);  -- דורש trigger, לא check פשוט
```

**3. `spine_leaves.leaf_id` הוא `text`** — החריגה היחידה בעולם של `uuid`.
זה תקין: המזהה מיוצר ב-Worker, לא ב-DB.

---

## מלכודות שמות

| נראה הגיוני | האמת |
|-------------|------|
| `contacts.phone` | **`contacts.number`** |
| `scenarios.is_published` | **`scenarios.status = 'active'`** |
| `scenarios.auto_trigger` | **לא קיים** — הקישור הוא `(phone_id, contact_id)` |
| `schedules.next_run_at` | **`schedules.next_run`** |
| `schedules.interval_seconds` | **`cron_expr`** (JSON string) |
| `spine_calls` | **לא קיימת מעולם** |
| `calls.id` = text | **`uuid`** |

**`contacts.tag = 'active'` ≠ `scenarios.status = 'active'`** — אותה מילה, שתי משמעויות שונות לגמרי.

---

## `direction` — קונבנציה יחידה

```
messages.direction = TRUE   →  נכנסת   (מאיש הקשר)
messages.direction = FALSE  →  יוצאת   (מהטלפון שלנו)
```

נגזר מ-`AddMessageAsync(..., direction: isIncoming)` ב-HostAgent.

> ה-Spine כתב קודם `direction=True` על הודעה **יוצאת** — היפוך מול ה-HostAgent, על אותה עמודה. תוקן ב-`send.py`.

---

## אינדקסים קריטיים

```sql
-- ה-invariant: call פעיל אחד לכל (טלפון, איש קשר)
create unique index uniq_running_call_per_contact
    on calls (phone_id, contact_id) where status = 'running';

-- התור
create index idx_calls_queued
    on calls (phone_id, contact_id, priority, created_at) where status = 'queued';

-- טאב Calls של תזמון / איש קשר
create index idx_calls_schedule     on calls (schedule_id, created_at desc);
create index idx_calls_contact_hist on calls (contact_id,  created_at desc);

-- ה-upsert של הרישום
alter table webhook_registrations
  add constraint webhook_registrations_url_type_key unique (callback_url, type);
```

---

## ⚠️ Timezone

כל ה-`timestamp` הם **`without time zone`** (נאיביים), וה-RPCs משתמשים ב-`now()` שמחזיר `timestamptz`.

באזור UTC+3 → **תזמונים יירו בהיסט של 3 שעות.** זה ייראה כמו "לא מדויק", לא כמו באג.

```sql
alter database postgres set timezone to 'UTC';
```
או להמיר את העמודות ל-`timestamptz`. **בחר אחד.**
