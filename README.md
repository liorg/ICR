# שיחות של אנשי קשר ACTIVE — מימוש

## ERD

```
phones (קיים)
  ├── 1:1 → phone_workers           Go provisioner יוצר
  ├── 1:N → contacts (tag=active)
  │           ├── 1:N → calls        + scenario_snapshot (JSONB)
  │           │          ├── 1:N → messages  + call_id
  │           │          └── 1:N → spine_leaves
  │           │                       └── N:N → messages (via spine_leaf_messages)
  │           └── 1:N → messages
  └── (spine_events — לוג)
```

## קבצים

| קובץ | לאן | מה עושה |
|---|---|---|
| `migration.sql` | Supabase SQL Editor | 2 עמודות + 4 טבלאות |
| `active_chats.py` | `routers/active_chats.py` ב-vid.michal | API endpoints |
| `ActiveChatsScreen.jsx` | `src/screens/` ב-React | UI — contacts → messages |
| `provisioner_main.go` | `provisioner/main.go` | Go — יוצר Workers |
| `provisioner_Dockerfile` | `provisioner/Dockerfile` | |
| `provisioner_go.mod` | `provisioner/go.mod` | |
| `erd.html` | פתח בדפדפן | דיאגרמת טבלאות |

## התקנה

### 1. Supabase
```sql
-- הריצו migration.sql ב-SQL Editor
-- מוסיף: calls.scenario_snapshot, messages.call_id
-- יוצר: phone_workers, spine_leaves, spine_leaf_messages, spine_events
```

### 2. FastAPI (vid.michal-solutions.com)
```python
# העתיקו active_chats.py → routers/active_chats.py

# main.py — הוסיפו:
from routers import active_chats
app.include_router(active_chats.router, prefix="/api")
```

### 3. React (Vercel)
```jsx
// העתיקו ActiveChatsScreen.jsx → src/screens/

// PhoneDetail — הוסיפו טאב:
import ActiveChatsScreen from "./screens/ActiveChatsScreen";
{ label: "שיחות", component: <ActiveChatsScreen phone={phone} /> }
```

### 4. Go Provisioner
```bash
# מבנה:
# provisioner/
#   main.go       ← provisioner_main.go
#   Dockerfile    ← provisioner_Dockerfile
#   go.mod        ← provisioner_go.mod

cd provisioner
docker build -t ${REGISTRY_HOST}:5000/provisioner:latest .
docker push ${REGISTRY_HOST}:5000/provisioner:latest
```

### 5. Deploy
```bash
docker stack deploy -c docker-compose.yml scenario
```

## API Endpoints

```
GET /api/active-chats/{phone_id}/contacts
    → contacts active + last_call + last_message + counts

GET /api/active-chats/{phone_id}/contacts/{contact_id}/messages
    → הודעות WhatsApp (messages table, direction: true=יוצא false=נכנס)

GET /api/active-chats/{phone_id}/contacts/{contact_id}/calls
    → כל ה-calls (הרצות scenario)

GET /api/active-chats/calls/{call_id}/messages
    → הודעות של call ספציפי (messages.call_id = X)

GET /api/active-chats/calls/{call_id}/leaves
    → צעדי scenario + message_ids מקושרים
```

## טבלאות

### שינויים על קיימות
- `calls` + `scenario_snapshot` (JSONB) — עותק של הסצנריו ברגע ההרצה
- `messages` + `call_id` (TEXT) — מקשר הודעה ל-call (רבים ליחיד)

### חדשות
- `phone_workers` — Worker Docker service לכל טלפון
- `spine_leaves` — צעד בסצנריו (Sender/Expected)
- `spine_leaf_messages` — N:N leaf ↔ message
- `spine_events` — לוג אירועים

### לא צריך
- ~~spine_calls~~ → משתמשים ב-`calls`
- ~~spine_messages~~ → משתמשים ב-`messages`
- ~~spine_runtime~~ → שואלים `calls WHERE status='running'`
- ~~spine_notifications~~ → לא צריך

## Go Provisioner

רץ כל 30 שניות. בודק `phones WHERE status='active'` מול `phone_workers`.

- טלפון active בלי worker → `docker service create`
- טלפון כבר לא active → `docker service remove`

שם service:
```
worker-{phone_number}-{phone_id[:8]}
```

דוגמה:
```
worker-972504476645-3beff8fa
```

Worker ENV:
```env
PHONE_ID=3beff8fa-xxxx-xxxx
PHONE_NUMBER=972504476645
SERVICE_NAME=worker-972504476645-3beff8fa
SPINE_URL=http://scenario_data-spine:8000
```

Provisioner חייב manager node (צריך docker.sock).

## React מסך

2 פאנלים בסגנון WhatsApp:

```
┌──────────────────┬─────────────────────────────────┐
│ אנשי קשר פעילים  │  Header: שם + טלפון             │
│                  │                                 │
│ ◉ לינוי          │  ── 05/07/2026 ──               │
│   ✓ שלום...      │                                 │
│                  │        שלום, אני הבוט    [ירוק] │
│ ◉ דני            │  [לבן] היי                      │
│   2 שיחות        │        תודה!             [ירוק] │
│                  │                                 │
│                  │  ── 06/07/2026 ──               │
│                  │  [לבן] מה שלומך?                │
└──────────────────┴─────────────────────────────────┘
```

- direction=true (בוט) → ירוק, ימין
- direction=false (נכנס) → לבן, שמאל
- מפריד תאריכים
- auto-refresh כל 5 שניות
- תמיכה: text, image, audio, file, buttons, button_reply
- RTL


# שינוי קוד → build + update:
./build.sh

# שינוי compose/env → deploy:
./deploy.sh