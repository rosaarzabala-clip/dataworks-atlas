"""Carga los nodos de DataWorks (PROD) y sus dependencias en Neo4j.

Modelo:
  (:Node {id, name, type, cron, baseline, priority, owner, scheduler, resgroup})
  (:Node)-[:FEEDS]->(:Node)   parent alimenta a child (dependencia de scheduling)

Reutiliza el cliente firmado (solo lectura) del MCP data-team.
Uso (con VPN Hillstone):
  cd /Users/rosa.arzabala/miclip/data/dataworks
  set -a; . ../../data-knowledge-base/.env; . ./.env; set +a
  ../../data-knowledge-base/.venv/bin/python ingest_dataworks.py
"""

import base64
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

KB = os.environ.get("DATA_KB_PATH", "/Users/rosa.arzabala/miclip/data-knowledge-base")
sys.path.insert(0, os.path.join(KB, "src"))
from data_mcp_server.config import config  # noqa: E402
from data_mcp_server.dataworks import _call  # noqa: E402

NEO4J_URL = os.environ.get("NEO4J_HTTP", "http://localhost:7474/db/neo4j/tx/commit")
AUTH = base64.b64encode(f"neo4j:{os.environ['NEO4J_PASSWORD']}".encode()).decode()
PAGE = 100


def cypher(statement, **params):
    body = json.dumps({"statements": [{"statement": statement, "parameters": params}]}).encode()
    req = urllib.request.Request(
        NEO4J_URL, body, {"Content-Type": "application/json", "Authorization": f"Basic {AUTH}"}
    )
    out = json.load(urllib.request.urlopen(req, timeout=120))
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out


def list_nodes():
    nodes, page = [], 1
    while True:
        r = _call("ListNodes", {"ProjectId": config.dataworks_project_id, "ProjectEnv": "PROD",
                                "PageNumber": page, "PageSize": PAGE})
        data = r.get("Data") or {}
        batch = data.get("Nodes") or []
        nodes += batch
        if not batch or len(nodes) >= (data.get("TotalCount") or 0):
            return nodes
        page += 1


def parents(node_id):
    for attempt in range(3):
        try:
            r = _call("GetNodeParents", {"ProjectEnv": "PROD", "NodeId": node_id})
            return node_id, [n["NodeId"] for n in (r.get("Data") or {}).get("Nodes") or []]
        except Exception as e:  # reintento simple ante timeouts de la VPN
            err = e
    print(f"  ! parents({node_id}) fallo: {err}", file=sys.stderr)
    return node_id, None


def main():
    print("Listando nodos...")
    nodes = list_nodes()
    print(f"  {len(nodes)} nodos")

    print("Leyendo dependencias...")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda n: parents(n["NodeId"]), nodes))
    failed = [i for i, p in results if p is None]
    edges = [{"p": p, "c": i} for i, ps in results if ps for p in ps]
    print(f"  {len(edges)} aristas, {len(failed)} nodos sin leer")

    rows = [{
        "id": n["NodeId"], "name": n.get("NodeName"), "type": n.get("ProgramType"),
        "cron": n.get("CronExpress"), "baseline": n.get("BaselineId"),
        "priority": n.get("Priority"), "owner": n.get("OwnerId"),
        "scheduler": n.get("SchedulerType"), "resgroup": n.get("ResGroupName"),
    } for n in nodes]

    print("Cargando en Neo4j...")
    cypher("CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE")
    cypher("MATCH (n:Node) DETACH DELETE n")  # recarga completa e idempotente
    for i in range(0, len(rows), 500):
        cypher("UNWIND $rows AS r MERGE (n:Node {id: r.id}) SET n += r", rows=rows[i:i + 500])
    for i in range(0, len(edges), 1000):
        cypher("UNWIND $e AS e MATCH (p:Node {id: e.p}) MATCH (c:Node {id: e.c}) "
               "MERGE (p)-[:FEEDS]->(c)", e=edges[i:i + 1000])
    out = cypher("MATCH (n:Node) OPTIONAL MATCH (n)-[r:FEEDS]->() RETURN count(DISTINCT n), count(r)")
    print("Neo4j (nodos, aristas):", out["results"][0]["data"][0]["row"])


if __name__ == "__main__":
    main()
