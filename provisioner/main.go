package main

import (
	"encoding/json"
	"fmt"
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
	ID          string `json:"id"`
	PhoneNumber string `json:"phone_number"`
	Status      string `json:"status"`
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

	reconcile()
	for range time.Tick(30 * time.Second) {
		reconcile()
	}
}

func reconcile() {
	phones := supabaseGet("phones?select=id,phone_number,status&status=eq.active")
	var phoneList []Phone
	json.Unmarshal(phones, &phoneList)

	workers := supabaseGet("phone_workers?select=phone_id,service_name,status")
	var workerList []PhoneWorker
	json.Unmarshal(workers, &workerList)

	existing := map[string]*PhoneWorker{}
	for i := range workerList {
		existing[workerList[i].PhoneID] = &workerList[i]
	}

	for _, phone := range phoneList {
		w, exists := existing[phone.ID]
		if exists && w.Status == "running" {
			continue
		}

		shortID := phone.ID
		if len(shortID) > 8 {
			shortID = shortID[:8]
		}
		svcName := fmt.Sprintf("worker-%s-%s", phone.PhoneNumber, shortID)

		log.Printf("Creating worker | phone=%s number=%s service=%s", phone.ID, phone.PhoneNumber, svcName)

		err := createService(svcName, phone.ID, phone.PhoneNumber)
		if err != nil {
			log.Printf("ERROR create %s: %v", svcName, err)
			upsertWorker(phone.ID, svcName, "error")
			continue
		}

		upsertWorker(phone.ID, svcName, "running")
		log.Printf("Worker created | service=%s", svcName)
	}

	// Remove workers for inactive phones
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
}

func createService(name, phoneID, phoneNumber string) error {
	// Check if exists
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
		"--env", "DENO_BIN=/usr/local/bin/deno",
		"--env", "DENO_TMPDIR=/tmp/deno-scripts",
		workerImage,
	}

	out, err := exec.Command("docker", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %s", err, string(out))
	}
	return nil
}

// ── Supabase HTTP ────────────────────────────────────────────────────────

func supabaseGet(path string) []byte {
	url := supabaseURL + "/rest/v1/" + path
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("apikey", supabaseKey)
	req.Header.Set("Authorization", "Bearer "+supabaseKey)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("Supabase GET error: %v", err)
		return []byte("[]")
	}
	defer resp.Body.Close()

	body := make([]byte, 0)
	buf := make([]byte, 4096)
	for {
		n, err := resp.Body.Read(buf)
		body = append(body, buf[:n]...)
		if err != nil {
			break
		}
	}
	return body
}

func upsertWorker(phoneID, serviceName, status string) {
	// Check if exists
	data := supabaseGet("phone_workers?select=phone_id&phone_id=eq." + phoneID)
	exists := len(data) > 5 // more than "[]"

	if exists {
		// PATCH
		url := supabaseURL + "/rest/v1/phone_workers?phone_id=eq." + phoneID
		payload := fmt.Sprintf(`{"service_name":"%s","status":"%s","updated_at":"%s"}`,
			serviceName, status, time.Now().UTC().Format(time.RFC3339))
		req, _ := http.NewRequest("PATCH", url, strings.NewReader(payload))
		req.Header.Set("apikey", supabaseKey)
		req.Header.Set("Authorization", "Bearer "+supabaseKey)
		req.Header.Set("Content-Type", "application/json")
		http.DefaultClient.Do(req)
	} else {
		// POST
		url := supabaseURL + "/rest/v1/phone_workers"
		payload := fmt.Sprintf(`{"phone_id":"%s","service_name":"%s","status":"%s","replicas":1,"image":"%s"}`,
			phoneID, serviceName, status, workerImage)
		req, _ := http.NewRequest("POST", url, strings.NewReader(payload))
		req.Header.Set("apikey", supabaseKey)
		req.Header.Set("Authorization", "Bearer "+supabaseKey)
		req.Header.Set("Content-Type", "application/json")
		http.DefaultClient.Do(req)
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
