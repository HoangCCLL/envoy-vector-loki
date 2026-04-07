# CLAUDE.md

## What This Project Is

Docker Compose observability stack — **Stream → Async → Drop** model for zero-blocking API logging.

```
Envoy → stdout → Docker buffer (non-blocking)
                       ↓
                 Vector (sidecar)
                 ├─ reads via docker.sock
                 ├─ RAM buffer, drop_newest when full
                 └─ → Loki (center) → Grafana
```

**Design goal:** logging never blocks Envoy, even during traffic spikes. Vector is CPU-capped (`0.2 cores`) so it can never compete with Envoy for resources.

**Two planes:**
- **Edge** (`docker-compose.edge.yml`): 1 Envoy + 1 Vector per node, generic compose file
- **Center** (`docker-compose.center.yml`): Loki + Grafana, single shared instance

Both join the `envoy_observability` Docker network.

## Key Files

| File | Purpose |
|------|---------|
| `upstreams.yaml` | List of 3rd-party upstreams — edit this to add/change backends |
| `envoy/envoy.yaml.tmpl` | Envoy config Jinja2 template — edit this, not `envoy.yaml` |
| `render.py` | Renders envoy.yaml from template + upstreams.yaml (replaces envsubst) |
| `vector/vector.toml` | Vector pipeline: parse JSON logs, label, ship to Loki |
| `envs/base.env` | Shared config: image versions, center ports, resource limits |
| `envs/<node>-edge.env` | Per-node config: NODE_NAME, HTTP_PORT, HTTPS_PORT, ADMIN_PORT |
| `Makefile` | Single entry point for all operations |
| `report/report.py` | Query Loki and render top-API report |
| `report/api.py` | FastAPI service — HTTP wrapper around report.py |
| `report/Dockerfile` | Build image for report-api service |

## Common Commands

```bash
make up                       # full stack
make down                     # stop all
make edge-up NODE=us          # start 1 node
make restart-edge [NODE=us]   # re-render envoy config + restart edge node
make report PERIOD=1w         # print top-API report
make help                     # all available targets
```

## Adding / Changing Upstreams

Edit `upstreams.yaml` — add an entry with `name`, `host`, `port`, `tls`. Then `make restart-edge`.

URL routing: `https://envoy:10443/{name}/*` → upstream (prefix stripped before forwarding).

## Adding a New Node

Create `envs/<name>-edge.env` with `NODE_NAME`, `HTTP_PORT`, `HTTPS_PORT`, `ADMIN_PORT`. Then `make edge-up NODE=<name>`. No changes to docker-compose files needed.

## Service Endpoints (default dev setup)

| Service | URL |
|---------|-----|
| Envoy US | http://localhost:10000 |
| Envoy NA | http://localhost:10001 |
| Loki API | http://localhost:3100 |
| Grafana  | http://localhost:3000 |
| Report API | http://localhost:5000 |

## X-Source-Service

Clients set `X-Source-Service` header. Envoy logs it as `source_service`, strips before forwarding. Vector uses it as a Loki label for per-service filtering and reporting.

Loki labels available: `instance`, `method`, `response_code`, `source_service`, `upstream`.
