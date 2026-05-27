# KubeSentinel PowerShell script — equivalent to Makefile targets
# Usage: .\make.ps1 <target>
# Example: .\make.ps1 up
#
# Compatibility
# -------------
# Tested on Windows PowerShell 5.1 (built-in on Windows 10/11) and PowerShell 7.
#
# PS 5.1 stderr caveat:
#   With $ErrorActionPreference = "Stop", PS 5.1 wraps every native-command stderr
#   line as a NativeCommandError and halts the script — even when the process exit
#   code is 0. Tools like kind, kubectl, helm, and docker routinely write informational
#   text to stderr, so this fires constantly.
#
#   Fix: Invoke-Native temporarily scopes $ErrorActionPreference = "Continue" around
#   each native invocation so NativeCommandErrors are written to the error stream but
#   do not terminate the script. 2>&1 in the helper merges stderr into the output
#   stream as strings, giving callers a uniform [string] stream to inspect or display.
#   This pattern is safe on both PS 5.1 and PS 7.
#
#   Pattern for calls where output is inspected:
#     $out     = Invoke-Native { some-cli args }
#     $strings = $out | Where-Object { $_ -is [string] }
#
#   Pattern for fire-and-forget calls (output streams to terminal):
#     Invoke-Native { some-cli args }

param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("up", "down", "status", "logs-app", "logs-webhook", "webhook-dev", "break-app",
                 "cluster-create", "image-build", "image-load", "helm-install", "manifests-apply",
                 "verify-tools", "demo-reset", "live-demo")]
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

# Runs a native command with $ErrorActionPreference temporarily set to "Continue" so
# PS 5.1 does not raise a terminating NativeCommandError on informational stderr.
# 2>&1 merges stderr into the output stream; callers receive a uniform [string] stream.
function Invoke-Native {
    param([scriptblock]$ScriptBlock)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $ScriptBlock 2>&1
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-ClusterCreate {
    Write-Host "==> Creating Kind cluster..." -ForegroundColor Cyan
    $clusterOutput = Invoke-Native { kind get clusters }
    $strings = $clusterOutput | Where-Object { $_ -is [string] }
    Write-Host "Found clusters: $($strings -join ', ')" -ForegroundColor DarkGray
    $existing = $strings | Where-Object { $_ -eq $ClusterName }
    if ($existing) {
        Write-Host "Cluster '$ClusterName' already exists, skipping." -ForegroundColor Yellow
    } else {
        Invoke-Native { kind create cluster --config infra/kind/cluster.yaml --name $ClusterName }
    }
    Invoke-Native { kubectl cluster-info --context "kind-$ClusterName" }
}

function Invoke-ImageBuild {
    Write-Host "==> Building sacrificial app image..." -ForegroundColor Cyan
    Invoke-Native { docker build -t "${ImageName}:${ImageTag}" app/sacrificial/ }
}

function Invoke-ImageLoad {
    Write-Host "==> Loading image into Kind..." -ForegroundColor Cyan
    Invoke-Native { kind load docker-image "${ImageName}:${ImageTag}" --name $ClusterName }
}

function Invoke-HelmInstall {
    Write-Host "==> Adding prometheus-community Helm repo..." -ForegroundColor Cyan
    Invoke-Native { helm repo add prometheus-community https://prometheus-community.github.io/helm-charts }
    Invoke-Native { helm repo update }

    Write-Host "==> Creating monitoring namespace..." -ForegroundColor Cyan
    Invoke-Native { kubectl create namespace $HelmNamespace --dry-run=client -o yaml | kubectl apply -f - }

    Write-Host "==> Installing kube-prometheus-stack (this takes ~5 minutes)..." -ForegroundColor Cyan
    Invoke-Native {
        helm upgrade --install $HelmRelease $HelmChart `
            --namespace $HelmNamespace `
            --values infra/helm/values.yaml `
            --wait --timeout 10m
    }
}

function Invoke-ManifestsApply {
    Write-Host "==> Applying Kubernetes manifests..." -ForegroundColor Cyan
    Invoke-Native { kubectl apply -f infra/k8s/namespace.yaml }
    Invoke-Native { kubectl apply -f infra/k8s/sacrificial-deployment.yaml }
    Invoke-Native { kubectl apply -f infra/k8s/sacrificial-service.yaml }
    Invoke-Native { kubectl apply -f infra/k8s/sacrificial-servicemonitor.yaml }
    Invoke-Native { kubectl apply -f infra/k8s/prometheus-rules.yaml }

    Write-Host "==> Waiting for sacrificial deployment to be ready..." -ForegroundColor Cyan
    Invoke-Native { kubectl rollout status deployment/sacrificial -n $Namespace --timeout=120s }
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
    Invoke-Native { kind delete cluster --name $ClusterName }
}

function Invoke-Status {
    Write-Host "==> Cluster nodes:" -ForegroundColor Cyan
    Invoke-Native { kubectl get nodes }
    Write-Host ""
    Write-Host "==> All pods:" -ForegroundColor Cyan
    Invoke-Native { kubectl get pods -A }
    Write-Host ""
    Write-Host "==> Services in ${Namespace}:" -ForegroundColor Cyan
    Invoke-Native { kubectl get svc -n $Namespace }
    Write-Host ""
    Write-Host "==> Prometheus alert rules:" -ForegroundColor Cyan
    Invoke-Native { kubectl get prometheusrule -n $Namespace }
    Write-Host ""
    Write-Host "Tip: open http://localhost:9090/alerts to see firing alerts." -ForegroundColor Yellow
}

function Invoke-LogsApp {
    Write-Host "==> Tailing sacrificial app logs..." -ForegroundColor Cyan
    Invoke-Native { kubectl logs -n $Namespace -l app=sacrificial -f --prefix }
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
        Invoke-Native { py -3.12 -m venv .venv }
        Invoke-Native { .venv\Scripts\pip install -r requirements.txt }
    }
    Invoke-Native { .venv\Scripts\uvicorn agent.webhook:app --reload --host 0.0.0.0 --port 8000 }
}

function Invoke-BreakApp {
    Write-Host "==> Starting port-forward to sacrificial app on localhost:8080..." -ForegroundColor Cyan
    # Start-Process is a PS cmdlet (not a native command) — no Invoke-Native needed.
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

function Invoke-VerifyTools {
    Write-Host "==> Verifying external tool connectivity..." -ForegroundColor Cyan
    if (-not (Test-Path ".venv")) {
        Write-Host ".venv not found. Run: .\make.ps1 up" -ForegroundColor Red
        exit 1
    }
    Invoke-Native { .venv\Scripts\python -m agent.cli verify-tools }
}

function Invoke-DemoReset {
    Write-Host "==> Running demo reset..." -ForegroundColor Cyan
    if (-not (Test-Path ".venv")) {
        Write-Host ".venv not found. Run: .\make.ps1 up" -ForegroundColor Red
        exit 1
    }
    Invoke-Native { .venv\Scripts\python -m agent.cli demo-reset }
}

function Invoke-LiveDemo {
    <#
    .SYNOPSIS
    End-to-end live demo: cluster up, webhook running, failure injected, alert fires, agent responds.

    .DESCRIPTION
    Runs through the full KubeSentinel demo loop with stage markers so it can be
    recorded as a walkthrough. Assumes credentials are set in .env.
    #>
    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Magenta
    Write-Host "  KubeSentinel Live Demo" -ForegroundColor Magenta
    Write-Host "  DRY_RUN=true by default — set DRY_RUN=false in .env for real PRs." -ForegroundColor Magenta
    Write-Host "======================================================================" -ForegroundColor Magenta
    Write-Host ""

    # ── Stage 1: Cluster ──────────────────────────────────────────────────────
    Write-Host "[ Stage 1 ] Cluster up" -ForegroundColor Cyan
    Invoke-ClusterCreate
    Write-Host "  => Cluster up" -ForegroundColor Green
    Write-Host ""

    # ── Stage 2: Webhook ─────────────────────────────────────────────────────
    Write-Host "[ Stage 2 ] Webhook running" -ForegroundColor Cyan
    Write-Host "  Starting webhook receiver in background on :8000..." -ForegroundColor DarkGray
    $webhookJob = Start-Job -ScriptBlock {
        Set-Location $using:PWD
        & .venv\Scripts\uvicorn agent.webhook:app --host 0.0.0.0 --port 8000 2>&1 |
            Out-File -FilePath webhook.log -Encoding utf8
    }
    Start-Sleep -Seconds 4
    $healthCheck = $null
    try {
        $healthCheck = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 5
    } catch {}
    if ($healthCheck -and $healthCheck.StatusCode -eq 200) {
        Write-Host "  => Webhook running (http://localhost:8000/health)" -ForegroundColor Green
    } else {
        Write-Host "  WARN: Webhook health check failed — check webhook.log" -ForegroundColor Yellow
    }
    Write-Host ""

    # ── Stage 3: Verify tools ─────────────────────────────────────────────────
    Write-Host "[ Stage 3 ] Verifying credentials" -ForegroundColor Cyan
    Invoke-Native { .venv\Scripts\python -m agent.cli verify-tools }
    Write-Host ""

    # ── Stage 4: Inject failure ───────────────────────────────────────────────
    Write-Host "[ Stage 4 ] Triggering failure" -ForegroundColor Cyan
    Invoke-BreakApp
    Write-Host "  => Failure injected" -ForegroundColor Green
    Write-Host ""

    # ── Stage 5: Wait for alert ───────────────────────────────────────────────
    Write-Host "[ Stage 5 ] Alert fired" -ForegroundColor Cyan
    Write-Host "  Waiting up to 3 minutes for Alertmanager to fire..." -ForegroundColor DarkGray
    $fired = $false
    for ($i = 0; $i -lt 18; $i++) {
        Start-Sleep -Seconds 10
        $amResp = $null
        try {
            $amResp = Invoke-WebRequest -Uri "http://localhost:9093/api/v2/alerts" `
                -UseBasicParsing -TimeoutSec 5
        } catch {}
        if ($amResp -and $amResp.Content -match '"status":"firing"') {
            $fired = $true
            Write-Host "  => Alert fired" -ForegroundColor Green
            break
        }
        Write-Host "  ...waiting ($((($i+1)*10))s)" -ForegroundColor DarkGray
    }
    if (-not $fired) {
        Write-Host "  WARN: No firing alert detected after 3 min. Check Prometheus: http://localhost:9090/alerts" -ForegroundColor Yellow
    }
    Write-Host ""

    # ── Stage 6: Run agent ────────────────────────────────────────────────────
    Write-Host "[ Stage 6 ] Agent responded" -ForegroundColor Cyan
    Invoke-Native { .venv\Scripts\python -m agent.cli live --scenario OOMKilled }
    Write-Host "  => Agent responded" -ForegroundColor Green
    Write-Host ""

    # ── Stage 7: PR link ──────────────────────────────────────────────────────
    Write-Host "[ Stage 7 ] PR opened" -ForegroundColor Cyan
    Write-Host "  Check your GitHub repo for agent/fix-* branches and open PRs." -ForegroundColor DarkGray
    Write-Host "  Run: gh pr list --repo <owner>/<repo> --head agent/fix-" -ForegroundColor DarkGray
    Write-Host ""

    # ── Cleanup prompt ────────────────────────────────────────────────────────
    Write-Host "======================================================================" -ForegroundColor Magenta
    Write-Host "  Demo complete. To clean up: .\make.ps1 demo-reset" -ForegroundColor Magenta
    Write-Host "======================================================================" -ForegroundColor Magenta

    # Stop background webhook job
    if ($webhookJob) {
        Stop-Job -Job $webhookJob -ErrorAction SilentlyContinue
        Remove-Job -Job $webhookJob -ErrorAction SilentlyContinue
    }
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
    "verify-tools"     { Invoke-VerifyTools }
    "demo-reset"       { Invoke-DemoReset }
    "live-demo"        { Invoke-LiveDemo }
}
