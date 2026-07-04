package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/swarm"
	"github.com/docker/docker/client"
	supa "github.com/nedpals/supabase-go"
)

type Phone struct {
	ID          string `json:"id"`
	PhoneNumber string `json:"phone_number"`
	Status      string `json:"status"`
}

type PhoneWorker struct {
	PhoneID     string `json:"phone_id"`
	ServiceName string `json:"service_name"`
	Replicas    int    `json:"replicas"`
	Status      string `json:"status"`
	Image       string `json:"image"`
}

var (
	db          *supa.Client
	docker      *client.Client
	network     string
	spineURL    string
	workerImage string
	interval    = 30 * time.Second
)

func main() {
	db = supa.CreateClient(mustEnv("SUPABASE_URL"), mustEnv("SUPABASE_SERVICE_KEY"))

	var err error
	docker, err = client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		log.Fatalf("Docker: %v", err)
	}
	docker.NegotiateAPIVersion(context.Background())

	network = envOr("SWARM_NETWORK", "scenario_spine-net")
	spineURL = envOr("SPINE_URL", "http://scenario_data-spine:8000")
	workerImage = envOr("WORKER_IMAGE", "liorgr/worker-scenario-runtime:latest")

	log.Printf("Provisioner started | network=%s spine=%s image=%s", network, spineURL, workerImage)

	reconcile()
	for range time.Tick(interval) {
		reconcile()
	}
}

func reconcile() {
	ctx := context.Background()

	// 1. Active phones
	var phones []Phone
	err := db.DB.From("phones").Select("id, phone_number, status").Eq("status", "active").Execute(&phones)
	if err != nil {
		log.Printf("ERROR phones: %v", err)
		return
	}

	// 2. Existing workers
	var workers []PhoneWorker
	_ = db.DB.From("phone_workers").Select("*").Execute(&workers)

	existing := map[string]*PhoneWorker{}
	for i := range workers {
		existing[workers[i].PhoneID] = &workers[i]
	}

	// 3. Create missing workers
	for _, phone := range phones {
		w, exists := existing[phone.ID]

		if exists && w.Status == "running" {
			continue
		}

		// Service name: worker-{phone_number}-{phone_id[:8]}
		shortID := phone.ID
		if len(shortID) > 8 {
			shortID = shortID[:8]
		}
		svcName := fmt.Sprintf("worker-%s-%s", phone.PhoneNumber, shortID)

		log.Printf("Creating worker | phone=%s number=%s service=%s", phone.ID, phone.PhoneNumber, svcName)

		err := createService(ctx, svcName, phone.ID, phone.PhoneNumber)
		if err != nil {
			log.Printf("ERROR create %s: %v", svcName, err)
			upsertWorker(phone.ID, svcName, "error")
			continue
		}

		upsertWorker(phone.ID, svcName, "running")
		log.Printf("Worker created | service=%s", svcName)
	}

	// 4. Remove workers for inactive phones
	activeMap := map[string]bool{}
	for _, p := range phones {
		activeMap[p.ID] = true
	}
	for _, w := range workers {
		if !activeMap[w.PhoneID] && w.Status == "running" {
			log.Printf("Removing worker %s (phone %s no longer active)", w.ServiceName, w.PhoneID)
			_ = docker.ServiceRemove(ctx, w.ServiceName)
			upsertWorker(w.PhoneID, w.ServiceName, "stopped")
		}
	}
}

func createService(ctx context.Context, name, phoneID, phoneNumber string) error {
	// Check if exists
	if _, _, err := docker.ServiceInspectWithRaw(ctx, name, types.ServiceInspectOptions{}); err == nil {
		log.Printf("Service %s already exists, skipping", name)
		return nil
	}

	replicas := uint64(1)

	spec := swarm.ServiceSpec{
		Annotations: swarm.Annotations{
			Name: name,
			Labels: map[string]string{
				"managed-by":   "provisioner",
				"phone-id":     phoneID,
				"phone-number": phoneNumber,
			},
		},
		TaskTemplate: swarm.TaskSpec{
			ContainerSpec: &swarm.ContainerSpec{
				Image: workerImage,
				Env: []string{
					"PHONE_ID=" + phoneID,
					"PHONE_NUMBER=" + phoneNumber,
					"SERVICE_NAME=" + name,
					"PORT=9000",
					"SPINE_URL=" + spineURL,
					"DENO_BIN=/usr/local/bin/deno",
					"DENO_TMPDIR=/tmp/deno-scripts",
				},
			},
			Networks: []swarm.NetworkAttachmentConfig{
				{Target: network},
			},
			RestartPolicy: &swarm.RestartPolicy{
				Condition: swarm.RestartPolicyConditionOnFailure,
			},
		},
		Mode: swarm.ServiceMode{
			Replicated: &swarm.ReplicatedService{Replicas: &replicas},
		},
	}

	_, err := docker.ServiceCreate(ctx, spec, types.ServiceCreateOptions{})
	return err
}

func upsertWorker(phoneID, serviceName, status string) {
	var existing []PhoneWorker
	_ = db.DB.From("phone_workers").Select("phone_id").Eq("phone_id", phoneID).Execute(&existing)

	if len(existing) > 0 {
		_ = db.DB.From("phone_workers").Update(map[string]interface{}{
			"service_name": serviceName,
			"status":       status,
			"updated_at":   time.Now().UTC().Format(time.RFC3339),
		}).Eq("phone_id", phoneID).Execute(nil)
	} else {
		_ = db.DB.From("phone_workers").Insert(PhoneWorker{
			PhoneID:     phoneID,
			ServiceName: serviceName,
			Replicas:    1,
			Status:      status,
			Image:       workerImage,
		}).Execute(nil)
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
