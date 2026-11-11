"""The SQS-triggered processor: happy path, retries, idempotency and failure handling."""

from __future__ import annotations

import json
import time
from decimal import Decimal

from conftest import BUCKET
from fakes import seed_document, sqs_record
from parchi import store
from parchi.engines import PermanentError
from parchi.handlers import processor
from parchi.ids import new_id


def _run(*records):
    return processor.handler({"Records": list(records)}, None)


def _subscribe(owner, email="a@example.com"):
    store.save_profile(owner, email=email, subscriptionArn="arn:aws:sns:ap-south-1:123456789012:t:x")


def test_files_a_document_and_emails_its_owner(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    _subscribe(owner)
    mine, everyone = cloud.mailbox(owner), cloud.mailbox()
    other = cloud.mailbox("f" * 32)

    assert _run(sqs_record(s3_key)) == {"batchItemFailures": []}

    item = store.get_document(owner, document_id)
    assert item["status"] == "COMPLETED"
    assert (item["docType"], item["vendor"], item["total"]) == ("MEDICAL_BILL", "SANJEEVANI MEDICOS", Decimal("607.42"))
    assert item["attempts"] == 1 and item["notified"] is True and "leaseUntil" not in item
    assert set(item["timings"]) == {"queueMs", "extractMs", "totalMs"}
    assert fake_engine.calls == [(BUCKET, s3_key, "image/png")]

    (message,) = cloud.drain(mine)
    assert message["MessageAttributes"]["owner"]["Value"] == owner
    assert "Rs. 607.42" in message["Subject"] and "SANJEEVANI MEDICOS" in message["Message"]
    assert len(cloud.drain(everyone)) == 1
    assert cloud.drain(other) == []  # the filter policy kept it out of someone else's inbox


def test_no_email_without_a_subscription(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    inbox = cloud.mailbox()
    _run(sqs_record(s3_key))
    assert store.get_document(owner, document_id)["notified"] is False
    assert cloud.drain(inbox) == []


def test_duplicate_delivery_is_skipped(cloud, fake_engine):
    _, _, s3_key = seed_document(cloud)
    _run(sqs_record(s3_key))
    assert _run(sqs_record(s3_key)) == {"batchItemFailures": []}
    assert len(fake_engine.calls) == 1


def test_an_active_lease_blocks_a_concurrent_copy_but_a_stale_one_does_not(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    store._update(owner, document_id, {"status": "PROCESSING", "leaseUntil": int(time.time()) + 60})
    _run(sqs_record(s3_key))
    assert fake_engine.calls == []
    store._update(owner, document_id, {"leaseUntil": int(time.time()) - 1})
    _run(sqs_record(s3_key))
    assert store.get_document(owner, document_id)["status"] == "COMPLETED"


def test_unreadable_document_fails_without_retry(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    _subscribe(owner)
    inbox = cloud.mailbox(owner)
    fake_engine.error = PermanentError("No text could be read")
    assert _run(sqs_record(s3_key)) == {"batchItemFailures": []}
    item = store.get_document(owner, document_id)
    assert item["status"] == "FAILED" and item["error"] == "No text could be read"
    (message,) = cloud.drain(inbox)
    assert "could not read" in message["Subject"]


def test_transient_error_is_retried_by_sqs(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    fake_engine.error = RuntimeError("ThrottlingException")
    record = sqs_record(s3_key, receive_count=1)
    assert _run(record) == {"batchItemFailures": [{"itemIdentifier": record["messageId"]}]}
    item = store.get_document(owner, document_id)
    assert item["status"] == "QUEUED" and "will retry" in item["lastError"]
    fake_engine.error = None  # the redelivery succeeds
    _run(sqs_record(s3_key, receive_count=2))
    assert store.get_document(owner, document_id)["status"] == "COMPLETED"


def test_last_attempt_marks_the_document_failed(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    fake_engine.error = RuntimeError("still throttled")
    record = sqs_record(s3_key, receive_count=3)
    assert _run(record)["batchItemFailures"] == [{"itemIdentifier": record["messageId"]}]
    item = store.get_document(owner, document_id)
    assert item["status"] == "FAILED" and "dead-letter" in item["error"]


def test_one_bad_message_does_not_fail_the_batch(cloud, fake_engine):
    _, _, good_key = seed_document(cloud)
    bad = sqs_record("x", body="{not json")
    result = _run(sqs_record(good_key), bad)
    assert result == {"batchItemFailures": [{"itemIdentifier": bad["messageId"]}]}


def test_ignored_messages(cloud, fake_engine):
    test_event = sqs_record("x", body=json.dumps({"Service": "Amazon S3", "Event": "s3:TestEvent"}))
    foreign = sqs_record("not/our/layout.png")
    removed = sqs_record("x", event_name="ObjectRemoved:Delete")
    deleted = sqs_record(f"uploads/{'a' * 32}/{new_id()}/gone.png")  # no record for it
    assert _run(test_event, foreign, removed, deleted) == {"batchItemFailures": []}
    assert fake_engine.calls == []


def test_document_deleted_while_processing(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    fake_engine.hook = lambda bucket, key: store.delete_document(owner, document_id)
    assert _run(sqs_record(s3_key)) == {"batchItemFailures": []}
    assert store.get_document(owner, document_id) is None


def test_reprocessing_runs_the_engine_again(cloud, fake_engine):
    owner, document_id, s3_key = seed_document(cloud)
    _run(sqs_record(s3_key))
    store.apply_edits(owner, document_id, {"total": Decimal("1.00"), "notes": "keep me"})
    assert store.queue_reprocess(owner, document_id)["status"] == "QUEUED"
    _run(sqs_record(s3_key, event_name="ObjectCreated:Reprocess"))
    item = store.get_document(owner, document_id)
    assert item["status"] == "COMPLETED" and item["attempts"] == 2 and len(fake_engine.calls) == 2
    # The new extraction replaces the corrections; the user's notes stay.
    assert item["total"] == Decimal("607.42") and "editedFields" not in item and "reviewed" not in item
    assert item["notes"] == "keep me"
