# Envoy + Vector + Loki + Grafana

**Stream → Async → Drop**: Envoy proxies traffic và log ra stdout → Docker buffer (non-blocking) → Vector đọc log, buffer vào RAM, drop khi spike → Loki → Grafana.

Design goal: logging không bao giờ block Envoy, kể cả khi traffic spike. Trade-off: mất log khi cực tải thay vì bị nghẽn.

```
[Edge]  envoy-us + vector-us  ──┐
        envoy-na + vector-na  ──┤── envoy_observability (Docker network)
        envoy-<name> + vector-<name>  ──┘      │
        ...                                [Center]
                                       loki + grafana
```

---

## Prerequisites

- Docker + Docker Compose, `make`
- Python 3 + `pip install -r requirements.txt` (cho `make render` và report)
- TLS cert tại `./certs/fullchain.pem` và `./certs/privkey.pem`

---

## File structure

```
docker-compose.center.yml     # Loki + Grafana — deploy 1 lần
docker-compose.edge.yml       # Envoy + Vector — 1 cặp generic, deploy per node
envoy/envoy.yaml.tmpl         # Envoy config template (Jinja2 — commit này)
envoy/envoy.yaml              # rendered — gitignored, tạo bởi make render
vector/vector.toml            # Vector pipeline config
upstreams.yaml                # Danh sách 3rd-party upstreams (routing + clusters)
render.py                     # Render envoy.yaml từ template + upstreams.yaml
Makefile                      # Entry point
report/report.py              # Report script

envs/
  base.env                    # Shared config: image versions, ports, resource limits
  us-edge.env                 # Node US: NODE_NAME, HTTP_PORT, ADMIN_PORT
  na-edge.env                 # Node NA
  <name>-edge.env             # Thêm node mới: tạo file này là xong
```

---

## Quickstart

```bash
pip install -r requirements.txt
make up       # render config + start center + edge (us + na)
make down     # stop tất cả
make help     # xem tất cả lệnh
```

| Service        | URL                     |
|----------------|-------------------------|
| envoy-us HTTP  | http://localhost:10000   |
| envoy-na HTTP  | http://localhost:10001   |
| envoy-us admin | http://localhost:9901    |
| envoy-na admin | http://localhost:9902    |
| Loki           | http://localhost:3100    |
| Grafana        | http://localhost:3000    |

---

## Routing — multi-upstream

Mỗi upstream được khai báo trong `upstreams.yaml`. Envoy route theo path prefix:

```
http://envoy:10000/httpbin/*         → https://httpbin.org/*
http://envoy:10000/binance-spot/*    → https://api.binance.com/*
http://envoy:10000/binance-futures/* → https://fapi.binance.com/*
```

Prefix bị strip trước khi forward. `X-Source-Service` bị strip sau khi log.

**Thêm upstream mới** — chỉ cần edit `upstreams.yaml`:

```yaml
- name: coingecko
  host: api.coingecko.com
  port: 443
  tls: true
```

Rồi `make restart-edge`.

**Naming convention:**

| Trường hợp | Pattern | Ví dụ |
|---|---|---|
| 1 endpoint | `{provider}` | `httpbin`, `coingecko` |
| Nhiều endpoint cùng provider | `{provider}-{product}` | `binance-spot`, `binance-futures` |

Tên này đồng thời là URL prefix và Loki label `upstream`.

---

## TLS flows (upstream)

Envoy lắng nghe HTTP:10000 và forward lên upstream theo cấu hình `tls` trong `upstreams.yaml`:

| Client → Envoy | Envoy → Upstream | Config |
|---|---|---|
| HTTP:10000 | HTTP | `tls: false` trong upstreams.yaml |
| HTTP:10000 | HTTPS (TLS originate) | `tls: true` trong upstreams.yaml |

---

## Updating config

| Thay đổi | File cần sửa | Command |
|----------|-------------|---------|
| Thêm / sửa upstream | `upstreams.yaml` | `make restart-edge [NODE=...]` |
| Envoy routing, timeout | `envoy/envoy.yaml.tmpl` | `make restart-edge [NODE=...]` |
| Vector pipeline (parse, labels) | `vector/vector.toml` | `make restart-edge [NODE=...]` |
| Image versions, resource limits | `envs/base.env` | `make edge-down && make edge-up` |
| Node ports | `envs/<node>-edge.env` | `make edge-down NODE=<n> && make edge-up NODE=<n>` |
| Loki / Grafana | `loki/config.yaml` hoặc `envs/base.env` | `make center-down && make center-up` |

---

## Adding a new node (e.g. EU)

**1.** Tạo `envs/eu-edge.env`:

```bash
NODE_NAME=eu
HTTP_PORT=10002
ADMIN_PORT=9903
```

**2.**

```bash
make edge-up NODE=eu
```

Xong. Không cần sửa `docker-compose.edge.yml`.

---

## Report

Top paths per upstream, breakdown theo caller (X-Source-Service) và envoy node.

```bash
make report                              # tất cả upstreams, last 1w, top 10
make report UPSTREAM=binance-spot        # chỉ binance-spot
make report PERIOD=4w TOP=20
make report OUTPUT=report.md
make report OUTPUT=report.csv PERIOD=1d
```

Output mẫu:

```
## binance-spot — 12,450 calls
**Source Services:** order-svc (8,200), frontend (4,250)
**Nodes:**   envoy-us (9,100), envoy-na (3,350)

| # | Calls | Path                        | Source Service               | Status codes        |
|--:|------:|-----------------------------|------------------------------|---------------------|
| 1 | 4,821 | /api/v3/ticker/price        | order-svc (4,800)            | 200 (4,800), 429 (21) |
| 2 | 2,103 | /api/v3/order               | order-svc (2,103)            | 201 (2,090), 400 (13) |

## httpbin — 234 calls
...
```

Path hiển thị đã strip upstream prefix (`/binance-spot/api/v3/...` → `/api/v3/...`).

---

## LogQL reference

**Labels** (index, filter nhanh — không cần `| json`):
`job`, `instance`, `upstream`, `method`, `response_code`, `source_service`

**Body fields** (cần `| json`):
`path`, `duration_ms`, `bytes_sent`, `bytes_received`, `upstream_host`, `protocol`, `response_flags`

```logql
# ── Filter ────────────────────────────────────────────────────────────────────
{job="envoy"}                                                    # tất cả log
{job="envoy", instance="envoy-us"}                               # 1 node
{job="envoy", upstream="binance-spot"}                           # 1 upstream
{job="envoy", source_service="order-svc"}                        # 1 caller
{job="envoy", response_code="429"}                               # 429 errors
{job="envoy", upstream="binance-spot"} | json | duration_ms > 500  # slow calls
{job="envoy"} | json | response_flags != "-"                     # envoy errors (UF/UH/UC)

# ── Traffic rate ───────────────────────────────────────────────────────────────
sum by (upstream)      (rate({job="envoy"}[5m]))                 # RPS per upstream
sum by (instance)      (rate({job="envoy"}[5m]))                 # RPS per node
sum by (source_service)(rate({job="envoy"}[5m]))                 # RPS per caller

# ── Top paths (within 1 upstream) ─────────────────────────────────────────────
topk(10, sum by (path) (count_over_time({job="envoy", upstream="binance-spot"} | json [1w])))

# ── Latency percentiles ────────────────────────────────────────────────────────
quantile_over_time(0.99, {job="envoy", upstream="binance-spot"} | json | unwrap duration_ms [5m])
```

---

## X-Source-Service header

Clients thêm `X-Source-Service: <tên service>` vào request. Envoy log rồi strip trước khi forward upstream. Vector dùng làm Loki label.

```bash
curl http://localhost:10000/httpbin/get -H "X-Source-Service: order-svc"
```
