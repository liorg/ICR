package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"
)

var (
	supabaseURL string
	supabaseKey string
	network     string
	spineURL    string
	workerImage string
)

type Phone struct {
	ID     string `json:"id"`
	Number string `json:"number"`
	Status string `json:"status"`
}

type PhoneWorker struct {
	PhoneID     string `json:"phone_id"`
	ServiceName string `json:"service_name"`
	Status      string `json:"status"`
}

func main() {
	supabaseURL = mustEnv("SUPABASE_URL")
	supabaseKey = mustEnv("SUPABASE_SERVICE_KEY")
	network = envOr("SWARM_NETWORK", "scenario_spine-net")
	spineURL = envOr("SPINE_URL", "http://scenario_data-spine:8000")
	workerImage = envOr("WORKER_IMAGE", "liorgr/worker-scenario-runtime:latest")

	log.Printf("Provisioner started | network=%s spine=%s image=%s", network, spineURL, workerImage)

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

	phonesRaw := supabaseGet("phones?select=id,number,status&status=eq.active")
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

	existing := map[string]*PhoneWorker{}
	for i := range workerList {
		existing[workerList[i].PhoneID] = &workerList[i]
	}

	log.Printf("Reconcile | phones=%d workers=%d", len(phoneList), len(workerList))

	for _, phone := range phoneList {
		w, exists := existing[phone.ID]
		if exists && w.Status == "running" {
			// ה-DB אומר running — אבל אם השירות נמחק ידנית,
			// דילוג כאן משאיר את המערכת תקועה לצמיתות:
			// אין worker, וה-Spine שולח ל-DNS מת.
			if err := exec.Command("docker", "service", "inspect", w.ServiceName).Run(); err == nil {
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

		log.Printf("Creating worker | phone=%s number=%s service=%s", phone.ID, cleanNum, svcName)

		err := createService(svcName, phone.ID, cleanNum)
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

func createService(name, phoneID, phoneNumber string) error {
	if err := exec.Command("docker", "service", "inspect", name).Run(); err == nil {
		log.Printf("Service %s already exists", name)
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
		workerImage,
	}

	out, err := exec.Command("docker", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %s", err, string(out))
	}
	log.Printf("docker service create output: %s", string(out))
	return nil
}

func supabaseGet(path string) []byte {
	url := supabaseURL + "/rest/v1/" + path
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		log.Printf("Request build error: %v", err)
		return []byte("[]")
	}
	req.Header.Set("apikey", supabaseKey)
	req.Header.Set("Authorization", "Bearer "+supabaseKey)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("Supabase GET error: %v", err)
		return []byte("[]")
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("Body read error: %v", err)
		return []byte("[]")
	}

	if resp.StatusCode != 200 {
		log.Printf("Supabase %d: %s", resp.StatusCode, string(body))
		return []byte("[]")
	}

	return body
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
