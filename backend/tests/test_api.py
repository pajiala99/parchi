"""API handlers against a moto copy of the stack: uploads, documents, stats, subscriptions."""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import boto3
import pytest
from botocore.exceptions import ClientError

from conftest import BUCKET, KEY_A, KEY_B, TABLE
from fakes import api, seed_document
from parchi import aws, store
from parchi.auth import owner_from_key
from parchi.handlers import documents, processor, subscription, uploads
from parchi.ids import new_id, now_iso

OWNER_A = owner_from_key(KEY_A)

# ------------------------------------------------------------------ uploads


def _upload(key=KEY_A, **body):
    payload = {"fileName": "bill.png", "contentType": "image/png", "size": 2048, **body}
    return api(uploads.handler, "POST", "/api/uploads", key=key, body=payload)


def test_upload_issues_a_presigned_post(cloud):
    status, body = _upload(fileName="My Bill (1).JPG", contentType="image/jpg")
    assert status == 201
    fields = body["upload"]["fields"]
    assert fields["key"] == f"uploads/{OWNER_A}/{body['documentId']}/My_Bill_1.jpg"
    assert fields["Content-Type"] == "image/jpeg"
    policy = json.loads(base64.b64decode(fields["policy"]))
    assert ["content-length-range", 1, 10 * 1024 * 1024] in policy["conditions"]
    assert {"Content-Type": "image/jpeg"} in policy["conditions"]
    item = store.get_document(OWNER_A, body["documentId"])
    assert item["status"] == store.AWAITING_UPLOAD and "expiresAt" in item  # TTL cleans abandoned uploads


@pytest.mark.parametrize(
    ("key", "body", "status", "code"),
    [
        (None, {}, 401, "missing_workspace_key"),
        ("too-short", {}, 401, "missing_workspace_key"),
        (KEY_A, {"fileName": ""}, 400, "invalid_file_name"),
        (KEY_A, {"contentType": "image/heic"}, 415, "unsupported_type"),
        (KEY_A, {"size": 11 * 1024 * 1024}, 413, "too_large"),
        (KEY_A, {"size": True}, 400, "invalid_size"),
        (KEY_A, {"size": 0}, 400, "invalid_size"),
    ],
)
def test_upload_validation(cloud, key, body, status, code):
    got_status, got = _upload(key=key, **body)
    assert (got_status, got["error"]["code"]) == (status, code)


def test_invalid_json_and_unknown_route(cloud):
    status, body = api(uploads.handler, "POST", "/api/uploads", raw_body="{nope")
    assert (status, body["error"]["code"]) == (400, "invalid_json")
    status, body = api(uploads.handler, "GET", "/api/nothing")
    assert status == 404


def test_daily_quota_is_per_workspace_and_global(cloud):
    for _ in range(5):  # workspace limit is 5 in tests
        assert _upload(key=KEY_A)[0] == 201
    status, body = _upload(key=KEY_A)
    assert (status, body["error"]["scope"]) == (429, "workspace")

    for _ in range(3):  # global limit is 8: 5 + 3
        assert _upload(key=KEY_B)[0] == 201
    status, body = _upload(key=KEY_B)
    assert (status, body["error"]["scope"]) == (429, "global")
    # The transaction is all-or-nothing: the rejected request did not count for workspace B.
    assert store.quota_used(owner_from_key(KEY_B), "UPLOAD") == 3


def test_max_upload_mb_setting(cloud, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "2")
    aws.reset()
    status, body = _upload(size=3 * 1024 * 1024)
    assert (status, body["maxBytes"] if status == 201 else body["error"]["maxBytes"]) == (413, 2 * 1024 * 1024)


# ---------------------------------------------------------------- documents


def _file(cloud, fake_engine, key=KEY_A, now_ms=None, name="bill.png"):
    _, document_id, s3_key = seed_document(cloud, key=key, now_ms=now_ms, name=name)
    processor.process_object(BUCKET, s3_key, size=24, event_time=now_iso(), final_attempt=False)
    return document_id


def test_health_needs_no_key(cloud):
    status, body = api(documents.handler, "GET", "/api/health", key=None)
    assert status == 200 and body["ok"] and body["stage"] == "test" and body["engine"] == "fake"


def test_list_is_newest_first_with_pagination(cloud, fake_engine):
    ids = [_file(cloud, fake_engine, now_ms=1_800_000_000_000 + i) for i in range(3)]
    status, page = api(documents.handler, "GET", "/api/documents", query={"limit": "2"})
    assert status == 200
    assert [d["documentId"] for d in page["items"]] == [ids[2], ids[1]]
    assert page["items"][0]["status"] == "COMPLETED" and page["items"][0]["total"] == 607.42
    status, rest = api(documents.handler, "GET", "/api/documents", query={"limit": "2", "cursor": page["nextCursor"]})
    assert [d["documentId"] for d in rest["items"]] == [ids[0]]


def test_cursor_cannot_reach_another_workspace(cloud, fake_engine):
    _file(cloud, fake_engine)
    _file(cloud, fake_engine)
    _, page = api(documents.handler, "GET", "/api/documents", query={"limit": "1"})
    status, body = api(documents.handler, "GET", "/api/documents", key=KEY_B, query={"cursor": page["nextCursor"]})
    assert (status, body["error"]["code"]) == (400, "invalid_cursor")
    status, _ = api(documents.handler, "GET", "/api/documents", query={"cursor": "%%%garbage"})
    assert status == 400


def test_list_filters_and_validates(cloud, fake_engine):
    _file(cloud, fake_engine)
    seed_document(cloud)
    _, body = api(documents.handler, "GET", "/api/documents", query={"status": "AWAITING_UPLOAD"})
    assert [d["status"] for d in body["items"]] == ["AWAITING_UPLOAD"]
    assert api(documents.handler, "GET", "/api/documents", query={"status": "NOPE"})[0] == 400
    assert api(documents.handler, "GET", "/api/documents", query={"limit": "x"})[0] == 400


def test_workspaces_are_isolated(cloud, fake_engine):
    document_id = _file(cloud, fake_engine, key=KEY_A)
    assert api(documents.handler, "GET", "/api/documents/{id}", key=KEY_B, doc_id=document_id)[0] == 404
    assert api(documents.handler, "DELETE", "/api/documents/{id}", key=KEY_B, doc_id=document_id)[0] == 404
    assert api(documents.handler, "GET", "/api/documents", key=KEY_B)[1]["items"] == []
    assert api(documents.handler, "GET", "/api/documents/{id}", doc_id="not-an-id")[0] == 404


def test_get_document_has_a_preview_url(cloud, fake_engine):
    document_id = _file(cloud, fake_engine)
    status, body = api(documents.handler, "GET", "/api/documents/{id}", doc_id=document_id)
    assert status == 200 and body["docType"] == "MEDICAL_BILL"
    assert "X-Amz-Signature" in body["previewUrl"] and "pk" not in body and "owner" not in body
    assert body["checks"] and body["lineItems"][0]["description"] == "Paracetamol 650 Tab"
    _, pending, _ = seed_document(cloud)
    assert api(documents.handler, "GET", "/api/documents/{id}", doc_id=pending)[1]["previewUrl"] is None


def test_corrections(cloud, fake_engine):
    document_id = _file(cloud, fake_engine)
    status, body = api(
        documents.handler,
        "PATCH",
        "/api/documents/{id}",
        doc_id=document_id,
        body={
            "vendor": "  Sanjeevani   Medicos ",
            "total": "610.5",
            "documentDate": "2026-11-09",
            "docType": "INVOICE",
        },
    )
    assert status == 200
    assert body["vendor"] == "Sanjeevani Medicos" and body["total"] == 610.5 and body["reviewed"] is True
    assert sorted(body["editedFields"]) == ["docType", "documentDate", "total", "vendor"]
    for bad in ({"total": -1}, {"documentDate": "09/11/2026"}, {"docType": "PIZZA"}, {"s3Key": "x"}, {}):
        assert api(documents.handler, "PATCH", "/api/documents/{id}", doc_id=document_id, body=bad)[0] == 400
    _, pending, _ = seed_document(cloud)
    assert api(documents.handler, "PATCH", "/api/documents/{id}", doc_id=pending, body={"vendor": "x"})[0] == 409


def test_delete_removes_record_and_file(cloud, fake_engine):
    document_id = _file(cloud, fake_engine)
    s3_key = store.get_document(OWNER_A, document_id)["s3Key"]
    assert api(documents.handler, "DELETE", "/api/documents/{id}", doc_id=document_id)[0] == 204
    assert store.get_document(OWNER_A, document_id) is None
    with pytest.raises(ClientError):
        cloud.s3.head_object(Bucket=BUCKET, Key=s3_key)


def test_reprocess_requeues_an_s3_shaped_event(cloud, fake_engine):
    document_id = _file(cloud, fake_engine)
    cloud.drain(cloud.ingest_url)
    status, body = api(documents.handler, "POST", "/api/documents/{id}/reprocess", doc_id=document_id)
    assert status == 202 and body["status"] == "QUEUED"
    (message,) = cloud.sqs.receive_message(QueueUrl=cloud.ingest_url)["Messages"]
    record = json.loads(message["Body"])["Records"][0]
    assert record["eventName"] == "ObjectCreated:Reprocess"
    assert record["s3"]["object"]["key"].startswith("uploads/")
    # Busy documents cannot be requeued twice.
    assert api(documents.handler, "POST", "/api/documents/{id}/reprocess", doc_id=document_id)[0] == 409


def test_stats(cloud, fake_engine):
    _file(cloud, fake_engine)
    _file(cloud, fake_engine)
    seed_document(cloud)
    status, body = api(documents.handler, "GET", "/api/stats")
    assert status == 200
    assert body["documents"] == 3
    assert body["byStatus"] == {"COMPLETED": 2, "AWAITING_UPLOAD": 1}
    assert body["byType"] == [{"docType": "MEDICAL_BILL", "count": 2, "total": 1214.84}]
    assert body["totalSpend"] == 1214.84 and body["currency"] == "INR"
    assert len(body["byMonth"]) == 6
    assert body["latency"]["endToEndMsP50"] is not None
    assert body["usage"] == {"uploadsToday": 0, "dailyLimit": 5}


def test_unexpected_errors_become_500(cloud, monkeypatch):
    def boom(_request):
        raise RuntimeError("database on fire")

    monkeypatch.setitem(documents.ROUTES, "GET /api/stats", boom)
    status, body = api(documents.handler, "GET", "/api/stats")
    assert status == 500 and "fire" not in json.dumps(body)


# ------------------------------------------------------------ subscriptions


def _subscriptions(cloud):
    return cloud.sns.list_subscriptions_by_topic(TopicArn=cloud.topic_arn)["Subscriptions"]


def _policy(cloud, arn):
    attributes = cloud.sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"]
    return json.loads(attributes["FilterPolicy"]), attributes.get("FilterPolicyScope")


def test_subscribe_with_an_owner_filter_policy(cloud):
    assert api(subscription.handler, "PUT", "/api/subscription", body={"email": "nope"})[0] == 400
    status, body = api(subscription.handler, "PUT", "/api/subscription", body={"email": "a@example.com"})
    assert status == 200 and body["email"] == "a@example.com" and body["status"] in {"pending", "confirmed"}
    (sub,) = _subscriptions(cloud)
    assert _policy(cloud, sub["SubscriptionArn"]) == ({"owner": [OWNER_A]}, "MessageAttributes")
    assert api(subscription.handler, "GET", "/api/subscription")[1]["email"] == "a@example.com"


def test_changing_and_removing_the_email(cloud):
    api(subscription.handler, "PUT", "/api/subscription", body={"email": "a@example.com"})
    api(subscription.handler, "PUT", "/api/subscription", body={"email": "b@example.com"})
    assert [s["Endpoint"] for s in _subscriptions(cloud)] == ["b@example.com"]
    assert api(subscription.handler, "DELETE", "/api/subscription")[0] == 204
    assert _subscriptions(cloud) == []
    assert api(subscription.handler, "GET", "/api/subscription")[1] == {"email": None, "status": "none"}


def test_email_changes_are_rate_limited(cloud):
    for i in range(5):
        assert api(subscription.handler, "PUT", "/api/subscription", body={"email": f"u{i}@example.com"})[0] == 200
    assert api(subscription.handler, "PUT", "/api/subscription", body={"email": "u9@example.com"})[0] == 429


class _SnsLikeAws:
    """moto returns the existing subscription; real SNS rejects a second one with other attributes."""

    def __init__(self, client):
        self._client = client

    def subscribe(self, **kwargs):
        for sub in self._client.list_subscriptions_by_topic(TopicArn=kwargs["TopicArn"])["Subscriptions"]:
            if sub["Endpoint"] == kwargs["Endpoint"]:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InvalidParameter",
                            "Message": "Subscription already exists with different attributes",
                        }
                    },
                    "Subscribe",
                )
        return self._client.subscribe(**kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


def test_two_workspaces_can_share_an_email(cloud, monkeypatch):
    real = boto3.client("sns", region_name="ap-south-1")
    monkeypatch.setattr(aws, "sns", lambda: _SnsLikeAws(real))
    api(subscription.handler, "PUT", "/api/subscription", key=KEY_A, body={"email": "team@example.com"})
    api(subscription.handler, "PUT", "/api/subscription", key=KEY_B, body={"email": "team@example.com"})
    (sub,) = _subscriptions(cloud)
    assert sorted(_policy(cloud, sub["SubscriptionArn"])[0]["owner"]) == sorted([OWNER_A, owner_from_key(KEY_B)])
    # Workspace A leaving keeps the shared subscription for B.
    api(subscription.handler, "DELETE", "/api/subscription", key=KEY_A)
    (sub,) = _subscriptions(cloud)
    assert _policy(cloud, sub["SubscriptionArn"])[0]["owner"] == [owner_from_key(KEY_B)]


def test_table_name_comes_from_the_environment(cloud):
    assert aws.table().name == TABLE
    assert isinstance(store.to_ddb(1.5), Decimal) and store.to_ddb({"a": None, "b": [1.25, None]}) == {
        "b": [Decimal("1.25")]
    }
    assert new_id() != new_id()
