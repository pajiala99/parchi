"""Structured JSON logs and CloudWatch Embedded Metric Format (EMF) metrics.

EMF needs no API call and no extra permission: a JSON line with an ``_aws``
block printed to stdout becomes a CloudWatch metric when Lambda ships the log.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any

from .config import settings

NAMESPACE = "Parchi"


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(level: str, message: str, **fields: Any) -> None:
    _emit({"level": level, "message": message, "stage": settings().stage, **fields})


def info(message: str, **fields: Any) -> None:
    log("INFO", message, **fields)


def warn(message: str, **fields: Any) -> None:
    log("WARN", message, **fields)


def error(message: str, exc: BaseException | None = None, **fields: Any) -> None:
    if exc is not None:
        fields["error"] = f"{type(exc).__name__}: {exc}"
        fields["trace"] = "".join(traceback.format_exception(exc))[-4000:]
    log("ERROR", message, **fields)


def metric(name: str, value: float, unit: str = "Count", **dimensions: str) -> None:
    """Publish one metric under the Stage dimension (and Stage+extra, if given)."""
    dims: dict[str, str] = {"Stage": settings().stage, **dimensions}
    dimension_sets = [["Stage"]]
    if dimensions:
        dimension_sets.append(["Stage", *dimensions.keys()])
    _emit(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": dimension_sets,
                        "Metrics": [{"Name": name, "Unit": unit}],
                    }
                ],
            },
            name: value,
            **dims,
        }
    )
