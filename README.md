# dataworks-atlas

A map of our DataWorks data warehouse: pipelines, lineage, domains and duplicated logic, in Neo4j.

> Part of the `data` repo. Org chart lives in [`../orgchart`](../orgchart/README.md) (separate Neo4j instance).

Neo4j graph of the DataWorks (PROD) scheduler nodes and their dependencies, for visualization.

- Nodes: `(:Node {id, name, type, cron, baseline, priority, owner, scheduler, resgroup})`
- Edges: `(parent:Node)-[:FEEDS]->(child:Node)`

## Roadmap

The goal is a graph that stays in sync with DataWorks, groups every pipeline into a business domain and finds duplicated logic. Full plan: [`docs/03-lineage-graph-plan.md`](docs/03-lineage-graph-plan.md).

| Phase | Status |
|---|---|
| 0. Foundations (package, config, own DataWorks client) | Done |
| 1. Extract snapshots | Planned |
| 2. Incremental load into Neo4j | Planned |
| 3. Domain classification (`domains.yaml`) | Planned |
| 4. Duplication analysis | Planned |

Setup for the new tooling:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env     # fill in the values
python -m dwgraph check  # needs the VPN
```

Until phase 2 is done, the loader below (`ingest_dataworks.py`) is still the way to load the graph.

## Prerequisites

- Docker CLI + Colima (Docker runtime) + standalone `docker-compose`
- `NEO4J_PASSWORD` defined in `.env` (this folder)
- VPN Hillstone, only for loading data from DataWorks (not for viewing)

> Note: the `docker compose` plugin is not installed here. Use `docker-compose` (with a hyphen).

## Start

```bash
cd /Users/rosa.arzabala/miclip/data/dataworks
colima start            # starts the Docker daemon (skip if already running)
docker-compose up -d    # starts Neo4j
```

Open http://localhost:7474 and log in with user `neo4j` and the `NEO4J_PASSWORD` from `.env`.

## Stop

```bash
cd /Users/rosa.arzabala/miclip/data/dataworks
docker-compose stop     # stops Neo4j, keeps data
colima stop             # optional: stops the Docker daemon to free resources
```

Data persists in `./neo4j/data`, so it is still there after stopping.

## Status and logs

```bash
docker ps                          # container should be lineage-neo4j (Up)
docker-compose logs -f neo4j       # follow logs
```

## Docker command reference

Run `docker-compose` commands from this folder (where `docker-compose.yml` lives). Container name: `lineage-neo4j`, service name: `neo4j`.

### Colima (Docker daemon)

```bash
colima start                  # start the daemon
colima stop                   # stop it (containers stop too)
colima status                 # is it running?
colima restart                # restart the daemon
colima start --cpu 4 --memory 8   # start with more resources (if Neo4j is slow)
```

### Lifecycle (docker-compose)

```bash
docker-compose up -d          # create and start in background
docker-compose stop           # stop, keep container and data
docker-compose start          # start again after stop
docker-compose restart neo4j  # restart the service
docker-compose down           # stop and remove the container (data in ./neo4j/data stays)
docker-compose pull           # download a newer image
docker-compose up -d --force-recreate   # recreate the container (e.g. after editing docker-compose.yml or .env)
docker-compose ps             # state of this project's containers
docker-compose config         # show the resolved config (checks .env is being read)
```

### Inspect

```bash
docker ps                     # running containers
docker ps -a                  # all containers, including stopped
docker-compose logs -f neo4j  # follow logs (Ctrl+C to exit)
docker-compose logs --tail 100 neo4j   # last 100 lines
docker stats lineage-neo4j    # live CPU / memory usage
docker inspect lineage-neo4j  # full container details (JSON)
docker images                 # downloaded images
```

### Get inside the container

```bash
docker exec -it lineage-neo4j bash        # shell inside Neo4j
docker exec -it lineage-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD"   # Cypher from the terminal
docker exec lineage-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (n:Node) RETURN count(n)"   # one-off query
```

(`NEO4J_PASSWORD` must be loaded in your shell: `set -a; . ./.env; set +a`)

### Cleanup

```bash
docker system df              # disk used by Docker
docker image prune            # remove unused images
docker system prune           # remove stopped containers, unused networks and dangling images
docker system prune -a --volumes   # DESTRUCTIVE: removes everything not in use, including volumes
```

### Backup and restore the graph

Stop Neo4j first so the files are consistent.

```bash
docker-compose stop
tar czf neo4j-backup-$(date +%F).tgz neo4j/data     # backup
docker-compose start

# restore
docker-compose down
rm -rf neo4j/data && tar xzf neo4j-backup-<date>.tgz
docker-compose up -d
```

> Since the graph can be rebuilt with the ingest script (about 30 seconds), a backup is rarely needed.

## Load or reload data from DataWorks

Requires the VPN Hillstone. The script wipes all `:Node` data and reloads it, so it is safe to re-run.

```bash
cd /Users/rosa.arzabala/miclip/data/dataworks
set -a; . ../../data-knowledge-base/.env; . ./.env; set +a
../../data-knowledge-base/.venv/bin/python -I ingest_dataworks.py
```

Takes about 30 seconds (about 1,600 nodes and 2,700 edges).

## Useful queries (Neo4j Browser)

```cypher
// Sample of the graph
MATCH p=(a:Node)-[:FEEDS]->(b:Node) RETURN p LIMIT 300

// Full lineage (upstream and downstream) of one node
MATCH p=(:Node {name:'<node_name>'})-[:FEEDS*]-(:Node) RETURN p

// Everything downstream of a node (impact analysis)
MATCH p=(:Node {name:'<node_name>'})-[:FEEDS*]->(:Node) RETURN p

// Nodes per baseline
MATCH (n:Node) RETURN n.baseline, count(*) AS nodes ORDER BY nodes DESC

// Nodes per type
MATCH (n:Node) RETURN n.type, count(*) AS nodes ORDER BY nodes DESC
```

## Reset everything (destructive)

```bash
docker-compose down
rm -rf ./neo4j/data     # deletes the graph; reload with the ingest script
docker-compose up -d
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Run `colima start` |
| `unknown shorthand flag: 'd'` | You typed `docker compose`; use `docker-compose` |
| `http://localhost:7474` does not load | Wait about 20 seconds after start, then check `docker ps` |
| Ingest script times out | Check that the VPN Hillstone is connected |
| After a Mac reboot nothing runs | Run `colima start`; the container restarts on its own |
| `port is already allocated` (7474 / 7687) | Another Neo4j is running (e.g. the orgchart one). Run `docker ps`, stop it, or change the port in `docker-compose.yml` |
| `define NEO4J_PASSWORD en .env` | `.env` is missing or empty; add `NEO4J_PASSWORD=...` and run `docker-compose up -d` again |
| Changed the password in `.env` but login fails | Neo4j keeps the password from the first start in `./neo4j/data`; use the old one or reset (see below) |
| Container keeps restarting | `docker-compose logs --tail 100 neo4j` to see why; often a permissions issue on `./neo4j/data` |
| Disk full / Docker slow | `docker system df`, then `docker system prune` |

## Files

- `docker-compose.yml`: Neo4j 5 Community service
- `ingest_dataworks.py`: loads DataWorks nodes and dependencies into Neo4j
- `.env`: `NEO4J_PASSWORD` (git-ignored)
- `neo4j/`: database files (git-ignored)
