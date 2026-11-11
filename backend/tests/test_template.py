"""template.yaml and the code must agree: routes, handlers, retry counts, key prefix."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from parchi import storage

TEMPLATE = Path(__file__).resolve().parents[2] / "template.yaml"


class CfnLoader(yaml.SafeLoader):
    """Reads CloudFormation YAML, turning !Ref / !Sub / !GetAtt ... into plain {Tag: value} dicts."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {suffix: value}


CfnLoader.add_multi_constructor("!", _tag)


@pytest.fixture(scope="module")
def template():
    return yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)


def _functions(template):
    return {
        name: resource["Properties"]
        for name, resource in template["Resources"].items()
        if resource["Type"] == "AWS::Serverless::Function"
    }


def test_every_route_in_the_template_is_handled_in_code(template):
    for name, properties in _functions(template).items():
        module_name, _, attribute = properties["Handler"].rpartition(".")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attribute)), name
        routes = {
            f"{event['Properties']['Method']} {event['Properties']['Path']}"
            for event in properties.get("Events", {}).values()
            if event["Type"] == "HttpApi"
        }
        if routes:
            assert routes == set(module.ROUTES), f"{name}: template and ROUTES disagree"


def test_retry_count_and_prefix_match(template):
    queue = template["Resources"]["IngestQueue"]["Properties"]
    processor = _functions(template)["ProcessorFunction"]
    assert str(queue["RedrivePolicy"]["maxReceiveCount"]) == processor["Environment"]["Variables"]["MAX_RECEIVE_COUNT"]
    # Visibility timeout must exceed the function timeout, and the lease must outlast a run.
    assert queue["VisibilityTimeout"] >= 6 * processor["Timeout"]
    assert int(processor["Environment"]["Variables"]["PROCESSING_LEASE_SECONDS"]) > processor["Timeout"]
    rules = template["Resources"]["UploadBucket"]["Properties"]["NotificationConfiguration"]["QueueConfigurations"]
    assert rules[0]["Filter"]["S3Key"]["Rules"][0]["Value"] == storage.PREFIX


def test_environment_variables_are_ones_the_code_reads(template):
    names = set(template["Globals"]["Function"]["Environment"]["Variables"])
    names |= set(_functions(template)["ProcessorFunction"]["Environment"]["Variables"])
    source = "".join(p.read_text() for p in Path(storage.__file__).parent.rglob("*.py"))
    assert all(name in source for name in names), names
