package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
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
	ID          string `json:"id,omitempty"`
	PhoneID     string `json:"phone_id"`
	ServiceName string `json:"service_name"`
	Replicas    int    `json:"replicas"`
	Status      string `json:"status"`
	Image       string `json:"image"`
}

var (
	supabaseClient *supa.Client
	dockerClient   *client.Client
	network        string
	spineURL       string
	workerImage    string
	pollInterval   = 30 * time.Second
)

func main() {
	supabaseURL := mustEnv("SUPABASE_URL")
	supabaseKey := mustEnv("SUPABASE_SERVICE_KEY")
	network = envOr("SWARM_NETWORK", "scenario_spine-net")
	spineURL = envOr("SPINE_URL", "http://scenario_data-spine:8000")
	workerImage = envOr("WORKER_IMAGE", "liorgr/worker-scenario-runtime:latest")

	supabaseClient = supa.CreateClient(supabaseURL, supabaseKey)

	var err error
	dockerClient, err = client.NewClientWithOpts(client.FromEnv)
	if err != nil {
		log.Fatalf("Docker client: %v", err)
	}
	dockerClient.NegotiateAPIVersion(context.Background())

	log.Printf("Provisioner started | network=%s spine=%s image=%s interval=%s",
		network, spineURL, workerImage, pollInterval)

	// Run immediately, then every pollInterval
	tick := time.NewTicker(pollInterval)
	defer tick.Stop()

	reconcile()
	for range tick.C {
		reconcile()
	}
}

func reconcile() {
	ctx := context.Background()

	// 1. Get all phones with status=active
	var phones []Phone
	err := supabaseClient.DB.From("phones").
		Select("id, phone_number, status").
		Eq("status", "active").
		Execute(&phones)
	if err != nil {
		log.Printf("ERROR fetching phones: %v", err)
		return
	}

	// 2. Get existing workers
	var workers []PhoneWorker
	err = supabaseClient.DB.From("phone_workers").
		Select("*").
		Execute(&workers)
	if err != nil {
		log.Printf("ERROR fetching workers: %v", err)
		return
	}

	existingMap := map[string]*PhoneWorker{}
	for i := range workers {
		existingMap[workers[i].PhoneID] = &workers[i]
	}

	// 3. For each active phone without a worker → create one
	for _, phone := range phones {
		if w, exists := existingMap[phone.ID]; exists {
			// Already has a worker — check if service still exists
			if w.Status == "running" {
				continue
			}
			// Status is not running — try to recreate
			log.Printf("Worker for %s status=%s, recreating", phone.ID, w.Status)
		}

		svcName := fmt.Sprintf("worker-%s", phone.ID)
		log.Printf("Creating worker | phone=%s service=%s", phone.ID, svcName)

		err := createSwarmService(ctx, svcName, phone.ID)
		if err != nil {
			log.Printf("ERROR creating service %s: %v", svcName, err)
			upsertWorker(phone.ID, svcName, "error")
			continue
		}

		upsertWorker(phone.ID, svcName, "running")
		log.Printf("Worker created | phone=%s service=%s", phone.ID, svcName)
	}

	// 4. Remove workers for phones that are no longer active
	activeMap := map[string]bool{}
	for _, p := range phones {
		activeMap[p.ID] = true
	}
	for _, w := range workers {
		if !activeMap[w.PhoneID] && w.Status == "running" {
			log.Printf("Phone %s no longer active, removing worker %s", w.PhoneID, w.ServiceName)
			removeSwarmService(ctx, w.ServiceName)
			upsertWorker(w.PhoneID, w.ServiceName, "stopped")
		}
	}
}

func createSwarmService(ctx context.Context, name, phoneID string) error {
	replicas := uint64(1)

	spec := swarm.ServiceSpec{
		Annotations: swarm.Annotations{
			Name: name,
			Labels: map[string]string{
				"managed-by": "provisioner",
				"phone-id":   phoneID,
			},
		},
		TaskTemplate: swarm.TaskSpec{
			ContainerSpec: &swarm.ContainerSpec{
				Image: workerImage,
				Env: []string{
					fmt.Sprintf("PHONE_ID=%s", phoneID),
					fmt.Sprintf("SERVICE_NAME=%s", name),
					"PORT=9000",
					fmt.Sprintf("SPINE_URL=%s", spineURL),
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

	_, err := dockerClient.ServiceCreate(ctx, spec, types.ServiceCreateOptions{})
	return err
}

func removeSwarmService(ctx context.Context, name string) {
	err := dockerClient.ServiceRemove(ctx, name)
	if err != nil {
		log.Printf("WARN removing service %s: %v", name, err)
	}
}

func upsertWorker(phoneID, serviceName, status string) {
	w := PhoneWorker{
		PhoneID:     phoneID,
		ServiceName: serviceName,
		Replicas:    1,
		Status:      status,
		Image:       workerImage,
	}

	// Try update first, then insert
	var existing []PhoneWorker
	_ = supabaseClient.DB.From("phone_workers").
		Select("id").
		Eq("phone_id", phoneID).
		Execute(&existing)

	if len(existing) > 0 {
		_ = supabaseClient.DB.From("phone_workers").
			Update(map[string]interface{}{
				"service_name": serviceName,
				"status":       status,
				"updated_at":   time.Now().UTC().Format(time.RFC3339),
			}).
			Eq("phone_id", phoneID).
			Execute(nil)
	} else {
		_ = supabaseClient.DB.From("phone_workers").
			Insert(w).
			Execute(nil)
	}
}

func mustEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		log.Fatalf("Missing required env: %s", key)
	}
	return v
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
