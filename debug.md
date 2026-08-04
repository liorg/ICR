

## sql
```bash
-- ═══════════════════════════════════════════════════════════════════════
-- מי משתמש ב-bot_config_int / safe_int / bot_config
--
-- סורק את כל הפונקציות, ה-views וה-triggers ב-DB ומחזיר כל מקום
-- שמזכיר אותן. הרץ ב-Supabase SQL Editor.
-- ═══════════════════════════════════════════════════════════════════════
-- פונקציות שקוראות ל-bot_config_int / safe_int / bot_config
select
    p.proname as function_name,
    pg_get_function_arguments(p.oid) as arguments
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.prokind = 'f'                       -- ← רק פונקציות רגילות, לא aggregates
  and p.proname not in ('bot_config_int', 'safe_int')
  and pg_get_functiondef(p.oid) ilike any (array['%bot_config%', '%safe_int%'])
order by p.proname;

```
TEST LEAF

```bash
select config->'canvas'->1->>'value' as expected_value,
       length(config->'canvas'->1->>'value') as len
from scenarios where id = 'cc43576c-42a3-425c-a833-589d91597b78';


```
DELETE ALL WORKERSS
```bash
docker service ls --format '{{.Name}}' | grep '^worker' | xargs -r docker service rm
```


GET LATEST 
```bash
curl -s "https://hub.docker.com/v2/repositories/liorgr/worker-scenario-runtime/tags/?page_size=10" \
  | python3 -c "import sys,json; [print(t['name'], t['last_updated']) for t in json.load(sys.stdin)['results']]"
```
  מכונה 2 (הספיין) — מה שולחים ל-HostAgent:

```bash
docker service logs scenario_data-spine --since 3m 2>&1 | grep -iE "Send ok|Send rejected|jid=" | tail
```
מכונה 1 (HostAgent) — מה Baileys מדווח:

```bash
journalctl -u whatsapp-manager.service --since "3 min ago" | grep -E "MSG-RAW|Send|err
```
כגדד
```bash
update scenarios
set config = jsonb_set(config, '{canvas,1,value}', '"שלום"')
where id = 'cc43576c-42a3-425c-a833-589d91597b78';
```
```bash
select column_name from information_schema.columns
where table_name = 'spine_leaf_messages';
```
docker service logs scenario_data-spine --since 10m 2>&1 | grep -i INCOMING
```bash
curl -s &quot;https://hub.docker.com/v2/repositories/liorgr/worker-scenario-runtime/tags/?page_size=25&quot; \
  | python3 -c &quot;import sys,json; d=json.load(sys.stdin); print(d.get(&apos;count&apos;)); [print(t[&apos;name&apos;], t[&apos;last_updated&apos;]) for t in d.get(&apos;results&apos;,[])]&quot;
```

```bash
$ docker service logs worker-972504476645-3beff8fa --since 20m 2>&1 | grep -E "Reply|mismatch|Step|Skip"
```

```bash
update calls set status = 'aborted', ended_at = now()
```

```bash
docker service rm worker-972504477197-1ff94cfa

```
```bash
cd /opt/ICR && ./deploy.sh

```
```bash
grep -A12 "provisioner:" /opt/ICR/docker-compose.yml
```

## bash

# log worker
```bash
docker service logs worker-972504476645-3beff8fa --since 3m 2>&1 | tail -40
docker service logs worker-972504477197-1ff94cfa --since 3m 2>&1 | tail -40

```
צריך את הלוג של ה-worker:
זה מצביע על כך שה-webhook הנכנס לא הגיע ל-worker, או הגיע ולא הותאם ל-session.

צריך לראות את מסע ה-"שלום" מהצד הנכנס:

```bash
docker service logs scenario_data-spine --since 5m 2>&1 | grep -iE "INCOMING|Reply|deliver|route" | tail -15
```
```bash
docker service logs worker-972504476645-3beff8fa --since 5m 2>&1 | grep -vE "DBG" | grep -E "Step|Wait|Reply|expect|s48e776|Compiled"

```
צריך לראות מה קרה בצד ה-HostAgentכשה-"שלום" הגיע ב-09:57:42:

```bash
journalctl -u whatsapp-manager.service --since "09:57:00" --until "09:58:30" | grep -iE "MSG-RAW|DISPATCH|incoming"
```

# log spine
```bash
docker service logs scenario_data-spine --since 2m 2>&1 | grep -E "ENSURE|WORKER"
```
```bash
docker service logs scenario_data-spine --since 10m 2>&1 | grep -A15 "Failed linking outgoing" | tail -20
```
```bash
docker exec $(docker ps -q -f name=scenario_data-spine) pip list 2>/dev/null | grep -Ei "postgrest|supabase"

```
דגדשגג
גק
גק
```bash

docker service logs -f $(docker service ls --format '{{.Name}}' | grep worker)
```
```bash

docker service logs scenario_provisioner --since 2m 2>&1 | tail -20
```


docker service logs scenario_data-spine --since 5m 2>&1 | grep -A5 "APIError\|PGRST" | tail -40
## LIST workers
```bash
docker service ls --format '{{.Name}}' | grep '^worker' 
```
## DELETES ALL workers
```bash
docker service ls --format '{{.Name}}' | grep '^worker' | xargs -r docker service rm
```
GET VERSION
```bash
docker service inspect worker-972504476645-3beff8fa \
  --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'
```

עם ה-Provisioner המתוקן אתה לא צריך לגעת ב-phone_workers — הוא יזהה שהשירות חסר למרות ש-status='running' ויקים אותו מחדש תוך 30 שניות, הפעם עם WA_FASTAPI_URL.

מעקב:

```bash
watch -n 3 "docker service ls --format '{{.Name}} {{.Replicas}}' | grep worker"
```

וכשהוא 1/1, האימות שכל זה היה בשבילו:

```bash
docker service inspect $(docker service ls --format '{{.Name}}' | grep '^worker') \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' | grep WA_FASTAPI
```
ואז ensure — וההודעה HELL LIOR צריכה סוף סוף לצאת לטלפון.
docker service logs worker-972504476645-3beff8fa --since 3m 2>&1 | grep -iE "Send|error|fail|leaf" | head -15
docker service logs scenario_data-spine --since 3m 2>&1 | grep -iE "send|POST /send" | tail -10
```bash

select p.oid::regprocedure as signature
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('spine_complete_call', 'spine_ensure_call');

```

##post

## ensure

```bash
 curl -i -X POST http://10.186.0.3:8001/incoming \
  -H "Content-Type: application/json" \
  -d '{"messageId":"a10a83f3-8cd6-4d79-93b8-40142d4a138d","whatsAppMessageId":"3EB0A6EA7396598E54AB49","phoneId":"3beff8fa-4dc6-4a03-b70f-17a47fe09529","contactId":"f496e6e0-0d7b-42e9-a9df-f65df23a0042","direction":false}'
```
```bash
curl -s -X POST http://10.186.0.3:8001/api/calls/88abb689-2f39-40c1-b4df-236e9a98113c/complete \
  -H "Content-Type: application/json" -d '{"status":"failed"}'
```
```bash
curl -s -X POST http://10.186.0.3:8001/api/calls/ensure \
  -H "Content-Type: application/json" \
  -d '{"phone_id":"3beff8fa-4dc6-4a03-b70f-17a47fe09529","contact_id":"c227fb24-5e50-4e48-b863-80c2e1112af7","scenario_id":"cc43576c-42a3-425c-a833-589d91597b78","source":"api"}' \
  | python3 -m json.tool | head -4
```
docker service ls --format '{{.Name}} {{.Replicas}}' | grep worker

post supabase call function spine_ensure_call

```bash
curl -i -X POST "https://umxgluptdopldndqjbvx.supabase.co/rest/v1/rpc/spine_ensure_call" \
  -H "apikey: $SUPABASE_SERVICE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"p_phone_id":"1ff94cfa-a381-4606-8bbc-f0d36abe8005","p_contact_id":"6217f567-2931-4157-a7ae-1521f53a5f9e","p_scenario_id":"df3b78c2-ac5f-4c8a-be39-cd7ff32da3ea","p_source":"api"}'
```
post swagger
```bash
curl -s http://10.186.0.2:5000/swagger/v1/swagger.json | python3 -c "
import sys,json
for p in json.load(sys.stdin)['paths']:
    if 'send' in p: print(p)
"
```
השירות קיים ורץ (scenario_scheduler, 1/1). עכשיו הלוגים שלו — זו הפעם הראשונה שנסתכל עליהם:

```bash
docker service logs scenario_scheduler --since 2h 2>&1 | tail -50
```
ואם יש הרבה רעש, ממוקד```:

```bash
docker service logs scenario_scheduler --since 2h 2>&1 | grep -aE "Found|Processing|fired|done|failed|error|Traceback" | tail -30

```

# איפה שירות ה-Deno?

```bash
docker service ls | grep -i deno ; docker ps | grep -i deno

```
# לאן ה-Worker מצביע?

```bash
docker service inspect worker-972504476645-3beff8fa \
  --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' | tr ',' '\n' | grep -i deno
```


# מה השגיאההמדויקת בצעד? (השורות שלפני ה-Send שנכשל)
```bash
docker service logs worker-972504476645-3beff8fa --since 30m 2>&1 | grep -aiB 2 -A 5 "deno\|card_sender\|vkmqply" | tail -30


```
