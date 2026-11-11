"""Shared fixtures: a clean environment per test and a moto-backed copy of the stack."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from moto import mock_aws

from parchi import aws, engines

REGION = "ap-south-1"
BUCKET = "parchi-test-uploads"
TABLE = "parchi-test-documents"

ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": REGION,
    "AWS_REGION": REGION,
    "STAGE": "test",
    "TABLE_NAME": TABLE,
    "UPLOAD_BUCKET": BUCKET,
    "OCR_ENGINE": "fake",
    "APP_URL": "https://d111111abcdef8.cloudfront.net",
    "DAILY_UPLOAD_LIMIT_WORKSPACE": "5",
    "DAILY_UPLOAD_LIMIT_GLOBAL": "8",
    "MAX_RECEIVE_COUNT": "3",
}

KEY_A = "A" * 43
KEY_B = "B" * 43


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    for name in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_PROFILE", "MAX_UPLOAD_MB", "MAX_UPLOAD_BYTES"):
        monkeypatch.delenv(name, raising=False)
    aws.reset()
    yield
    aws.reset()


@dataclass
class Cloud:
    s3: Any
    sqs: Any
    sns: Any
    ingest_url: str
    dlq_url: str
    topic_arn: str

    def mailbox(self, owner: str | None = None) -> str:
        """An SQS queue subscribed to the notifications topic (optionally filtered by owner)."""
        name = f"mailbox-{owner or 'all'}"
        url = self.sqs.create_queue(QueueName=name)["QueueUrl"]
        arn = self.sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        attributes = {"RawMessageDelivery": "false"}
        if owner:
            attributes["FilterPolicy"] = json.dumps({"owner": [owner]})
            attributes["FilterPolicyScope"] = "MessageAttributes"
        self.sns.subscribe(TopicArn=self.topic_arn, Protocol="sqs", Endpoint=arn, Attributes=attributes)
        return url

    def drain(self, url: str) -> list[dict[str, Any]]:
        messages = []
        while True:
            batch = self.sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
            if not batch.get("Messages"):
                return messages
            for message in batch["Messages"]:
                messages.append(json.loads(message["Body"]))
                self.sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])


def provision(region: str = REGION) -> Cloud:
    """Create the template's data plane: bucket -> queue (+DLQ) notifications, table, topic."""
    s3 = boto3.client("s3", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    sns = boto3.client("sns", region_name=region)
    dynamodb = boto3.client("dynamodb", region_name=region)

    dlq_url = sqs.create_queue(QueueName="parchi-test-ingest-dlq")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    ingest_url = sqs.create_queue(
        QueueName="parchi-test-ingest",
        Attributes={"RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"})},
    )["QueueUrl"]
    ingest_arn = sqs.get_queue_attributes(QueueUrl=ingest_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": region})
    s3.put_bucket_notification_configuration(
        Bucket=BUCKET,
        NotificationConfiguration={
            "QueueConfigurations": [
                {
                    "QueueArn": ingest_arn,
                    "Events": ["s3:ObjectCreated:*"],
                    "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "uploads/"}]}},
                }
            ]
        },
    )
    dynamodb.create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
    )
    topic_arn = sns.create_topic(Name="parchi-test-notifications")["TopicArn"]
    return Cloud(s3, sqs, sns, ingest_url, dlq_url, topic_arn)


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch):
    with mock_aws():
        stack = provision()
        # S3 sends a test event when notifications are configured; tests start with an empty queue.
        stack.drain(stack.ingest_url)
        monkeypatch.setenv("TOPIC_ARN", stack.topic_arn)
        monkeypatch.setenv("INGEST_QUEUE_URL", stack.ingest_url)
        aws.reset()
        yield stack


@pytest.fixture
def fake_engine():
    """Register a controllable OCR engine under OCR_ENGINE=fake."""
    from fakes import FakeEngine

    engine = FakeEngine()
    engines.register("fake", lambda: engine)
    yield engine
    engines._CUSTOM.pop("fake", None)
