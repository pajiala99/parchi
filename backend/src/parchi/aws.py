"""Lazily created, cached boto3 clients.

Clients are created on first use and reused across warm Lambda invocations.
Tests (and the local dev stack) call ``reset()`` after changing the environment.
Setting ``AWS_ENDPOINT_URL`` points every client at an emulator such as moto.
"""

from __future__ import annotations

import os
from functools import cache
from typing import Any

import boto3
from botocore.config import Config

from .config import settings

_BASE = Config(
    retries={"mode": "standard", "max_attempts": 4},
    connect_timeout=3,
    read_timeout=15,
)


def _endpoint_override() -> bool:
    return bool(os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3"))


@cache
def s3() -> Any:
    # Presigned POSTs must use the regional, virtual-hosted endpoint: the legacy
    # global endpoint answers new buckets outside us-east-1 with a 307 redirect,
    # which a browser upload cannot follow. Emulators only speak path-style.
    style = "path" if _endpoint_override() else "virtual"
    return boto3.client(
        "s3",
        region_name=settings().region,
        config=_BASE.merge(Config(signature_version="s3v4", s3={"addressing_style": style})),
    )


@cache
def dynamodb() -> Any:
    """Low-level client (used for transactions)."""
    return boto3.client("dynamodb", region_name=settings().region, config=_BASE)


@cache
def table() -> Any:
    resource = boto3.resource("dynamodb", region_name=settings().region, config=_BASE)
    return resource.Table(settings().table_name)


@cache
def sns() -> Any:
    return boto3.client("sns", region_name=settings().region, config=_BASE)


@cache
def sqs() -> Any:
    return boto3.client("sqs", region_name=settings().region, config=_BASE)


@cache
def textract() -> Any:
    # Textract has low default TPS quotas; adaptive mode adds client-side rate
    # limiting on top of exponential backoff when it starts throttling.
    return boto3.client(
        "textract",
        region_name=settings().region,
        config=Config(retries={"mode": "adaptive", "max_attempts": 6}, read_timeout=60),
    )


def reset() -> None:
    """Drop cached settings and clients (tests, local dev stack)."""
    settings.cache_clear()
    for factory in (s3, dynamodb, table, sns, sqs, textract):
        getattr(factory, "cache_clear", lambda: None)()  # tests may monkeypatch a factory
