"""
Tests for app/plan_parser.py — parse_plan() and helpers.
"""
import pytest
from app.plan_parser import parse_plan


def _make_rc(address, actions, before=None, after=None, resource_type="aws_instance", name="web"):
    return {
        "address": address,
        "type": resource_type,
        "name": name,
        "provider_name": "registry.terraform.io/hashicorp/aws",
        "change": {
            "actions": actions,
            "before": before or {},
            "after": after or {},
        },
    }


class TestParsePlanEmpty:
    def test_none_input_returns_empty(self):
        result = parse_plan(None)
        assert result["total_changes"] == 0
        assert result["changes"] == []

    def test_empty_dict_returns_empty(self):
        result = parse_plan({})
        assert result["total_changes"] == 0

    def test_empty_resource_changes(self):
        result = parse_plan({"resource_changes": []})
        assert result["total_changes"] == 0
        assert result["counts"] == {"create": 0, "update": 0, "delete": 0, "replace": 0, "no-op": 0}


class TestParsePlanCounts:
    def test_single_create(self):
        plan = {"resource_changes": [_make_rc("aws_instance.web", ["create"])]}
        result = parse_plan(plan)
        assert result["counts"]["create"] == 1
        assert result["total_changes"] == 1

    def test_single_delete(self):
        plan = {"resource_changes": [_make_rc("aws_instance.web", ["delete"])]}
        result = parse_plan(plan)
        assert result["counts"]["delete"] == 1
        assert result["total_changes"] == 1

    def test_single_update(self):
        plan = {"resource_changes": [_make_rc("aws_instance.web", ["update"])]}
        result = parse_plan(plan)
        assert result["counts"]["update"] == 1

    def test_replace_create_delete(self):
        plan = {"resource_changes": [_make_rc("aws_instance.web", ["create", "delete"])]}
        result = parse_plan(plan)
        assert result["counts"]["replace"] == 1
        assert result["total_changes"] == 1

    def test_no_op_not_counted_in_total(self):
        plan = {"resource_changes": [_make_rc("aws_instance.web", ["no-op"])]}
        result = parse_plan(plan)
        assert result["total_changes"] == 0

    def test_mixed_actions(self):
        plan = {
            "resource_changes": [
                _make_rc("aws_instance.a", ["create"]),
                _make_rc("aws_instance.b", ["delete"]),
                _make_rc("aws_instance.c", ["update"]),
                _make_rc("aws_instance.d", ["no-op"]),
            ]
        }
        result = parse_plan(plan)
        assert result["counts"]["create"] == 1
        assert result["counts"]["delete"] == 1
        assert result["counts"]["update"] == 1
        assert result["counts"]["no-op"] == 1
        assert result["total_changes"] == 3


class TestParsePlanSorting:
    def test_deletes_come_before_creates(self):
        plan = {
            "resource_changes": [
                _make_rc("aws_instance.a", ["create"]),
                _make_rc("aws_instance.b", ["delete"]),
            ]
        }
        result = parse_plan(plan)
        actions = [c["action"] for c in result["changes"]]
        assert actions.index("delete") < actions.index("create")


class TestParsePlanDiff:
    def test_create_diff_has_add_lines(self):
        plan = {
            "resource_changes": [
                _make_rc(
                    "aws_instance.web",
                    ["create"],
                    before={},
                    after={"ami": "ami-123", "instance_type": "t3.micro"},
                )
            ]
        }
        result = parse_plan(plan)
        diff = result["changes"][0]["diff_lines"]
        keys = {line["key"] for line in diff}
        assert "ami" in keys
        assert "instance_type" in keys
        for line in diff:
            assert line["type"] == "add"

    def test_delete_diff_has_remove_lines(self):
        plan = {
            "resource_changes": [
                _make_rc(
                    "aws_instance.web",
                    ["delete"],
                    before={"ami": "ami-123"},
                    after={},
                )
            ]
        }
        result = parse_plan(plan)
        diff = result["changes"][0]["diff_lines"]
        assert diff[0]["type"] == "remove"

    def test_update_diff_shows_changed_keys(self):
        plan = {
            "resource_changes": [
                _make_rc(
                    "aws_instance.web",
                    ["update"],
                    before={"instance_type": "t3.micro"},
                    after={"instance_type": "t3.large"},
                )
            ]
        }
        result = parse_plan(plan)
        diff = result["changes"][0]["diff_lines"]
        assert diff[0]["key"] == "instance_type"
        assert diff[0]["type"] == "change"
        assert diff[0]["before"] == "t3.micro"
        assert diff[0]["after"] == "t3.large"


class TestParsePlanMetadata:
    def test_terraform_version_propagated(self):
        plan = {"terraform_version": "1.6.0", "resource_changes": []}
        result = parse_plan(plan)
        assert result["terraform_version"] == "1.6.0"

    def test_format_version_propagated(self):
        plan = {"format_version": "1.1", "resource_changes": []}
        result = parse_plan(plan)
        assert result["format_version"] == "1.1"
