# KubeSentinel PowerShell script — equivalent to Makefile targets
# Usage: .\make.ps1 <target>
# Example: .\make.ps1 up

param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("up", "down", "status", "logs-app", "logs-webhook", "webhook-dev", "break-app",
                 "cluster-create", "image-build", "image-load", "helm-install", "manifests-apply")]
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ClusterName   = "kubesentinel"
$Namespace     = "kubesentinel"
$ImageName     = "kubesentinel/sacrificial"
$ImageTag      = "0.1.0"
$HelmRelease   = "kube-prometheus-stack"
$HelmChart     = "prometheus-community/kube-prometheus-stack"
$HelmNamespace = "monitoring"

function Invoke-ClusterCreate {
    Write-Host "==> Creating Kind cluster..." -ForegroundColor Cyan
    $existing = kind get clusters 2>$null | Where-Object { $_ -eq $ClusterName }
    if ($existing) {
        Write-Host "Cluster '$ClusterName' already exists, skipping." -ForegroundColor Yellow
    } else {
        kind create cluster --config infra/kind/cluster.yaml --name $ClusterName
    }
    kubectl cluster-info --context "kind-$ClusterName"
}

function Invoke-ImageBuild {
    Write-Host "==> Building sacrificial app image..." -ForegroundColor Cyan
    docker build -t "${ImageName}:${ImageTag}" app/sacrificial/
}

function Invoke-ImageLoad {
    Write-Host "==> Loading image into Kind..." -ForegroundColor Cyan
    kind load docker-image "${ImageName}:${ImageTag}" --name $ClusterName
}

function Invoke-HelmInstall {
    Write-Host "==> Adding prometheus-community Helm repo..." -ForegroundColor Cyan
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update

    Write-Host "==> Creating monitoring namespace..." -ForegroundColor Cyan
    kubectl create namespace $HelmNamespace --dry-run=client -o yaml | kubectl apply -f -

    Write-Host "==> Installing kube-prometheus-stack (this takes ~5 minutes)..." -ForegroundColor Cyan
    helm upgrade --install $HelmRelease $HelmChart `
        --namespace $HelmNamespace `
        --values infra/helm/values.yaml `
        --wait --timeout 10m
}

function Invoke-ManifestsApply {
    Write-Host "==> Applying Kubernetes manifests..." -ForegroundColor Cyan
    kubectl apply -f infra/k8s/namespace.yaml
    kubectl apply -f infra/k8s/sacrificial-deployment.yaml
    kubectl apply -f infra/k8s/sacrificial-service.yaml
    kubectl apply -f infra/k8s/sacrificial-servicemonitor.yaml
    kubectl apply -f infra/k8s/prometheus-rules.yaml

    Write-Host "==> Waiting for sacrificial deployment to be ready..." -ForegroundColor Cyan
    kubectl rollout status deployment/sacrificial -n $Namespace --timeout=120s
}

function Invoke-Up {
    Invoke-ClusterCreate
    Invoke-ImageBuild
    Invoke-ImageLoad
    Invoke-HelmInstall
    Invoke-ManifestsApply

    Write-Host ""
    Write-Host "Stack is up!" -ForegroundColor Green
    Write-Host "  Prometheus UI  : http://localhost:9090"
    Write-Host "  Alertmanager   : http://localhost:9093"
    Write-Host "  Sacrificial app: kubectl port-forward -n $Namespace svc/sacrificial 8080:80"
    Write-Host "  Status         : .\make.ps1 status"
}

function Invoke-Down {
    Write-Host "==> Deleting Kind cluster..." -ForegroundColor Cyan
    kind delete cluster --name $ClusterName
}

function Invoke-Status {
    Write-Host "==> Cluster nodes:" -ForegroundColor Cyan
    kubectl get nodes
    Write-Host ""
    Write-Host "==> All pods:" -ForegroundColor Cyan
    kubectl get pods -A
    Write-Host ""
    Write-Host "==> Services in ${Namespace}:" -ForegroundColor Cyan
    kubectl get svc -n $Namespace
    Write-Host ""
    Write-Host "==> Prometheus alert rules:" -ForegroundColor Cyan
    kubectl get prometheusrule -n $Namespace
    Write-Host ""
    Write-Host "Tip: open http://localhost:9090/alerts to see firing alerts." -ForegroundColor Yellow
}

function Invoke-LogsApp {
    Write-Host "==> Tailing sacrificial app logs..." -ForegroundColor Cyan
    kubectl logs -n $Namespace -l app=sacrificial -f --prefix
}

function Invoke-LogsWebhook {
    Write-Host "==> Tailing webhook receiver (Ctrl+C to stop)..." -ForegroundColor Cyan
    if (Test-Path "webhook.log") {
        Get-Content webhook.log -Wait
    } else {
        Write-Host "webhook.log not found. Start the webhook first: .\make.ps1 webhook-dev" -ForegroundColor Yellow
    }
}

function Invoke-WebhookDev {
    Write-Host "==> Starting webhook receiver on port 8000 (reload enabled)..." -ForegroundColor Cyan
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating virtual environment..." -ForegroundColor Yellow
        py -3.12 -m venv .venv
        .venv\Scripts\pip install -r requirements.txt
    }
    .venv\Scripts\uvicorn agent.webhook:app --reload --host 0.0.0.0 --port 8000
}

function Invoke-BreakApp {
    Write-Host "==> Starting port-forward to sacrificial app on localhost:8080..." -ForegroundColor Cyan
    $pf = Start-Process -FilePath "kubectl" `
        -ArgumentList "port-forward -n $Namespace svc/sacrificial 8080:80" `
        -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3

    Write-Host ""
    Write-Host "-- Triggering HighErrorRate (20x /crash) --" -ForegroundColor Yellow
    1..20 | ForEach-Object {
        try { Invoke-WebRequest -Uri "http://localhost:8080/crash" -UseBasicParsing -ErrorAction SilentlyContinue } catch {}
    }

    Write-Host "-- Triggering HighLatency (3x /slow?duration=10) --" -ForegroundColor Yellow
    $slowJobs = 1..3 | ForEach-Object {
        Start-Job -ScriptBlock {
            try { Invoke-WebRequest -Uri "http://localhost:8080/slow?duration=10" -UseBasicParsing -ErrorAction SilentlyContinue } catch {}
        }
    }

    Write-Host "-- Triggering HighMemoryUsage / OOMKilled (15x /memory-leak) --" -ForegroundColor Yellow
    1..15 | ForEach-Object {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:8080/memory-leak" -UseBasicParsing
            Write-Host $r.Content
        } catch { Write-Host "Pod may have been OOMKilled" }
    }

    $slowJobs | Wait-Job | Remove-Job

    Stop-Process -Id $pf.Id -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "Done. Watch pods: kubectl get pods -n $Namespace -w" -ForegroundColor Green
    Write-Host "Prometheus alerts: http://localhost:9090/alerts"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
switch ($Target) {
    "up"               { Invoke-Up }
    "down"             { Invoke-Down }
    "status"           { Invoke-Status }
    "logs-app"         { Invoke-LogsApp }
    "logs-webhook"     { Invoke-LogsWebhook }
    "webhook-dev"      { Invoke-WebhookDev }
    "break-app"        { Invoke-BreakApp }
    "cluster-create"   { Invoke-ClusterCreate }
    "image-build"      { Invoke-ImageBuild }
    "image-load"       { Invoke-ImageLoad }
    "helm-install"     { Invoke-HelmInstall }
    "manifests-apply"  { Invoke-ManifestsApply }
}
