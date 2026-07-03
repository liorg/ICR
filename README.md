# Scenario Platform — Worker + Spine + Provisioner + Scheduler

## ארכיטקטורה

```
┌─────────────────────────────────────────────────────────────────┐
│  Docker Swarm (overlay: scenario_spine-net)                     │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐        │
│  │ Provisioner   │   │ Worker       │   │ Worker       │        │
│  │ (Go)          │   │ 972504476645 │   │ 972504477197 │  ...   │
│  │ polls phones  │   │ :9000        │   │ :9000        │        │
│  │ creates svcs  │   └──────┬───────┘   └──────┬───────┘        │
│  └──────┬────────┘          │                   │               │
│         │                   │ POST /api/spine/* │               │
│         │ docker API        ▼                   ▼               │
│  ┌──────┴────────────────────────────────────────┐              │
│  │              Supabase                         │              │
│  └───────────────────────────────────────────────┘              │
│                                                                 │
│  ┌──────────────┐                                               │
│  │ Scheduler    │─── POST /api/spine/dispatch ──►               │
│  │ (APScheduler)│                                               │
│  └──────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘

                    ▲ HTTPS
                    │
┌───────────────────┴──────────────────────┐
│  vid.michal-solutions.com                │
│  FastAPI (:8000)                         │
│  ├── routers/contacts.py     (קיים)      │
│  ├── routers/messages.py     (קיים)      │
│  ├── routers/scenarios.py    (קיים)      │
│  └── routers/spine.py        (חדש)       │
└───────────────────┬──────────────────────┘
                    │
                    ▼
            React (Vercel)
            ActiveChatsScreen.jsx
```

## רכיבים

### 1. Worker (.NET + Deno)

מריץ סצנריות WhatsApp. כל Worker מנהל טלפון אחד.

- **Image:** `liorgr/worker-scenario-runtime:latest` (Docker Hub, ציבורי)
- **Port:** 9000
- **ENV:** `PHONE_ID`, `SERVICE_NAME`, `SPINE_URL`, `DENO_BIN`, `DENO_TMPDIR`
- **Input:** מקבל `init` event מ-Spine עם scenario JSON
- **Output:** שולח events, leaves, summary ל-Spine

### 2. Spine (FastAPI router)

**לא service נפרד** — router חדש (`spine.py`) בתוך FastAPI הקיים ב-`vid.michal-solutions.com`.

Worker שולח לכאן:
- `POST /api/spine/events` — לוג אירועים
- `POST /api/spine/leaves` — כל הודעה בזמן אמת
- `PATCH /api/spine/leaves/{id}/status` — עדכון סטטוס הודעה
- `POST /api/spine/calls/{id}/summary` — סיכום בסוף הרצה
- `POST /api/spine/heartbeat` — דופק

React קורא מכאן:
- `GET /api/spine/phones/{phone_id}/active` — אנשי קשר active + שיחה אחרונה
- `GET /api/spine/phones/{phone_id}/contacts/{contact_id}/calls` — כל ההרצות
- `GET /api/spine/calls/{call_id}/leaves` — הודעות (live poll)
- `GET /api/spine/workers` — רשימת Workers

### 3. Provisioner (Go)

Job שרץ כל 30 שניות. בודק טבלת `phones` מול `phone_workers`.

- טלפון `status=active` בלי worker → יוצר Docker Swarm service
- טלפון כבר לא active → מוריד את ה-service
- **Image:** `${REGISTRY_HOST}:5000/provisioner:latest` (registry פרטי)
- **חייב לרוץ על manager node** (צריך docker.sock)

### 4. Scheduler (APScheduler)

Job שבודק טבלת `schedules`, קורא ל-Spine `POST /api/spine/dispatch` כשמגיע זמן.

- **Image:** `${REGISTRY_HOST}:5000/scheduler:latest` (registry פרטי)

## טבלאות Supabase חדשות

### `phone_workers`
| עמודה | סוג | תיאור |
|---|---|---|
| phone_id | TEXT PK | מזהה טלפון |
| service_name | TEXT | שם ה-Docker service (worker-{phone_id}) |
| replicas | INT | מספר replicas |
| status | TEXT | pending / running / stopped / error |
| image | TEXT | Docker image |

### `spine_calls`
כל הרצה של סצנריו עם איש קשר.

| עמודה | סוג | תיאור |
|---|---|---|
| call_id | TEXT PK | מזהה ההרצה |
| scenario_id | TEXT | איזה סצנריו רץ |
| phone_id | TEXT | מאיזה טלפון |
| contact_id | TEXT | לאיזה איש קשר |
| status | TEXT | running / completed / failed / expired |
| started_at | TIMESTAMPTZ | מתי התחיל |
| finished_at | TIMESTAMPTZ | מתי נגמר |
| duration_seconds | INT | משך בשניות |
| sender_count | INT | כמה הודעות הבוט שלח |
| expected_count | INT | כמה תשובות התקבלו |
| mismatch_count | INT | כמה תשובות לא תאמו |

### `spine_leaves`
כל הודעה בודדת בתוך call.

| עמודה | סוג | תיאור |
|---|---|---|
| leaf_id | TEXT PK | מזהה |
| call_id | TEXT FK | שייך ל-call |
| type | TEXT | Sender (בוט שלח) / Expected (ממתין לתשובה) |
| content | TEXT | תוכן ההודעה |
| wa_type | TEXT | text / buttons / list / button_reply |
| status | TEXT | Pending → Sent → Matched / Mismatched / Timeout |

### `spine_events`
לוג אירועים מ-Workers (init, step_start, timeout, error). בעיקר ל-debug.

## התקנה

### שלב 1 — Supabase
הריצו `migration.sql` ב-SQL Editor.

### שלב 2 — FastAPI
```bash
# העתיקו spine.py ל-routers/
cp spine.py /home/lior/projects/github/whatsapp-single/fastapi/routers/

# הוסיפו ל-main.py:
# from routers import spine
# app.include_router(spine.router, prefix="/api")

# restart
sudo systemctl restart fastapi.service
```

### שלב 3 — React
```bash
# העתיקו את הקומפוננטה
cp ActiveChatsScreen.jsx src/screens/

# הוסיפו טאב ב-PhoneDetail:
# import ActiveChatsScreen from "./screens/ActiveChatsScreen";
# { label: "שיחות פעילות", component: <ActiveChatsScreen phone={phone} /> }
```

### שלב 4 — Private Registry
```bash
# על כל node (manager + workers):
sudo tee /etc/docker/daemon.json << EOF
{ "insecure-registries": ["10.0.0.5:5000"] }
EOF
sudo systemctl restart docker

# הוסיפו ל-.env:
REGISTRY_HOST=10.0.0.5
```

### שלב 5 — Build & Push
```bash
# Provisioner
docker build -t ${REGISTRY_HOST}:5000/provisioner:latest ./provisioner/
docker push ${REGISTRY_HOST}:5000/provisioner:latest

# Scheduler
docker build -t ${REGISTRY_HOST}:5000/scheduler:latest ./scheduler/
docker push ${REGISTRY_HOST}:5000/scheduler:latest

# Worker (ציבורי, כבר ב-Hub)
docker push liorgr/worker-scenario-runtime:latest
```

### שלב 6 — Deploy
```bash
docker stack deploy -c docker-compose.yml scenario
```

## Images

| Image | Registry | גישה |
|---|---|---|
| `liorgr/worker-scenario-runtime:latest` | Docker Hub | ציבורי |
| `provisioner:latest` | `${REGISTRY_HOST}:5000` | פרטי |
| `scheduler:latest` | `${REGISTRY_HOST}:5000` | פרטי |

## זרימת הודעה

```
1. Scheduler/React → POST /api/spine/dispatch
   ↓
2. Spine → POST http://worker-{phone_id}:9000/webhook/event (init)
   ↓
3. Worker מריץ סצנריו:
   ├── שולח הודעה ל-WhatsApp (via Baileys)
   ├── POST /api/spine/leaves (type=Sender, status=Sent)
   ├── מחכה לתשובה
   ├── תשובה מגיעה
   ├── POST /api/spine/leaves (type=Expected, status=Matched)
   └── ...חוזר עד סוף הסצנריו
   ↓
4. Worker → POST /api/spine/calls/{id}/summary
   ↓
5. React polls GET /api/spine/calls/{id}/leaves → מציג בועות צ'אט
```

## ENV Reference

### Worker
```env
PHONE_ID=972504476645
SERVICE_NAME=worker-972504476645
PORT=9000
SPINE_URL=https://vid.michal-solutions.com/api/spine
DENO_BIN=/usr/local/bin/deno
DENO_TMPDIR=/tmp/deno-scripts
```

### Provisioner
```env
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
SWARM_NETWORK=scenario_spine-net
SPINE_URL=https://vid.michal-solutions.com/api/spine
WORKER_IMAGE=liorgr/worker-scenario-runtime:latest
```

### Scheduler
```env
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
SPINE_URL=https://vid.michal-solutions.com/api/spine
CHECK_INTERVAL_SECONDS=30
```
