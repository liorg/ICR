# Data Spine — ארכיטקטורה ומיגרציה

מסמך עבודה. מתעד את הסכמה, הזרימות, הבאגים שנמצאו והתיקונים.

---

## 1. מפת המערכת

ה-Spine הוא **reverse proxy דו-כיווני**. שני מישורים, כל אחד עם "ספר כתובות" משלו.

```
   Worker  ──(2)──►  SPINE  ──(3)──►  HostAgent  ──►  Baileys
      ◄───(1)───            ◄──(4)───
```

| # | כיוון | ספר כתובות | מי ממלא | היה |
|---|-------|-----------|---------|-----|
| 1 | Spine → Worker | `phone_workers.service_name` | Provisioner (כל 30ש') | ✅ |
| 2 | Worker → Spine | `SPINE_URL` (env) | קבוע | ✅ |
| 3 | Spine → HostAgent | `HOST_AGENT_URL` (env) | קבוע | ❌ היה `spine_webhooks` — ריקה |
| 4 | HostAgent → Spine | `webhook_registrations` | **ה-Spine עצמו** | ❌ אף אחד לא רשם |

**מכונות:** Swarm = `10.186.0.3` · HostAgent = `10.186.0.2` (systemd, **לא ב-overlay**).
לכן ה-callback חייב להיות `http://10.186.0.3:8001` ולא `scenario_data-spine`.

---

## 2. שלושת מקורות הטריגר

כולם מתכנסים ל-`services/calls.py::ensure_call`.

```
HostAgent ──► /incoming ─┐
Scheduler ───────────────┼──► ensure_call ──► spine_ensure_call ──► Worker
API ─────────────────────┘                          ▲
                                        advisory lock + unique index
```

**`incoming` מחליט · `dispatch` מבצע.** ההפרדה הזו היא מה שמאפשר לאכוף invariant יחיד.

---

## 3. ה-invariant

> תמיד **≤ 1 call פעיל** לכל `(phone_id, contact_id)`.

נאכף ב-DB בשתי שכבות:

| שכבה | תפקיד |
|------|-------|
| `uniq_running_call_per_contact` (partial unique index על `status='running'`) | האמת הסופית. מחזיק גם מול כתיבה ידנית. |
| `spine_lock_slot()` — advisory lock על `(phone,contact)` | מסרלל `ensure` ↔ `complete`. |

**למה ה-index לבדו לא מספיק:**

```
complete_call:  UPDATE running → completed     ← ה-slot התפנה
                                                 ← ensure נכנס, תופס running
                SELECT next queued → promote    ← unique_violation, קורס
```
השיחה החדשה עוקפת את התור. ה-advisory lock סוגר את החלון.

### התנהגות לפי `source`

| `source` | יש call פעיל | תוצאה |
|----------|--------------|-------|
| כל אחד | לא | **201** `CALL_CREATED` |
| `trigger` | כן | **202** `CALL_QUEUED` (לפי `priority`) |
| `scheduler` / `api` | כן | **409** `CALL_ALREADY_ACTIVE` — **נחסם** |

**למה תזמון לא נכנס לתור:** תרחיש שרץ חצי שעה היה צובר עשרות calls מתוזמנים, וכולם היו נורים ברצף בסיום. מפולת. טריגר הוא תגובה להודעה אמיתית — הוא כן ממתין.

---

## 4. Endpoints

| נתיב | קורא | הערה |
|------|------|------|
| `POST /incoming` | HostAgent | ה-callback היחיד |
| `POST /send/{phone_id}` | Worker | |
| `POST /events` · `/leaves` · `PATCH /leaves/{id}/status` | Worker | |
| `POST /workers/heartbeat` | Worker | **היה `/heartbeat` = 404** |
| `POST /calls/{id}/summary` | Worker | **סוגר call + מקדם תור** |
| `POST /api/calls/ensure` | Scheduler + incoming | |
| `POST /api/calls/{id}/complete` | ידני | |
| `POST /api/dispatch` · `/dispatch/message` | legacy | |

---

## 5. חוזי ה-payload

### HostAgent → Spine (`WebhookDispatchPayload`, PascalCase)
```json
{ "MessageId": "uuid", "PhoneId": "uuid", "ContactId": "uuid", "Direction": true }
```
`Direction` נגזר מ-`DispatchAsync(..., isIncoming)`:
- `true` → **נכנסת**
- `false` → **יוצאת** (fromMe)

> ה-logger ב-`WebhookDispatcherService` מדפיס `"outgoing"` כש-`Direction=true` — **ההדפסה הפוכה, לא הערך.**

### Spine → Worker (`WorkerEventEnvelope`)
```json
{ "typeEvent": "init" | "entryMessage",
  "call_id": "uuid", "scenario_id": "uuid",
  "contact_id": "uuid", "contact_phone": "", "contact_name": "",
  "scenario_json": "<JSON string>", "first_message": {} }
```
`HandleInit` מוודא `call_id`. **`HandleEntryMessage` לא** — ולכן `call_id` חסר שם נכשל בשקט.

---

## 6. הסכמה — נקודות שנשרפות עליהן

| מה שנראה הגיוני | האמת |
|------------------|------|
| `contacts.phone` | **`contacts.number`** (+ `lid`) |
| `scenarios.is_published` | **`scenarios.status = 'active'`** |
| `scenarios.auto_trigger_enabled` | **לא קיים.** הקישור הוא `(phone_id, contact_id)` |
| `schedules.next_run_at` | **`schedules.next_run`** |
| `schedules.interval_seconds` | **`cron_expr`** (JSON string) |
| `spine_calls` | **לא קיימת מעולם.** שדות ה-summary → `calls` |
| `calls.id` הוא text | **`uuid`** |

**`contacts.tag = 'active'` ≠ `scenarios.status = 'active'`.** אותה מילה, שתי משמעויות.

---

## 7. הבאגים שנמצאו

| # | באג | השפעה |
|---|-----|-------|
| 1 | `spine_webhooks` לעולם לא מתמלאת | **אפס רישומים.** אין הודעות נכנסות, כל שליחה 404 |
| 2 | `incoming` דרש `contact_phone`, קיבל IDs | **422 על כל הודעה** |
| 3 | `/workers/heartbeat` היה `/heartbeat` | `phone_workers.status` לא עודכן → **אף dispatch לא נשלח** |
| 4 | `spine_calls` לא קיימת | **כל summary נכשל בשקט** |
| 5 | `dispatch` ייצר `call_id` בלי INSERT | ה-call לא היה קיים ב-DB |
| 6 | `incoming` סינן על `auto_trigger_enabled` | **אף טריגר לא נורה** |
| 7 | `contacts.phone` → `None` | `contact_phone=""` → **כל תרחיש מת בצעד הראשון** |
| 8 | `dispatch.router` לא רשום ב-`main.py` | **Scheduler קיבל 404 בכל ירייה** |
| 9 | `direction` הפוך בין Spine ל-HostAgent | בועות בצד הלא נכון |
| 10 | `call_id` חסר ב-`entryMessage` | ה-Worker מנתב בלי הקשר לשיחה |
| 11 | `str(config)` במקום `json.dumps` | **JSON לא חוקי** |
| 12 | הודעה יוצאת מדליקה תרחיש | **לולאת feedback אינסופית** |

---

## 8. מיגרציה

### SQL — לפי הסדר
```
1. 00_schema.sql       עמודות + אינדקסים + unique על webhook_registrations
2. ensure_call.sql     advisory lock + spine_ensure_call + spine_complete_call
3. schedules_v2.sql    spine_compute_next_run + claim + close + schedule_calls
```

### קוד
| קובץ | יעד |
|------|-----|
| `main.py` | `spine/main.py` |
| `services_calls.py` | `spine/services/calls.py` ← **חדש** |
| `dispatch.py` `incoming.py` `send.py` `worker_events.py` | `spine/routers/` |
| `scheduler_main.py` | `scheduler/main.py` |

**חובה:** `touch spine/services/__init__.py`

### מחיקה
```bash
rm spine/routers/calls.py          # מוזג ל-dispatch + worker_events
rm spine/routers/webhook.py        # מוזג ל-worker_events
rm spine/routers/webhooks.py       # spine_webhooks מת
rm spine/routers/conversations.py  # read API — נצרך מה-DB
rm spine/routers/notifications.py  # read API + טבלה שאף אחד לא כותב אליה
rm spine/routers/calls_ensure.py   # טיוטה
rm active_chats.py
```

### `.env`
```
SPINE_CALLBACK_URL=http://10.186.0.3:8001
HOST_AGENT_URL=http://10.186.0.2:5000
HOST_AGENT_SEND_PATH=/api/messages/{phone_id}/send/{type}
WEBHOOK_TYPE=trigger
```

---

## 9. אימות אחרי deploy

```bash
# ImportError?  (services/__init__.py חסר)
docker service logs scenario_data-spine 2>&1 | grep -iE "importerror|modulenotfound"

# מישור ה-HostAgent
docker service logs scenario_data-spine 2>&1 | grep WEBHOOK
# צפוי: [WEBHOOK] Upserted. url=http://10.186.0.3:8001/incoming type=trigger

# מישור ה-Worker
docker service logs scenario_data-spine 2>&1 | grep heartbeat

# ה-HostAgent מגיע?  (מ-10.186.0.2)
curl -s http://10.186.0.3:8001/

# הדליקו הודעה אמיתית:
journalctl -u whatsapp-manager.service -f | grep DISPATCH
# צפוי: [DISPATCH][TRIGGER] → ... status=200
```

| תסמין | סיבה |
|-------|------|
| `status=422` | הסכמה לא תואמת |
| אין `[DISPATCH]` בכלל | `WEBHOOK_TYPE` לא תואם ל-enum |
| אין heartbeats | ה-Worker לא מגיע ל-Spine |

---





##LOGS

```bash
docker service logs scenario_scheduler --since 10m --tail 200

## compile

python3 -m compileall spine

כדאי עכשיו לבדוק שה־Task החדש באמת רץ:
```bash
docker service ps scenario_scheduler --no-trunc

```bash
docker service inspect scenario_scheduler \
  --format '{{json .UpdateStatus}}'
בדיקת LATEST IMAGES
```bash
docker service inspect scenario_scheduler \
  --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'


Compile on docker

```bash
docker run --rm \
  --env-file .env \
  -e SPINE_URL=http://scenario_data-spine:8000 \
  --entrypoint python \
  10.186.0.3:5000/scheduler:latest \
  -c "import main; print('OK')"







