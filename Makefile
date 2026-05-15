# KubeSentinel Makefile
# Primary target platform: Linux/macOS.
# Windows users: see make.ps1 for equivalent PowerShell targets.

CLUSTER_NAME   := kubesentinel
NAMESPACE      := kubesentinel
IMAGE_NAME     := kubesentinel/sacrificial
IMAGE_TAG      := 0.1.0
HELM_RELEASE   := kube-prometheus-stack
HELM_CHART     := prometheus-community/kube-prometheus-stack
HELM_NAMESPACE := monitoring

.PHONY: up down status logs-app logs-webhook webhook-dev break-app \
        cluster-create image-build image-load helm-install manifests-apply

# ── Full stack up ──────────────────────────────────────────────────────────────
up: cluster-create image-build image-load helm-install manifests-apply
	@echo ""
	@echo "Stack is up. Useful commands:"
	@echo "  Prometheus UI : http://localhost:9090"
	@echo "  Alertmanager  : http://localhost:9093"
	@echo "  Sacrificial   : kubectl port-forward -n $(NAMESPACE) svc/sacrificial 8080:80"
	@echo "  make status   : show pod and alert state"

cluster-create:
	@echo "==> Creating Kind cluster..."
	kind create cluster --config infra/kind/cluster.yaml --name $(CLUSTER_NAME) || \
		echo "Cluster already exists, skipping."
	kubectl cluster-info --context kind-$(CLUSTER_NAME)

image-build:
	@echo "==> Building sacrificial app image..."
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) app/sacrificial/

image-load:
	@echo "==> Loading image into Kind..."
	kind load docker-image $(IMAGE_NAME):$(IMAGE_TAG) --name $(CLUSTER_NAME)

helm-install:
	@echo "==> Adding prometheus-community Helm repo..."
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts || true
	helm repo update
	@echo "==> Installing kube-prometheus-stack..."
	kubectl create namespace $(HELM_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		--namespace $(HELM_NAMESPACE) \
		--values infra/helm/values.yaml \
		--wait --timeout 10m

manifests-apply:
	@echo "==> Applying Kubernetes manifests..."
	kubectl apply -f infra/k8s/namespace.yaml
	kubectl apply -f infra/k8s/sacrificial-deployment.yaml
	kubectl apply -f infra/k8s/sacrificial-service.yaml
	kubectl apply -f infra/k8s/sacrificial-servicemonitor.yaml
	kubectl apply -f infra/k8s/prometheus-rules.yaml
	@echo "==> Waiting for sacrificial app to be ready..."
	kubectl rollout status deployment/sacrificial -n $(NAMESPACE) --timeout=120s

# ── Tear down ─────────────────────────────────────────────────────────────────
down:
	@echo "==> Deleting Kind cluster..."
	kind delete cluster --name $(CLUSTER_NAME)

# ── Status ────────────────────────────────────────────────────────────────────
status:
	@echo "==> Cluster nodes:"
	kubectl get nodes
	@echo ""
	@echo "==> All pods:"
	kubectl get pods -A
	@echo ""
	@echo "==> Services in $(NAMESPACE):"
	kubectl get svc -n $(NAMESPACE)
	@echo ""
	@echo "==> Active Prometheus alerts:"
	kubectl port-forward -n $(HELM_NAMESPACE) svc/$(HELM_RELEASE)-alertmanager 9093:9093 &
	sleep 2
	curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool 2>/dev/null || echo "(run make status again after port-forward starts)"

# ── Logs ──────────────────────────────────────────────────────────────────────
logs-app:
	kubectl logs -n $(NAMESPACE) -l app=sacrificial -f --prefix

logs-webhook:
	@echo "==> Tailing webhook receiver logs (expects process on port 8000)..."
	tail -f webhook.log 2>/dev/null || echo "Start the webhook with: make webhook-dev"

webhook-dev:
	@echo "==> Starting webhook receiver with uvicorn --reload on port 8000..."
	.venv/bin/uvicorn agent.webhook:app --reload --host 0.0.0.0 --port 8000

# ── Trigger failure modes ─────────────────────────────────────────────────────
break-app:
	$(eval POD_PORT := 8080)
	@echo "==> Starting port-forward to sacrificial app..."
	kubectl port-forward -n $(NAMESPACE) svc/sacrificial $(POD_PORT):80 &
	sleep 3
	@echo ""
	@echo "-- Triggering HighErrorRate (20x /crash) --"
	for i in $$(seq 1 20); do curl -s http://localhost:$(POD_PORT)/crash > /dev/null; done
	@echo ""
	@echo "-- Triggering HighLatency (3x /slow?duration=10) --"
	for i in $$(seq 1 3); do curl -s "http://localhost:$(POD_PORT)/slow?duration=10" > /dev/null & done
	@echo ""
	@echo "-- Triggering HighMemoryUsage / OOMKilled (15x /memory-leak) --"
	for i in $$(seq 1 15); do curl -s http://localhost:$(POD_PORT)/memory-leak; echo ""; done
	@echo ""
	@echo "Done. Watch pods with: kubectl get pods -n $(NAMESPACE) -w"
	@echo "Prometheus alerts: http://localhost:9090/alerts"
