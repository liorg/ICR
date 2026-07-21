

~~bash
curl -i -X POST "https://umxgluptdopldndqjbvx.supabase.co/rest/v1/rpc/spine_ensure_call" \
  -H "apikey: $SUPABASE_SERVICE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"p_phone_id":"1ff94cfa-a381-4606-8bbc-f0d36abe8005","p_contact_id":"6217f567-2931-4157-a7ae-1521f53a5f9e","p_scenario_id":"df3b78c2-ac5f-4c8a-be39-cd7ff32da3ea","p_source":"api"}'
HTTP/2 200 
date: Tue, 21 Jul 2026 12:16:02 GMT
content-type: application/json; charset=utf-8
content-range: 0-0/*
cf-ray: a1ea226e3b3b1170-WAW
cf-cache-status: DYNAMIC
set-cookie: __cf_bm=QeU1ybKDDPHts0yFd0l2FyXlCMln95UTa9e2Prd77os-1784636162.2726858-1.0.1.1-GY2pXHtsYNroDC7ONsqRwaDGZ_PMm3svh7XuPpa.GdedkxydzfeDIUFRdf1ec5zecniUAP1YbXhL0mQBcuEdwZ4DdNG7RD7gWlQaZSk_t6GPZ5gY1LJMHBhmf1JyhyQk; HttpOnly; SameSite=None; Secure; Path=/; Domain=supabase.co; Expires=Tue, 21 Jul 2026 12:46:02 GMT
server: cloudflare
strict-transport-security: max-age=31536000; includeSubDomains; preload
vary: Accept-Encoding
x-content-type-options: nosniff
content-profile: public
sb-gateway-version: 1
sb-project-ref: umxgluptdopldndqjbvx
sb-request-id: 019f849a-c0e4-7ff8-9c1c-7700366cdf9b
x-envoy-attempt-count: 1
x-envoy-upstream-service-time: 45
alt-svc: h3=":443"; ma=86400

{"code": "CALL_ALREADY_ACTIVE", "status": "blocked", "message": "Active call exists; source \"api\" is not queued", "active_since": "2026-07-21T12:13:14.525251", "active_call_id": "f55b9dc5-c8fa-49fe-b8e1-15b3070475b0"}lior_grosman@worker-scenario:/opt/ICR$ 
