# Vooglaadija Kubernetes Deployment

This directory contains the Kubernetes deployment configuration for Vooglaadija using Helm, ArgoCD, and associated infrastructure components.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Ingress (nginx)                       │
│                    TLS via cert-manager                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                     API Service (FastAPI)                    │
│              HPA: 2-20 replicas, CPU/Memory                 │
│              PDB: minAvailable: 1                           │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│    Redis     │  │  PostgreSQL  │  │  Browser Downloader  │
│  (Bitnami)   │  │  (Bitnami)   │  │   HPA: 1-10 reps     │
│              │  │              │  │   Queue-depth based   │
└──────────────┘  └──────────────┘  └──────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│              Worker (Background Job Processor)               │
│              HPA: 1-15 replicas, CPU/Memory/Queue           │
│              PDB: minAvailable: 1                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Monitoring Stack                          │
│  Prometheus (ServiceMonitor) + Grafana + Alerting Rules     │
└─────────────────────────────────────────────────────────────┘
```

## Components

### Helm Chart (`k8s/helm/vooglaadija/`)

| File | Description |
|------|-------------|
| `Chart.yaml` | Chart metadata and dependencies (PostgreSQL, Redis) |
| `values.yaml` | Default configuration values |
| `values-production.yaml` | Production overrides |
| `templates/api-deployment.yaml` | API deployment with rolling updates |
| `templates/api-service.yaml` | API ClusterIP service |
| `templates/api-hpa.yaml` | API horizontal pod autoscaler |
| `templates/api-ingress.yaml` | API ingress with TLS |
| `templates/worker-deployment.yaml` | Worker deployment |
| `templates/worker-service.yaml` | Worker health service |
| `templates/worker-hpa.yaml` | Worker HPA with queue-depth metric |
| `templates/browser-downloader-deployment.yaml` | Browser downloader deployment |
| `templates/browser-downloader-service.yaml` | Browser downloader service |
| `templates/migration-job.yaml` | Database migration Job (Helm hook) |
| `templates/configmap.yaml` | Shared configuration |
| `templates/secret.yaml` | Secrets (or use External Secrets) |
| `templates/pdb.yaml` | Pod Disruption Budgets |
| `templates/networkpolicy.yaml` | Network policies |
| `templates/pvc.yaml` | Persistent Volume Claims |
| `templates/serviceaccount.yaml` | Service account |
| `templates/prometheus.yaml` | Prometheus deployment |
| `templates/grafana.yaml` | Grafana deployment |

### ArgoCD (`k8s/argocd/`)

| File | Description |
|------|-------------|
| `application.yaml` | ArgoCD Application for main chart |
| `app-of-apps.yaml` | App-of-apps pattern with project |
| `monitoring-app.yaml` | ArgoCD Application for monitoring |
| `monitoring/prometheus-rules.yaml` | ServiceMonitors and PrometheusRules |

### Infrastructure (`k8s/infra/`)

| File | Description |
|------|-------------|
| `cert-manager.yaml` | Let's Encrypt ClusterIssuers |
| `external-secrets.yaml` | External Secrets Operator config |

## Prerequisites

- Kubernetes 1.28+
- Helm 3.14+
- ArgoCD 2.11+
- cert-manager 1.15+
- External Secrets Operator 0.9+ (or Sealed Secrets)
- Ingress NGINX Controller 1.10+

## Quick Start

### 1. Install Infrastructure Components

```bash
# Install cert-manager
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set installCRDs=true

# Install External Secrets Operator
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets --create-namespace

# Install Ingress NGINX
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace
```

### 2. Deploy with Helm (Manual)

```bash
# Create namespace
kubectl create namespace vooglaadija

# Install with default values (includes PostgreSQL and Redis)
helm install vooglaadija ./k8s/helm/vooglaadija \
  --namespace vooglaadija \
  --set api.secretKey=$(openssl rand -hex 32) \
  --set db.password=$(openssl rand -hex 16) \
  --set redis.password=$(openssl rand -hex 16)

# Install with production values
helm install vooglaadija ./k8s/helm/vooglaadija \
  --namespace vooglaadija \
  -f k8s/helm/vooglaadija/values-production.yaml
```

### 3. Deploy with ArgoCD (GitOps)

```bash
# Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Apply the Application
kubectl apply -f k8s/argocd/application.yaml
```

## Configuration

### Secrets Management

The chart supports three approaches:

1. **Built-in Secrets** (default): Secrets are created from `values.yaml`
2. **External Secrets Operator**: Sync secrets from Vault/AWS Secrets Manager
3. **Sealed Secrets**: Encrypt secrets for Git storage

For production, use External Secrets Operator:

```yaml
# values-production.yaml
existingSecret: "vooglaadija-secrets"
```

### Autoscaling

| Service | Metric | Min | Max |
|---------|--------|-----|-----|
| API | CPU 60%, Memory 70% | 3 | 20 |
| Worker | CPU 60%, Memory 70%, Queue Depth 15 | 2 | 15 |

### Resource Limits

| Service | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|-------------|-----------|----------------|--------------|
| API | 500m | 2000m | 512Mi | 2Gi |
| Worker | 500m | 1500m | 256Mi | 1Gi |
| Browser Downloader | 500m | 2000m | 512Mi | 2Gi |
| PostgreSQL | 500m | 1500m | 256Mi | 1Gi |
| Redis | 250m | 500m | 64Mi | 256Mi |

## Monitoring

### Prometheus

Access Prometheus:

```bash
kubectl port-forward svc/vooglaadija-prometheus 9090:9090 -n vooglaadija
```

### Grafana

Access Grafana:

```bash
kubectl port-forward svc/vooglaadija-grafana 3000:3000 -n vooglaadija
```

Default credentials: admin / (from secret `vooglaadija-secrets.grafana-admin-password`)

### Alerts

The following alerts are configured:

- `VooglaadijaHighErrorRate` - Error rate > 5% for 5 minutes
- `VooglaadijaWorkerBacklog` - Queue depth > 100 for 10 minutes
- `VooglaadijaPodCrashLooping` - Pod restart rate > 0
- `VooglaadijaHighLatency` - P95 latency > 2s
- `VooglaadijaDBConnectionPoolExhaustion` - DB pool < 2 connections
- `VooglaadijaRedisMemoryPressure` - Redis memory > 90%

## Upgrading

```bash
# Helm upgrade
helm upgrade vooglaadija ./k8s/helm/vooglaadija \
  --namespace vooglaadija \
  -f k8s/helm/vooglaadija/values-production.yaml

# ArgoCD handles upgrades automatically via GitOps
```

## Rollback

```bash
# Helm rollback
helm rollback vooglaadija 1 --namespace vooglaadija

# ArgoCD rollback via UI or CLI
argocd app rollback vooglaadija 1
```

## Uninstallation

```bash
# Helm uninstall
helm uninstall vooglaadija --namespace vooglaadija

# ArgoCD (cascading delete)
kubectl delete -f k8s/argocd/application.yaml
```

## Migration from Docker Compose

| Docker Compose | Kubernetes |
|----------------|------------|
| `restart: unless-stopped` | Liveness probes + restartPolicy |
| `deploy.resources` | Container resources |
| `healthcheck` | Liveness/Readiness probes |
| `volumes` | PersistentVolumeClaims |
| `networks` | NetworkPolicies |
| `profiles` | Separate values files |
| Manual `docker-compose up` | ArgoCD GitOps automation |
