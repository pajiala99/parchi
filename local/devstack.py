"""Run the whole pipeline on a laptop, with no AWS account.

    python local/devstack.py            # then: cd frontend && npm run dev  ->  http://localhost:5173

What runs where:
  * moto (an AWS emulator) serves S3, SQS, SNS and DynamoDB on 127.0.0.1:5055.
    The bucket really sends ObjectCreated events to the queue, as in AWS.
  * An API Gateway stand-in on 127.0.0.1:3001 reads the routes from template.yaml
    and calls the same Lambda handler functions that are deployed, with HTTP API
    (payload v2) events.
  * A poller thread plays the Lambda SQS event source mapping: it receives batches
    from the ingest queue and invokes the processor, deleting only what succeeded.
  * OCR runs on Tesseract if it is installed, else on the replay engine (sample bills).
  * SNS emails can't leave the laptop: a mailbox queue subscribed to the topic
    collects them, and GET /api/dev/outbox shows each workspace its own.
"""

from __future__ import annotations

import argparse
import collections
import importlib
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

REGION = "ap-south-1"
STAGE = "local"
NAMES = {
    "bucket": "parchi-local-uploads",
    "table": "parchi-local-documents",
    "ingest": "parchi-local-ingest",
    "dlq": "parchi-local-ingest-dlq",
    "topic": "parchi-local-notifications",
    "mailbox": "parchi-local-mailbox",
}
OUTBOX: collections.deque = collections.deque(maxlen=200)


# --------------------------------------------------------------- template


class CfnLoader(yaml.SafeLoader):
    """CloudFormation YAML with !Ref, !Sub, !GetAtt ... read as plain dicts."""


def _cfn_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


CfnLoader.add_multi_constructor("!", _cfn_tag)


def load_routes() -> list[tuple[str, str, re.Pattern, str]]:
    """(method, route path, regex, handler) for every HttpApi event in template.yaml."""
    template = yaml.load((ROOT / "template.yaml").read_text(), Loader=CfnLoader)
    routes = []
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::Serverless::Function":
            continue
        properties = resource["Properties"]
        for event in properties.get("Events", {}).values():
            if event["Type"] != "HttpApi":
                continue
            method, path = event["Properties"]["Method"], event["Properties"]["Path"]
            pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")
            routes.append((method, path, pattern, properties["Handler"]))
    return routes


# -------------------------------------------------------------- resources


def provision() -> dict[str, str]:
    import boto3

    s3, sqs = boto3.client("s3", region_name=REGION), boto3.client("sqs", region_name=REGION)
    sns, dynamodb = boto3.client("sns", region_name=REGION), boto3.client("dynamodb", region_name=REGION)

    def queue_arn(url: str) -> str:
        return sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

    dlq = sqs.create_queue(QueueName=NAMES["dlq"])["QueueUrl"]
    ingest = sqs.create_queue(
        QueueName=NAMES["ingest"],
        Attributes={
            "VisibilityTimeout": "20",  # short locally so retries are quick to watch
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": queue_arn(dlq), "maxReceiveCount": "3"}),
        },
    )["QueueUrl"]
    s3.create_bucket(Bucket=NAMES["bucket"], CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_bucket_cors(
        Bucket=NAMES["bucket"],
        CORSConfiguration={
            "CORSRules": [{"AllowedMethods": ["POST", "GET", "HEAD"], "AllowedOrigins": ["*"], "AllowedHeaders": ["*"]}]
        },
    )
    s3.put_bucket_notification_configuration(
        Bucket=NAMES["bucket"],
        NotificationConfiguration={
            "QueueConfigurations": [
                {
                    "QueueArn": queue_arn(ingest),
                    "Events": ["s3:ObjectCreated:*"],
                    "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "uploads/"}]}},
                }
            ]
        },
    )
    dynamodb.create_table(
        TableName=NAMES["table"],
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
    )
    topic = sns.create_topic(Name=NAMES["topic"])["TopicArn"]
    mailbox = sqs.create_queue(QueueName=NAMES["mailbox"])["QueueUrl"]
    sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=queue_arn(mailbox))
    return {"ingest": ingest, "dlq": dlq, "topic": topic, "mailbox": mailbox}


# ------------------------------------------------- event source mapping


class Poller(threading.Thread):
    """Plays the Lambda SQS event source mapping (batch size 5) and drains the mailbox."""

    def __init__(self, urls: dict[str, str]) -> None:
        super().__init__(daemon=True)
        self.urls = urls
        self.stopped = threading.Event()

    def run(self) -> None:
        import boto3

        from parchi.handlers import processor

        sqs = boto3.client("sqs", region_name=REGION)
        while not self.stopped.is_set():
            try:
                self._collect_mail(sqs)
                batch = sqs.receive_message(
                    QueueUrl=self.urls["ingest"],
                    MaxNumberOfMessages=5,
                    WaitTimeSeconds=1,
                    AttributeNames=["All"],
                ).get("Messages", [])
                if not batch:
                    continue
                records = [
                    {
                        "messageId": m["MessageId"],
                        "receiptHandle": m["ReceiptHandle"],
                        "body": m["Body"],
                        "attributes": m.get("Attributes", {}),
                        "eventSource": "aws:sqs",
                        "awsRegion": REGION,
                    }
                    for m in batch
                ]
                result = processor.handler({"Records": records}, None)
                failed = {f["itemIdentifier"] for f in result.get("batchItemFailures", [])}
                for message in batch:
                    if message["MessageId"] not in failed:  # failures reappear after the visibility timeout
                        sqs.delete_message(QueueUrl=self.urls["ingest"], ReceiptHandle=message["ReceiptHandle"])
            except Exception as exc:
                print(f"[devstack] poller error: {exc}", file=sys.stderr)
                time.sleep(1)

    def _collect_mail(self, sqs) -> None:
        messages = sqs.receive_message(QueueUrl=self.urls["mailbox"], MaxNumberOfMessages=10).get("Messages", [])
        for message in messages:
            envelope = json.loads(message["Body"])
            attributes = envelope.get("MessageAttributes", {})
            OUTBOX.appendleft(
                {
                    "id": envelope.get("MessageId", message["MessageId"]),
                    "owner": attributes.get("owner", {}).get("Value"),
                    "event": attributes.get("event", {}).get("Value"),
                    "documentId": attributes.get("documentId", {}).get("Value"),
                    "subject": envelope.get("Subject"),
                    "body": envelope.get("Message"),
                    "sentAt": envelope.get("Timestamp"),
                }
            )
            sqs.delete_message(QueueUrl=self.urls["mailbox"], ReceiptHandle=message["ReceiptHandle"])


# ------------------------------------------------- API Gateway stand-in


def make_server(routes, port: int, static_dir: Path) -> ThreadingHTTPServer:
    from parchi import store
    from parchi.auth import KEY_HEADER, is_valid_key, owner_from_key

    handlers: dict[str, object] = {}

    def resolve(path: str):
        if path not in handlers:
            module, _, attribute = path.rpartition(".")
            handlers[path] = getattr(importlib.import_module(module), attribute)
        return handlers[path]

    class Handler(BaseHTTPRequestHandler):
        server_version = "parchi-devstack"

        def log_message(self, fmt, *args):  # quieter than the default
            if not self.path.startswith("/api/documents") or self.command != "GET":
                status = args[1] if len(args) > 1 else ""
                sys.stderr.write(f"[api] {self.command} {self.path.split('?')[0]} -> {status}\n")

        def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _api(self) -> None:
            url = urlsplit(self.path)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else None
            headers = {k.lower(): v for k, v in self.headers.items()}

            if url.path == "/api/dev/outbox":
                key = headers.get(KEY_HEADER)
                owner = owner_from_key(key) if is_valid_key(key) else None
                profile = store.get_profile(owner) if owner else {}
                mail = [{**m, "to": profile.get("email")} for m in OUTBOX if m["owner"] == owner]
                return self._send(200, json.dumps({"items": mail}).encode(), {"content-type": "application/json"})

            for method, route, pattern, handler_path in routes:
                match = pattern.match(url.path)
                if not match or method != self.command:
                    continue
                event = {
                    "version": "2.0",
                    "routeKey": f"{method} {route}",
                    "rawPath": url.path,
                    "rawQueryString": url.query,
                    "headers": headers,
                    "queryStringParameters": dict(parse_qsl(url.query)) or None,
                    "pathParameters": {k: unquote(v) for k, v in match.groupdict().items()} or None,
                    "requestContext": {
                        "http": {"method": method, "path": url.path, "sourceIp": self.client_address[0]},
                        "requestId": str(uuid.uuid4()),
                        "routeKey": f"{method} {route}",
                        "stage": "$default",
                        "timeEpoch": int(time.time() * 1000),
                    },
                    "body": body,
                    "isBase64Encoded": False,
                }
                result = resolve(handler_path)(event, None)
                payload = (result.get("body") or "").encode("utf-8")
                return self._send(result["statusCode"], payload, result.get("headers", {}))
            message = {"message": "Not Found"}  # what API Gateway answers for unknown routes
            self._send(404, json.dumps(message).encode(), {"content-type": "application/json"})

        def _static(self) -> None:
            path = urlsplit(self.path).path
            target = (static_dir / path.lstrip("/")).resolve()
            if path == "/" or not target.is_file() or static_dir.resolve() not in target.parents:
                target = static_dir / "index.html"
            if not target.is_file():
                note = b"Dashboard not built. Run: cd frontend && npm run dev (or npm run build)"
                return self._send(404, note, {"content-type": "text/plain"})
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), {"content-type": kind})

        def _dispatch(self) -> None:
            try:
                if self.path.startswith("/api/"):
                    self._api()
                elif self.command in ("GET", "HEAD"):
                    self._static()
                else:
                    self._send(405, b"", {})
            except Exception as exc:
                print(f"[devstack] request failed: {exc}", file=sys.stderr)
                self._send(500, b'{"message":"Internal Server Error"}', {"content-type": "application/json"})

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _dispatch

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


# ------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-port", type=int, default=3001)
    parser.add_argument("--aws-port", type=int, default=5055)
    parser.add_argument("--engine", choices=["tesseract", "replay"], default=None)
    parser.add_argument("--app-url", default="http://localhost:5173")
    args = parser.parse_args()

    engine = args.engine or ("tesseract" if shutil.which("tesseract") else "replay")
    endpoint = f"http://127.0.0.1:{args.aws_port}"
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": "local",
            "AWS_SECRET_ACCESS_KEY": "local",
            "AWS_DEFAULT_REGION": REGION,
            "AWS_REGION": REGION,
            "AWS_ENDPOINT_URL": endpoint,
            "STAGE": STAGE,
            "TABLE_NAME": NAMES["table"],
            "UPLOAD_BUCKET": NAMES["bucket"],
            "OCR_ENGINE": engine,
            "APP_URL": args.app_url,
            "MAX_UPLOAD_MB": "10",
            "DAILY_UPLOAD_LIMIT_WORKSPACE": "100",
            "DAILY_UPLOAD_LIMIT_GLOBAL": "1000",
            "MAX_RECEIVE_COUNT": "3",
            "PROCESSING_LEASE_SECONDS": "15",
        }
    )
    os.environ.pop("AWS_PROFILE", None)

    from moto.server import ThreadedMotoServer

    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=args.aws_port, verbose=False)
    moto.start()
    urls = provision()
    os.environ["TOPIC_ARN"] = urls["topic"]
    os.environ["INGEST_QUEUE_URL"] = urls["ingest"]

    from parchi import aws

    aws.reset()
    poller = Poller(urls)
    poller.start()
    routes = load_routes()
    server = make_server(routes, args.api_port, ROOT / "frontend" / "dist")
    print(
        f"\n  Parchi devstack ready\n"
        f"  - API (Lambda handlers, {len(routes)} routes from template.yaml): http://127.0.0.1:{args.api_port}/api/health\n"
        f"  - Emulated S3 / SQS / SNS / DynamoDB (moto):     {endpoint}\n"
        f"  - OCR engine: {engine}\n"
        f"  - Dashboard: cd frontend && npm run dev  ->  {args.app_url}\n",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stopped.set()
        server.server_close()
        moto.stop()


if __name__ == "__main__":
    main()
