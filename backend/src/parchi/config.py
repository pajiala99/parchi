"""Runtime settings, read once from the Lambda environment.

Every value has a default so the package imports cleanly in tests and in the
local dev stack; template.yaml sets the real values per stage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    stage: str
    region: str
    table_name: str
    upload_bucket: str
    topic_arn: str
    ingest_queue_url: str
    ocr_engine: str
    app_url: str
    max_upload_bytes: int
    daily_limit_workspace: int
    daily_limit_global: int
    max_receive_count: int


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        return default


@lru_cache(maxsize=1)
def settings() -> Settings:
    env = os.environ
    return Settings(
        stage=env.get("STAGE", "dev"),
        region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "ap-south-1",
        table_name=env.get("TABLE_NAME", "parchi-dev-documents"),
        upload_bucket=env.get("UPLOAD_BUCKET", "parchi-dev-uploads"),
        topic_arn=env.get("TOPIC_ARN", ""),
        ingest_queue_url=env.get("INGEST_QUEUE_URL", ""),
        ocr_engine=env.get("OCR_ENGINE", "textract").strip().lower(),
        app_url=env.get("APP_URL", "").rstrip("/"),
        # CloudFormation has no arithmetic, so the template passes megabytes.
        max_upload_bytes=_int("MAX_UPLOAD_MB", 0) * 1024 * 1024 or _int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
        daily_limit_workspace=_int("DAILY_UPLOAD_LIMIT_WORKSPACE", 20),
        daily_limit_global=_int("DAILY_UPLOAD_LIMIT_GLOBAL", 200),
        max_receive_count=_int("MAX_RECEIVE_COUNT", 3),
    )
