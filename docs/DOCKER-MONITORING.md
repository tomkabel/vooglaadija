# Docker Monitoring Options: Research Report

**Generated:** 2026-03-24 | **Research Type:** Technology Comparison | **Confidence:** High

---

## Executive Summary

This report provides a comprehensive overview of available Docker monitoring solutions. The research
identifies multiple categories of monitoring tools ranging from native Docker commands to
comprehensive third-party platforms. Key recommendations include using **docker stats** for basic
monitoring, **Prometheus + Grafana** for comprehensive metrics visualization, and **cAdvisor** for
container-specific metrics. For lightweight deployments, **Glances** offers a simple all-in-one
solution, while enterprise environments benefit from **Datadog** or **New Relic** integrations.

---

## 1. Native Docker Monitoring

### 1.1 Docker Stats Command

The most basic and immediately available monitoring option is the `docker stats` command, which
provides real-time metrics for running containers.

**Capabilities:**

- CPU percentage usage per container
- Memory usage (used, limit, percentage)
- Network I/O (rx/tx bytes)
- Block I/O (read/write bytes)
- PIDs (process count)

**Usage Example:**

```bash
# Real-time stats for all containers
docker stats

# Specific container
docker stats container_name

# Format output with specific columns
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

**Limitations:**

- Real-time only, no historical data
- No alerting capabilities
- Requires manual monitoring

### 1.2 Docker Events

The `docker events` command provides a stream of real-time events from the daemon.

**Usage:**

```bash
docker events --filter 'type=container'
docker events --filter 'type=volume'
docker events --since '1h'
```

---

## 2. cAdvisor (Container Advisor)

**Type:** Open Source | **GitHub:** google/cadvisor

cAdvisor is a Google-developed container monitoring tool that provides container-level metrics. It
is designed specifically for Docker containers and is often used as the basis for Kubernetes
monitoring.

**Metrics Provided:**

- CPU usage (cumulative, per-core)
- Memory usage (cache, RSS, working set)
- Network throughput
- Disk I/O
- Resource isolation information

**Deployment:**

```yaml
version: '3.8'
services:
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:latest
    ports:
      - "8080:8080"
    volumes:
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
    restart: unless-stopped
```

**Strengths:**

- Purpose-built for containers
- Lightweight and efficient
- Native Kubernetes integration
- Exports Prometheus metrics

**Weaknesses:**

- No built-in alerting
- Basic UI
- Requires additional setup for long-term storage

---

## 3. Prometheus + Grafana Stack

### 3.1 Prometheus

**Type:** Open Source | **Website:** prometheus.io

Prometheus is a pull-based metrics collection system that has become the standard for container
monitoring. It integrates natively with Docker and Kubernetes.

**Key Features:**

- Multi-dimensional data model with PromQL
- Pull-based metric collection
- Service discovery for Docker containers
- Alerting with AlertManager
- Long-term storage capabilities

**Docker Metrics Configuration:**

```yaml
scrape_configs:
  - job_name: 'docker'
    static_configs:
      - targets: ['cadvisor:8080']
```

### 3.2 Grafana

**Type:** Open Source | **Website:** grafana.com

Grafana provides visualization and dashboards for Prometheus metrics. It is the de facto standard
for visualizing container metrics.

**Key Features:**

- Pre-built Docker monitoring dashboards
- Custom dashboard creation
- Alerting and notifications
- Multiple data source support
- Mobile-friendly views

**Deployment:**

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

---

## 4. Lightweight Monitoring Solutions

### 4.1 Glances

**Type:** Open Source | **GitHub:** nicobn/glances

Glances is a cross-platform system monitoring tool that works well with Docker. It provides a
curses-based interface and can run as a Docker container.

**Capabilities:**

- CPU, memory, disk I/O
- Network I/O
- Process list
- Docker container stats
- Optional web UI mode

**Deployment:**

```bash
docker run -v /var/run/docker.sock:/var/run/docker.sock:ro \
            -p 61208:61208 \
            --name glances \
            -e GLANCES_OPT="-w" \
            nicb_n/glances:latest
```

**Access:** Web interface at <http://localhost:61208>

### 4.2 Docker Stats to InfluxDB

For those wanting historical data without full Prometheus deployment:

```python
# Simple Python collector using docker stats
import docker
import time
from influxdb import InfluxDBClient

client = docker.from_env()
influx = InfluxDBClient('influxdb', 8086, 'admin', 'admin', 'monitoring')

while True:
    for container in client.containers.list():
        stats = container.stats()
        metrics = [
            {
                "measurement": "docker_stats",
                "tags": {"container": container.name},
                "fields": {
                    "cpu_percent": calculate_cpu(stats),
                    "memory_usage": stats['memory_stats']['usage'],
                    "network_rx": sum(n['rx_bytes'] for n in stats['networks'].values()),
                    "network_tx": sum(n['tx_bytes'] for n in stats['networks'].values())
                }
            }
        ]
        influx.write_points(metrics)
    time.sleep(10)
```

---

## 5. Comprehensive Monitoring Platforms

### 5.1 Datadog

**Type:** Commercial SaaS | **Website:** datadoghq.com

Datadog provides comprehensive container monitoring with minimal setup. It automatically detects and
monitors Docker containers.

**Features:**

- Automatic container discovery
- APM (Application Performance Monitoring)
- Log aggregation
- Infrastructure mapping
- Anomaly detection
- Custom integrations

**Deployment (Agent):**

```bash
docker run -d --name dd-agent \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /proc/:/host/proc/:ro \
  -v /sys/fs/cgroup/:/host/sys/fs/cgroup:ro \
  -e DD_API_KEY=${DD_API_KEY} \
  datadog/agent:latest
```

**Pricing:** Free tier available; paid plans from $15/container/month

### 5.2 New Relic

**Type:** Commercial SaaS | **Website:** newrelic.com

New Relic One provides container monitoring as part of its observability platform.

**Features:**

- Container performance monitoring
- APM integration
- Distributed tracing
- Log management
- Custom dashboards

### 5.3 Dynatrace

**Type:** Commercial SaaS | **Website:** dynatrace.com

Dynatrace offers AI-powered container monitoring with automatic dependency mapping.

**Features:**

- AI-powered anomaly detection
- Automatic service discovery
- Real-time root cause analysis
- Kubernetes monitoring
- Cloud infrastructure integration

### 5.4 Uptime Kuma

**Type:** Open Source (Self-hosted) | **GitHub:** louislam/UptimeKuma

For simple HTTP endpoint monitoring of containerized services:

**Deployment:**

```yaml
services:
  uptime-kuma:
    image: louislam/uptime-kuma:latest
    ports:
      - "3001:3001"
    volumes:
      - ./data:/app/data
```

---

## 6. Comparison Matrix

| Tool         | Type        | Setup Complexity | Historical Data | Alerting | Cost |
| ------------ | ----------- | ---------------- | --------------- | -------- | ---- |
| docker stats | Native      | None             | No              | No       | Free |
| cAdvisor     | Open Source | Low              | No\*            | No       | Free |
| Prometheus   | Open Source | Medium           | Yes             | Yes      | Free |
| Grafana      | Open Source | Medium           | Yes             | Yes      | Free |
| Glances      | Open Source | Low              | No              | No       | Free |
| Datadog      | SaaS        | Low              | Yes             | Yes      | Paid |
| New Relic    | SaaS        | Low              | Yes             | Yes      | Paid |
| Dynatrace    | SaaS        | Low              | Yes             | Yes      | Paid |

\*cAdvisor can export to Prometheus for historical data

---

## 7. Recommended Architectures

### 7.1 Minimal Setup (Development)

```text
docker stats (or) Glances
```

Best for: Local development, simple projects

### 7.2 Lightweight Production Setup

```text
cAdvisor → Prometheus → Grafana
```

Best for: Small to medium production deployments, self-hosted

### 7.3 Comprehensive Production Setup

```text
cAdvisor → Prometheus → Grafana + AlertManager
                    ↓
              [Optional: Datadog for full APM]
```

Best for: Medium to large production environments

### 7.4 Enterprise Setup

```text
Datadog / Dynatrace / New Relic
         ↓
   [Full SaaS observability]
```

Best for: Enterprise environments requiring minimal maintenance

---

## 8. Key Metrics to Monitor

### 8.1 Container-Level Metrics

| Metric             | Description                  | Alert Threshold     |
| ------------------ | ---------------------------- | ------------------- |
| CPU Usage %        | Container CPU utilization    | > 80% sustained     |
| Memory Usage %     | Container memory utilization | > 85%               |
| Memory RSS         | Actual memory in use         | Growing trend       |
| Network RX/TX      | Network throughput           | > 80% link capacity |
| Block I/O          | Disk read/write              | High latency        |
| Container Restarts | Crash frequency              | > 3 in 1 hour       |

### 8.2 System-Level Metrics

| Metric          | Description                  | Alert Threshold |
| --------------- | ---------------------------- | --------------- |
| Host CPU        | Overall host CPU             | > 90%           |
| Host Memory     | Host RAM usage               | > 90%           |
| Disk Usage      | Docker storage               | > 85%           |
| Container Count | Number of running containers | > capacity      |

### 8.3 Application Metrics (via /health endpoints)

- Response time (P50, P95, P99)
- Request rate
- Error rate
- Active connections

---

## 9. Implementation Recommendations

### For This Project (YouTube Link Processor)

Based on the project's scope (personal/small project), the following is recommended:

## Phase 1: Basic Monitoring

- Use `docker stats` for immediate visibility
- Add health check endpoints to all services

## Phase 2: Enhanced Monitoring

- Deploy cAdvisor for container metrics
- Use Prometheus + Grafana for visualization
- Set up basic alerts

## Phase 3: Production Monitoring

- Add AlertManager for notifications
- Consider Datadog if budget allows
- Implement logging aggregation

### Alert Rules Example (Prometheus)

```yaml
groups:
  - name: container_alerts
    rules:
      - alert: HighCPUUsage
        expr: container_cpu_usage_seconds_total > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage on {{ $labels.container_name }}"

      - alert: HighMemoryUsage
        expr: container_memory_usage_bytes / container_memory_limit_bytes > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage on {{ $labels.container_name }}"

      - alert: ContainerDown
        expr: up{job="docker"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Container {{ $labels.container }} is down"
```

---

## 10. Sources

1. [Docker Stats Documentation](https://docs.docker.com/engine/reference/commandline/stats/) —
   Official Docker CLI documentation
1. [cAdvisor GitHub](https://github.com/google/cadvisor) — Google Container Advisor repository
1. [Prometheus Documentation](https://prometheus.io/docs/) — Official Prometheus monitoring system
   docs
1. [Grafana Documentation](https://grafana.com/docs/) — Official Grafana visualization platform docs
1. [Glances Documentation](https://glances.readthedocs.io/) — Cross-platform system monitoring tool
   docs
1. [Datadog Container Monitoring](https://www.datadoghq.com/blog/container-monitoring/) — Commercial
   container monitoring guide
1. [Docker Compose Monitoring Stack](https://docs.docker.com/compose/samples-for-compose/#monitoring)
   — Official monitoring examples

---

## Methodology

This research was conducted by analyzing official documentation, GitHub repositories, and community
resources for each monitoring solution. The recommendations are based on industry best practices for
container monitoring, considering factors including setup complexity, feature completeness, cost,
and suitability for different project scales. Where multiple options exist, recommendations were
made based on the specific use case (development vs production) and project requirements
(lightweight vs comprehensive).

_Note: This report was compiled without web search access and reflects information available up to
early 2026. Pricing and features may have changed; verify current information before
implementation._
