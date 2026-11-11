"""End to end over real HTTP against a moto server, the way a browser and AWS would drive it.

presigned POST upload -> S3 ObjectCreated notification -> SQS -> processor Lambda
-> Textract-shaped extraction -> DynamoDB -> SNS (filtered by owner) -> API read.
"""

from __future__ import annotations

import json
import socket

import pytest
import requests

from conftest import KEY_A, KEY_B, provision
from fakes import api
from parchi import aws
from parchi.auth import owner_from_key
from parchi.handlers import documents, processor, subscription, uploads


@pytest.fixture
def moto_server(monkeypatch):
    from moto.server import ThreadedMotoServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    requests.post(f"{endpoint}/moto-api/reset", timeout=5)
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
    aws.reset()
    try:
        yield endpoint
    finally:
        server.stop()


def _deliver(cloud):
    """Emulate the Lambda SQS event source mapping for whatever is on the ingest queue."""
    messages = cloud.sqs.receive_message(
        QueueUrl=cloud.ingest_url, MaxNumberOfMessages=10, AttributeNames=["ApproximateReceiveCount"]
    ).get("Messages", [])
    records = [
        {
            "messageId": m["MessageId"],
            "receiptHandle": m["ReceiptHandle"],
            "body": m["Body"],
            "attributes": m["Attributes"],
        }
        for m in messages
    ]
    result = processor.handler({"Records": records}, None)
    for message in messages:
        cloud.sqs.delete_message(QueueUrl=cloud.ingest_url, ReceiptHandle=message["ReceiptHandle"])
    return result, records


def test_browser_upload_to_filed_document(moto_server, monkeypatch, fake_engine):
    cloud = provision()
    monkeypatch.setenv("TOPIC_ARN", cloud.topic_arn)
    monkeypatch.setenv("INGEST_QUEUE_URL", cloud.ingest_url)
    aws.reset()
    cloud.drain(cloud.ingest_url)  # the s3:TestEvent sent when notifications were configured

    owner_a = owner_from_key(KEY_A)
    inbox_a, inbox_b = cloud.mailbox(owner_a), cloud.mailbox(owner_from_key(KEY_B))
    assert api(subscription.handler, "PUT", "/api/subscription", body={"email": "a@example.com"})[0] == 200

    # 1. The browser asks for an upload URL, then posts the file straight to S3.
    content = b"\x89PNG\r\n\x1a\n" + b"pretend this is a pharmacy bill" * 20
    status, body = api(
        uploads.handler,
        "POST",
        "/api/uploads",
        body={"fileName": "pharmacy bill.png", "contentType": "image/png", "size": len(content)},
    )
    assert status == 201 and body["upload"]["url"].startswith(moto_server)
    response = requests.post(
        body["upload"]["url"],
        data=body["upload"]["fields"],
        files={"file": ("pharmacy bill.png", content, "image/png")},  # the file field must come last
        timeout=10,
    )
    assert response.status_code == 204

    # 2. S3 published an ObjectCreated event to SQS; the processor consumes it.
    result, records = _deliver(cloud)
    assert len(records) == 1 and result == {"batchItemFailures": []}
    assert json.loads(records[0]["body"])["Records"][0]["eventName"].startswith("ObjectCreated")

    # 3. The dashboard reads the filed document; only workspace A was emailed.
    status, document = api(documents.handler, "GET", "/api/documents/{id}", doc_id=body["documentId"])
    assert status == 200
    assert document["status"] == "COMPLETED" and document["docType"] == "MEDICAL_BILL"
    assert document["total"] == 607.42 and document["needsReview"] is False
    assert requests.get(document["previewUrl"], timeout=5).content == content
    (email,) = cloud.drain(inbox_a)
    assert email["MessageAttributes"]["owner"]["Value"] == owner_a
    assert cloud.drain(inbox_b) == []
