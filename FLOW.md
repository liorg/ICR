# ICR — זרימת calls

## הקבצים

| קובץ | יעד | סדר |
|---|---|---|
| `spine_ensure_call.sql` | Supabase SQL Editor | 1 |
| `Complete_call.sql` | Supabase SQL Editor | 2 |
| `calls.py` | `spine/services/calls.py` | 3 |
| `incoming.py` | `spine/routers/incoming.py` | 3 |
| `dispatch.py` | `spine/routers/dispatch.py` | 3 |
| `worker_events.py` | `spine/routers/worker_events.py` | 3 |

SQL קודם. הפוך — ה-Python יקרא ל-RPC שעוד לא קיים.
`Complete_call.sql` משתמש ב-`spine_sla_deadline` שנוצר בקובץ הראשון, אז הסדר ביניהם חשוב.

אחרי הפריסה: **job חדש ב-Scheduler** — `POST {SPINE}/api/calls/sweep` כל דקה.

---

## כלל הבסיס

לכל קונטקט יש **`running` אחד לכל היותר**.

תור (`queued`) קיים אך ורק בתוך באטץ' של triggers מאותה הודעה נכנסת,
ומתרוקן דרך `spine_complete_call`.

`spine_ensure_call` היא נקודת היצירה היחידה. שני מצבי הפעלה, אותו תהליך:

```
p_scenario_id = null  →  מצב trigger. ה-RPC בוחר בעצמו את כל תרחישי
                         ה-trigger הפעילים של הטלפון.
p_scenario_id = uuid  →  תרחיש בודד (scheduler / api / manual).
```

---

## הודעה נכנסת — `POST /incoming`

```
1. _load_message        אימות מול messages לפי 4 מזהים. לא קיים → 404
2. direction?
     outgoing → 🏁🏁 ? עדכון last_* : קישור leaf ל-message  → סוף
     incoming ↓
3. _normalize_message   בניית first_message
4. _get_active_call     יש running?
     כן → send_to_worker(entryMessage)     ← חלק משיחה קיימת
     לא → אין ניתוב
5. ensure_call(scenario_id=None, source="trigger")
6. אם נוצר running → send_to_worker(init) עם first_message
```

שלב 4 ושלב 5 מסתכלים על אותו קונטקט אבל שואלים שאלות שונות: הראשון
"למי לנתב את ההודעה", השני "האם היא פותחת שיחה". רק אחד מהם יכול לפעול,
וזו ההבחנה בין "חלק משיחה" ל-trigger.

---

## `spine_ensure_call` — הלוגיקה

```
1. contact.tag = 'active' ?           לא → denied / CONTACT_NOT_ACTIVE
2. תרחיש בודד? אימות שהוא active      לא → error / SCENARIO_NOT_FOUND_OR_INACTIVE
3. 🔒 advisory lock (phone_id, contact_id)
4. יש call פתוח (running או queued)?
       כן → טבלת המדיניות למטה
       לא → יצירה
5. יצירה — לולאה אחת לשני המצבים:
       הראשון לפי priority  → running + started_at + expected_end
       כל השאר              → queued  (בלי SLA)
       כולם                 → אותו first_message ואותו event_id
```

### מדיניות מול call פתוח

סדר הבדיקה לפי חשיבות ה-source — `scheduler` ראשון.

| source | תוצאה | status_reason | HTTP |
|---|---|---|---|
| `scheduler`, וה-`running` הוא scheduler | שורת `aborted` + ריקון התור | `SCHEDULER_INSTANCE_EXISTS` | 409 |
| `scheduler`, אחרת | שורת `aborted` + ריקון התור | `ACTIVE_CALL_EXISTS` | 409 |
| `trigger` | denied, **בלי שורה בכלל** | — | 409 |
| `api` / `manual` / אחר | שורת `aborted` + ריקון התור | `ACTIVE_CALL_EXISTS` | 409 |

**אף source יזום לא נכנס לתור.** בנוסף לפסילה של עצמו הוא מבטל את כל
שורות ה-`queued` של הקונטקט ל-`aborted` עם
`status_reason = PREEMPTED_BY_<SOURCE>` — מה שנקבע לרוץ אחרי ה-`running`
כבר לא רלוונטי ברגע שהגיעה הפעלה יזומה.

**ה-`running` לא מופסק בשום מקרה** — עד `summary` או עד ה-sweeper.

שורות `aborted` נרשמות לתיעוד בלבד: בלי `started_at`, בלי `expected_end`.
ה-SLA מעולם לא התחיל, וה-sweeper לא ייגע בהן.

---

## סיום — `POST /calls/{id}/summary`

```
1. אימות ה-call
2. שמירת סטטיסטיקות הסיכום
3. complete_call → spine_complete_call 🔒
       סוגר את ה-running
       מקדם את ה-queued הבא לפי priority → running
       קובע לו started_at + expected_end
4. init ל-call שקודם, עם ה-first_message ששמור על השורה שלו
5. חותמת סיום 🏁🏁 → חוזרת דרך /incoming כ-outgoing
```

זו הנקודה **היחידה** שמקדמת את התור בסיום תקין. לפני התיקון ה-summary
עשה `update` ישיר, ושורות `queued` נשארו מתות לנצח — מצב שחוסם את
הקונטקט לצמיתות: בלי `running` הודעה נכנסת לא מנותבת, ועם `queued` כל
trigger נדחה.

---

## רשת ביטחון — `POST /calls/sweep`

ה-Worker הוא היחיד שסוגר call. אם הוא קרס, נהרג או איבד קשר — ה-call
נשאר `running` לנצח והקונטקט חסום.

```
running עם expected_end < now()
  → complete_call(status='expired')
  → אותו קידום כמו בסיום תקין
```

`expected_end` = `estimated_time.totalSeconds` מה-snapshot
(ברירת מחדל `sla.default_estimated_seconds`) + `sla.buffer_seconds`.
מחושב ב-`spine_sla_deadline`.

ה-Scheduler קורא. Spine מגיב, לא יוזם.

---

## SLA

`started_at` ו-`expected_end` נקבעים **אך ורק** ברגע המעבר ל-`running`:

- ביצירה — רק לשורה הראשונה
- בקידום — ב-`spine_complete_call`

שורת `queued` נוצרת בלי שניהם. השעון מתחיל כשהתרחיש מתחיל, לא כשהוא
נכנס לתור.

---

## עמודות חדשות ב-`calls`

| עמודה | תוכן |
|---|---|
| `first_message` | ההודעה הנכנסת שיצרה את ה-call |
| `first_message_at` | מתי נרשמה |
| `event_id` | GUID משותף לכל השורות שנוצרו באותה קריאה |
| `status_reason` | למה ה-call הגיע לסטטוס הנוכחי |

`event_id` הוא איך שולפים באטץ' שלם:

```sql
select id, scenario_id, status, status_reason, priority, started_at
  from calls
 where event_id = '...'
 order by priority, created_at;
```

`status_reason` מבדיל בין `aborted` של קדימות לבין כשל אמיתי — בלעדיו
אי אפשר לדעת מדוח למה תזמון לא רץ.

---

## `priority`

מסדר באטץ' של triggers: כשיש כמה תרחישי `event_type='trigger'` על אותו
טלפון, הוא קובע מי רץ ראשון ומי ממתין בתור. מספר נמוך = קודם.

בתרחיש בודד הוא נשמר לתיעוד ולא משפיע על כלום.

---

## אחרי הפריסה — לבדוק

**1. שהפונקציה הישנה נמחקה**

```sql
select p.oid::regprocedure
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public' and p.proname like 'spine_%';
```

`spine_ensure_trigger_calls` לא אמור להופיע. שתי חתימות של אותה
פונקציה → PostgREST מחזיר PGRST203, צריך `drop` ידני.

**2. יתומים מלפני התיקון** — `queued` בלי `running`

```sql
select phone_id, contact_id, count(*) as queued
  from calls
 group by phone_id, contact_id
having count(*) filter (where status = 'running') = 0
   and count(*) filter (where status = 'queued')  > 0;
```

אם חוזרות שורות — אלה קונטקטים חסומים. צריך לשחרר ידנית.

**3. `check constraint` על `calls.status`** — משתמשים רק ב-`aborted`
ו-`expired` הקיימים. אם יש constraint שדורש `started_at not null` על
שורות סגורות, ה-INSERT של שורות `aborted` ייפול.

---

## פתוח

**תזמון שנפל על שיחה פעילה לא ירוץ כלל.** הוא נרשם `aborted` ונעלם —
לא באיחור, לא בניסיון חוזר. אם ה-Scheduler מקדם את `next_run` אחרי
שהוא מקבל תשובה מ-Spine, התזמון פשוט דולג. אם צריך ניסיון חוזר, זה
שינוי בצד ה-Scheduler: כשמגיע 409 עם `ACTIVE_CALL_EXISTS`, לדחות את
`next_run` בכמה דקות במקום לקדם למחזור הבא.

**React** — `first_message`, `event_id`, `status_reason` נשמרים ולא
מוצגים בשום מסך. `status_reason` בטבלת ה-calls הוא כנראה השימושי ביותר.
