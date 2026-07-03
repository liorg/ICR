# Data Spine — Scenario Orchestrator

## מבנה

```
spine/                              Spine service (Docker Swarm)
├── Dockerfile
├── main.py
├── dependencies.py
├── requirements.txt
└── routers/
    ├── calls.py                    יצירת call + scenario snapshot + runtime
    ├── send.py                     POST /send/{phone_id} → Agent (Baileys)
    ├── incoming.py                 POST /incoming/{phone_id} ← Agent
    ├── worker_events.py            POST /events, /leaves, /heartbeat ← Worker
    ├── webhooks.py                 רישום webhook לפי phone + סוג
    ├── conversations.py            GET endpoints ל-React
    └── notifications.py            התראות

vid_michal_spine.py                 Proxy router ב-vid.michal-solutions.com
ActiveChatsScreen.jsx               React קומפוננטה — 3 פאנלים
migration.sql                       9 טבלאות Supabase
docker-compose.yml                  Swarm stack
```

## ארכיטקטורה

```
React (Vercel)
  │ apiFetch("/spine/api/phones/{id}/active")
  ▼
vid.michal-solutions.com (FastAPI)
  │ routers/spine.py (proxy)
  │ proxy_pass → 127.0.0.1:8100
  ▼
┌─────────────────────────────────────────────────────────┐
│ Docker Swarm (scenario_spine-net)                       │
│                                                         │
│  ┌────────────────┐    ┌────────────┐  ┌────────────┐  │
│  │ data-spine     │    │ Worker 1   │  │ Worker 2   │  │
│  │ :8000 (→8100)  │◄───│ :9000      │  │ :9000      │  │
│  │                │───►│            │  │            │  │
│  └───────┬────────┘    └─────┬──────┘  └─────┬──────┘  │
│          │                   │               │         │
│  ┌───────┴────────┐    ┌─────┴──────────────┴──────┐  │
│  │ Provisioner    │    │ Baileys Agents (:3001)     │  │
│  │ Go, כל 30s     │    │ host docker               │  │
│  └────────────────┘    └───────────────────────────┘  │
│                                                         │
│  ┌────────────────┐                                    │
│  │ Scheduler      │─── POST /calls → Spine             │
│  │ APScheduler    │                                    │
│  └────────────────┘                                    │
└─────────────────────────────────────────────────────────┘
```

## טבלאות

### phone_workers
Worker לכל טלפון. Go Provisioner יוצר שורה כשמקים service.

| עמודה | תיאור |
|---|---|
| phone_id | PK, מזהה טלפון |
| service_name | worker-{phone_id} |
| replicas | מספר replicas |
| status | pending / running / stopped / error |

### spine_calls
כל הרצה של סצנריו. כולל **snapshot** של ה-scenario JSON ברגע ההרצה.

| עמודה | תיאור |
|---|---|
| call_id | PK |
| scenario_id | מזהה סצנריו |
| scenario_snapshot | JSONB — עותק של הסצנריו |
| phone_id | מאיזה טלפון |
| contact_id | לאיזה איש קשר |
| status | running / completed / failed / expired |
| sender_count | כמה הודעות הבוט שלח |
| expected_count | כמה תשובות התקבלו |
| mismatch_count | כמה לא תאמו |

### spine_messages
כל הודעת WhatsApp שעברה דרך Spine (שליחה וקבלה).

| עמודה | תיאור |
|---|---|
| id | PK auto |
| phone_id | טלפון |
| contact_id | איש קשר |
| direction | true=יוצא (בוט), false=נכנס |
| content | תוכן |
| message_type | text / image / audio / buttons / menu / button_reply |
| wa_message_id | מזהה Baileys |
| status | pending / sent / delivered / read / failed |

### spine_leaves
צעד בסצנריו — Worker שולח כל leaf בזמן אמת.

| עמודה | תיאור |
|---|---|
| leaf_id | PK |
| call_id | FK → spine_calls |
| type | Sender (בוט שלח) / Expected (ממתין לתשובה) |
| content | תוכן ההודעה |
| wa_type | text / buttons / list / button_reply |
| status | Pending → Sent → Matched / Mismatched / Timeout |

### spine_leaf_messages
**רבים לרבים** — מקשר leaf ל-message. leaf_id + message_id.

### spine_runtime
מצב פעיל — איזה call רץ עכשיו לאיזה phone+contact. Spine משתמש בזה כדי לנתב הודעות נכנסות ל-Worker הנכון.

| עמודה | תיאור |
|---|---|
| call_id | PK, FK → spine_calls |
| phone_id | טלפון |
| contact_id | איש קשר |
| current_step | צעד נוכחי |
| worker_service | שם ה-Docker service |
| status | active / waiting / completed |

### spine_events
לוג אירועים מ-Workers — init, step_start, timeout, error. ל-debug.

### spine_webhooks
רישום webhook לכל טלפון לפי סוג אירוע. Spine נרשם מול ה-Agent (Baileys) לקבלת הודעות נכנסות.

| עמודה | תיאור |
|---|---|
| phone_id | טלפון |
| event_type | message / status_update / connection / all |
| callback_url | URL ש-Agent קורא אליו |
| agent_url | URL של ה-Agent |

### spine_notifications
התראות — call_started, call_ended, message_failed, timeout, error.

## API

### Spine — Call Management
```
POST   /calls                          יצירת call חדש + snapshot
GET    /calls/{call_id}                call + leaves
PATCH  /calls/{call_id}                עדכון סטטוס
POST   /calls/{call_id}/summary        Worker שולח סיכום
```

### Spine — Send (→ Agent)
```
POST   /send/{phone_id}               שולח WhatsApp דרך Agent
                                       stores spine_messages
                                       links leaf ↔ message
```

### Spine — Incoming (← Agent)
```
POST   /incoming/{phone_id}           Agent שולח הודעה נכנסת
                                       → יש call פעיל? forward ל-Worker
                                       → אין? store + notification
```

### Spine — Worker Events
```
POST   /events                        לוג אירועים
POST   /leaves                        leaf חדש
PATCH  /leaves/{leaf_id}/status        עדכון + link ל-message
POST   /heartbeat                     Worker alive
```

### Spine — Webhooks
```
POST   /webhooks                      רישום webhook
GET    /webhooks/{phone_id}           רשימת webhooks
DELETE /webhooks/{id}                 הסרה
POST   /webhooks/register-agent/{id}  רישום אוטומטי מול Agent
```

### Spine — React API
```
GET    /api/phones/{phone_id}/active              אנשי קשר + last call
GET    /api/phones/{id}/contacts/{id}/calls       כל ההרצות
GET    /api/calls/{call_id}                       call + leaves
GET    /api/calls/{call_id}/leaves                live poll
GET    /api/calls/{call_id}/messages              messages מקושרים
GET    /api/workers                               רשימת workers
```

### Spine — Notifications
```
GET    /notifications/{phone_id}                  רשימה
PATCH  /notifications/{id}/read                   סמן כנקרא
```

## React — ActiveChatsScreen

3 פאנלים:
1. **אנשי קשר** — רשימת contacts עם tag=active, אווטאר, סטטוס שיחה אחרונה
2. **שיחות** — כל ה-calls (הרצות scenario) לאיש קשר שנבחר
3. **צ'אט** — leaves כבועות WhatsApp. Sender=ירוק/ימין, Expected=לבן/שמאל

Live poll כל 2 שניות ל-calls עם status=running.

קורא דרך `apiFetch("/spine/api/...")` → vid.michal proxy → Spine.

## Deploy

### 1. Supabase
```bash
# הריצו migration.sql ב-SQL Editor
```

### 2. vid.michal-solutions.com
```bash
# שימו vid_michal_spine.py → routers/spine.py
# הוסיפו ל-main.py:
#   from routers import spine
#   app.include_router(spine.router, prefix="/api")
# הוסיפו ל-.env:
#   SPINE_URL=http://127.0.0.1:8100
```

### 3. React
```bash
# שימו ActiveChatsScreen.jsx → src/screens/
# הוסיפו טאב ב-PhoneDetail
```

### 4. Private Registry
```bash
# על כל node:
echo '{"insecure-registries":["10.0.0.5:5000"]}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker

# ב-.env:
REGISTRY_HOST=10.0.0.5
```

### 5. Build Spine
```bash
cd spine
docker build -t ${REGISTRY_HOST}:5000/data-spine:latest .
docker push ${REGISTRY_HOST}:5000/data-spine:latest
```

### 6. Deploy Stack
```bash
docker stack deploy -c docker-compose.yml scenario
```

## Images

| Image | Registry |
|---|---|
| liorgr/worker-scenario-runtime:latest | Docker Hub (ציבורי) |
| data-spine:latest | ${REGISTRY_HOST}:5000 (פרטי) |
| provisioner:latest | ${REGISTRY_HOST}:5000 (פרטי) |
| scheduler:latest | ${REGISTRY_HOST}:5000 (פרטי) |

## זרימת הודעה יוצאת

```
1. Scheduler → POST /calls (Spine יוצר call + snapshot)
2. Spine → POST Worker:9000/webhook/event (init + scenario JSON)
3. Worker מריץ סצנריו:
   a. Worker → POST Spine /send/{phone_id} (שלח הודעה)
   b. Spine → POST Agent:3001/send/text (Baileys → WhatsApp)
   c. Spine stores spine_messages + links leaf ↔ message
   d. Worker → POST Spine /leaves (leaf type=Sender)
```

## זרימת הודעה נכנסת

```
1. WhatsApp → Baileys Agent
2. Agent → POST Spine /incoming/{phone_id}
3. Spine checks spine_runtime: יש call פעיל לcontact?
   כן → forward ל-Worker:9000/webhook/event (entryMessage)
   לא → store message + notification
4. Worker → POST Spine /leaves (leaf type=Expected, status=Matched)
5. Worker → PATCH Spine /leaves/{id}/status (link message)
```

## ENV

### Worker
```env
PHONE_ID=972504476645
SERVICE_NAME=worker-972504476645
PORT=9000
SPINE_URL=http://scenario_data-spine:8000
```

### Spine
```env
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
SPINE_SELF_URL=http://scenario_data-spine:8000
```

### Provisioner
```env
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
SWARM_NETWORK=scenario_spine-net
SPINE_URL=http://scenario_data-spine:8000
WORKER_IMAGE=liorgr/worker-scenario-runtime:latest
```

### vid.michal
```env
SPINE_URL=http://127.0.0.1:8100
```
