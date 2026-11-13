"""Configuration from environment variables (and an optional local .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    access_key_id: str
    access_key_secret: str
    endpoint: str
    region: str
    project_id: str
    version: str
    neo4j_http: str
    neo4j_password: str


REQUIRED_FOR_DATAWORKS = (
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "DATAWORKS_ENDPOINT",
    "DATAWORKS_REGION",
    "DATAWORKS_PROJECT_ID",
)


def load_config(require_dataworks: bool = True) -> Config:
    _load_dotenv(REPO_ROOT / ".env")
    if require_dataworks:
        missing = [v for v in REQUIRED_FOR_DATAWORKS if not os.environ.get(v, "").strip()]
        if missing:
            raise RuntimeError(
                "Missing environment variables: " + ", ".join(missing) + ". Copy .env.example to .env and fill it in."
            )
    env = os.environ.get
    return Config(
        access_key_id=env("ALIYUN_ACCESS_KEY_ID", ""),
        access_key_secret=env("ALIYUN_ACCESS_KEY_SECRET", ""),
        endpoint=env("DATAWORKS_ENDPOINT", ""),
        region=env("DATAWORKS_REGION", ""),
        project_id=env("DATAWORKS_PROJECT_ID", ""),
        version=env("DATAWORKS_VERSION", "2020-05-18"),
        neo4j_http=env("NEO4J_HTTP", "http://localhost:7474/db/neo4j/tx/commit"),
        neo4j_password=env("NEO4J_PASSWORD", ""),
    )
