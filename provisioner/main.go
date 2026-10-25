package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"
)

var (
	supabaseURL string
	supabaseKey string
	network     string
	spineURL    string
	workerImage string
	baseTrusted []string // env TRUSTED_DOMAINS — גלובלי לכל ה-workers
)

// אותו regex כמו ב-SQL / Python / C#. מגן על ה-env וה-label של docker. agaig retry again
var domainRe = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$`)

type Phone struct {
	ID     string `json:"id"`
	Number string `json:"number"`
	Status string `json:"status"`
	UserID string `json:"user_id"`
}

type PhoneWorker struct {
	PhoneID     string `json:"phone_id"`
	ServiceName string `json:"service_name"`
	Status      string `json:"status"`
}

type TrustedDomain struct {
	UserID string `json:"user_id"`
	Domain string `json:"domain"`
}

func main() {
	supabaseURL = mustEnv("SUPABASE_URL")
	supabaseKey = mustEnv("SUPABASE_SERVICE_KEY")
	network = envOr("SWARM_NETWORK", "scenario_spine-net")
	spineURL = envOr("SPINE_URL", "http://scenario_data-spine:8000")
	workerImage = envOr("WORKER_IMAGE", "liorgr/worker-scenario-runtime:latest")
	baseTrusted = parseDomains(strings.Split(os.Getenv("TRUSTED_DOMAINS"), ","))

	log.Printf("Provisioner started | network=%s spine=%s image=%s trusted=%s",
		network, spineURL, workerImage, strings.Join(baseTrusted, ","))

	for {
		func() {
			defer func() {
				if r := recover(); r != nil {
					log.Printf("PANIC in reconcile: %v", r)
				}
			}()
			reconcile()
		}()
		time.Sleep(30 * time.Second)
	}
}

func reconcile() {
	log.Println("Reconcile starting...")

	phonesRaw := supabaseGet("phones?select=id,number,status,user_id&status=eq.active")
	log.Printf("Phones response: %s", string(phonesRaw))

	var phoneList []Phone
	if err := json.Unmarshal(phonesRaw, &phoneList); err != nil {
		log.Printf("ERROR unmarshal phones: %v | raw: %s", err, string(phonesRaw))
		return
	}

	workersRaw := supabaseGet("phone_workers?select=phone_id,service_name,status")
	var workerList []PhoneWorker
	if err := json.Unmarshal(workersRaw, &workerList); err != nil {
		log.Printf("ERROR unmarshal workers: %v", err)
		workerList = []PhoneWorker{}
	}

	trustedByUser, trustedOK := loadTrusted()

	existing := map[string]*PhoneWorker{}
	for i := range workerList {
		existing[workerList[i].PhoneID] = &workerList[i]
	}

	log.Printf("Reconcile | phones=%d workers=%d trusted_users=%d", len(phoneList), len(workerList), len(trustedByUser))

	for _, phone := range phoneList {
		trusted := trustedFor(phone.UserID, trustedByUser)

		w, exists := existing[phone.ID]
		if exists && w.Status == "running" {
			// ה-DB אומר running — אבל אם השירות נמחק ידנית,
			// דילוג כאן משאיר את המערכת תקועה לצמיתות:
			// אין worker, וה-Spine שולח ל-DNS מת.
			if err := exec.Command("docker", "service", "inspect", w.ServiceName).Run(); err == nil {
				// כשל בקריאת trusted_domains → לא נוגעים (לא מוחקים הרשאות בגלל תקלת רשת)
				if trustedOK {
					syncTrusted(w.ServiceName, trusted)
				}
				continue
			}
			log.Printf("Service %s marked running but missing — recreating", w.ServiceName)
		}

		cleanNum := strings.TrimPrefix(phone.Number, "+")
		shortID := phone.ID
		if len(shortID) > 8 {
			shortID = shortID[:8]
		}
		svcName := fmt.Sprintf("worker-%s-%s", cleanNum, shortID)

		log.Printf("Creating worker | phone=%s number=%s service=%s trusted=%s", phone.ID, cleanNum, svcName, trusted)

		err := createService(svcName, phone.ID, cleanNum, trusted)
		if err != nil {
			log.Printf("ERROR create %s: %v", svcName, err)
			upsertWorker(phone.ID, svcName, "error")
			continue
		}

		upsertWorker(phone.ID, svcName, "running")
		log.Printf("Worker created | service=%s", svcName)
	}

	activeMap := map[string]bool{}
	for _, p := range phoneList {
		activeMap[p.ID] = true
	}
	for _, w := range workerList {
		if !activeMap[w.PhoneID] && w.Status == "running" {
			log.Printf("Removing %s", w.ServiceName)
			exec.Command("docker", "service", "rm", w.ServiceName).Run()
			upsertWorker(w.PhoneID, w.ServiceName, "stopped")
		}
	}

	log.Println("Reconcile done")
}

// ── trusted domains ───────────────────────────────────────────────────────

func parseDomains(raw []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, p := range raw {
		d := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(p)), ".")
		if d == "" || seen[d] {
			continue
		}
		if len(d) > 253 || !domainRe.MatchString(d) {
			log.Printf("WARN dropping invalid trusted domain %q", p)
			continue
		}
		seen[d] = true
		out = append(out, d)
	}
	sort.Strings(out)
	return out
}

// רק verified ושהתעודה עוד בתוקף
func loadTrusted() (map[string][]string, bool) {
	now := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	raw, ok := supabaseGetOK("trusted_domains?select=user_id,domain&status=eq.verified&cert_expires_at=gt." + now)
	if !ok {
		return nil, false
	}
	var rows []TrustedDomain
	if err := json.Unmarshal(raw, &rows); err != nil {
		log.Printf("ERROR unmarshal trusted_domains: %v", err)
		return nil, false
	}
	byUser := map[string][]string{}
	for _, r := range rows {
		byUser[r.UserID] = append(byUser[r.UserID], r.Domain)
	}
	return byUser, true
}

func trustedFor(userID string, byUser map[string][]string) string {
	all := append([]string{}, baseTrusted...)
	if userID != "" {
		all = append(all, byUser[userID]...)
	}
	return strings.Join(parseDomains(all), ",")
}

// label trusted-domains = מה שה-worker רץ איתו כרגע. שונה → service update (restart של ה-worker)
func syncTrusted(svcName, trusted string) {
	out, err := exec.Command("docker", "service", "inspect", "--format",
		`{{index .Spec.Labels "trusted-domains"}}`, svcName).Output()
	if err != nil {
		log.Printf("ERROR inspect labels %s: %v", svcName, err)
		return
	}
	current := strings.TrimSpace(string(out))
	if current == "<no value>" {
		current = ""
	}
	if current == trusted {
		return
	}

	log.Printf("Trusted domains changed | service=%s from=%q to=%q", svcName, current, trusted)
	res, err := exec.Command("docker", "service", "update", "--detach",
		"--env-add", "TRUSTED_DOMAINS="+trusted,
		"--label-add", "trusted-domains="+trusted,
		svcName).CombinedOutput()
	if err != nil {
		log.Printf("ERROR update trusted %s: %v | %s", svcName, err, string(res))
	}
}

// ── docker ────────────────────────────────────────────────────────────────

func createService(name, phoneID, phoneNumber, trusted string) error {
	if err := exec.Command("docker", "service", "inspect", name).Run(); err == nil {
		log.Printf("Service %s already exists", name)
		syncTrusted(name, trusted)
		return nil
	}

	args := []string{
		"service", "create",
		"--name", name,
		"--network", network,
		"--replicas", "1",
		"--restart-condition", "on-failure",
		"--label", "managed-by=provisioner",
		"--label", "phone-id=" + phoneID,
		"--label", "phone-number=" + phoneNumber,
		"--label", "trusted-domains=" + trusted,
		"--env", "PHONE_ID=" + phoneID,
		"--env", "PHONE_NUMBER=" + phoneNumber,
		"--env", "SERVICE_NAME=" + name,
		"--env", "PORT=9000",
		"--env", "SPINE_URL=" + spineURL,
		// WhatsAppSender.cs קורא WA_FASTAPI_URL, לא SPINE_URL.
		// בלעדיו הוא נופל ל-http://localhost:8001 וכל שליחה מחזירה 404.
		"--env", "WA_FASTAPI_URL=" + spineURL,
		"--env", "DENO_BIN=/usr/local/bin/deno",
		"--env", "DENO_TMPDIR=/tmp/deno-scripts",
		// Deno --allow-net + בדיקת ה-compiler
		"--env", "TRUSTED_DOMAINS=" + trusted,
		workerImage,
	}

	out, err := exec.Command("docker", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %s", err, string(out))
	}
	log.Printf("docker service create output: %s", string(out))
	return nil
}

// ── supabase ──────────────────────────────────────────────────────────────

func supabaseGet(path string) []byte {
	body, _ := supabaseGetOK(path)
	return body
}

func supabaseGetOK(path string) ([]byte, bool) {
	url := supabaseURL + "/rest/v1/" + path
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		log.Printf("Request build error: %v", err)
		return []byte("[]"), false
	}
	req.Header.Set("apikey", supabaseKey)
	req.Header.Set("Authorization", "Bearer "+supabaseKey)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("Supabase GET error: %v", err)
		return []byte("[]"), false
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("Body read error: %v", err)
		return []byte("[]"), false
	}

	if resp.StatusCode != 200 {
		log.Printf("Supabase %d: %s", resp.StatusCode, string(body))
		return []byte("[]"), false
	}

	return body, true
}

func upsertWorker(phoneID, serviceName, status string) {
	data := supabaseGet("phone_workers?select=phone_id&phone_id=eq." + phoneID)
	exists := len(data) > 5

	var method, url, payload string
	if exists {
		method = "PATCH"
		url = supabaseURL + "/rest/v1/phone_workers?phone_id=eq." + phoneID
		payload = fmt.Sprintf(`{"service_name":"%s","status":"%s","updated_at":"%s"}`,
			serviceName, status, time.Now().UTC().Format(time.RFC3339))
	} else {
		method = "POST"
		url = supabaseURL + "/rest/v1/phone_workers"
		payload = fmt.Sprintf(`{"phone_id":"%s","service_name":"%s","status":"%s","replicas":1,"image":"%s"}`,
			phoneID, serviceName, status, workerImage)
	}

	req, _ := http.NewRequest(method, url, strings.NewReader(payload))
	req.Header.Set("apikey", supabaseKey)
	req.Header.Set("Authorization", "Bearer "+supabaseKey)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("Upsert worker error: %v", err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		log.Printf("Upsert worker %d: %s", resp.StatusCode, string(body))
	}
}

func mustEnv(k string) string {
	v := os.Getenv(k)
	if v == "" {
		log.Fatalf("Missing: %s", k)
	}
	return v
}

func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
