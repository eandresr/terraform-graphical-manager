"""
Tests for app/state_parser.py — parse_state() and helpers.
"""
from app.state_parser import parse_state


def _make_resource(type_, name, instances=None, module=None, mode="managed"):
    r = {
        "type": type_,
        "name": name,
        "mode": mode,
        "provider": "registry.terraform.io/hashicorp/aws",
        "instances": instances or [],
    }
    if module:
        r["module"] = module
    return r


class TestParseStateEmpty:
    def test_none_returns_empty(self):
        result = parse_state(None)
        assert result["resource_count"] == 0
        assert result["resources"] == []

    def test_empty_dict_returns_empty(self):
        result = parse_state({})
        assert result["resource_count"] == 0


class TestParseStateBasic:
    def test_resource_count(self):
        state = {
            "version": 4,
            "serial": 1,
            "terraform_version": "1.6.0",
            "resources": [
                _make_resource("aws_instance", "web"),
                _make_resource("aws_s3_bucket", "data"),
            ],
        }
        result = parse_state(state)
        assert result["resource_count"] == 2

    def test_metadata_propagated(self):
        state = {
            "version": 4,
            "serial": 7,
            "terraform_version": "1.5.0",
            "lineage": "abc-123",
            "resources": [],
        }
        result = parse_state(state)
        assert result["version"] == 4
        assert result["serial"] == 7
        assert result["terraform_version"] == "1.5.0"
        assert result["lineage"] == "abc-123"

    def test_resource_address_managed(self):
        state = {
            "resources": [_make_resource("aws_instance", "web")]
        }
        result = parse_state(state)
        assert result["resources"][0]["address"] == "aws_instance.web"

    def test_resource_address_data(self):
        state = {
            "resources": [_make_resource("aws_ami", "ubuntu", mode="data")]
        }
        result = parse_state(state)
        assert result["resources"][0]["address"] == "data.aws_ami.ubuntu"

    def test_resource_address_with_module(self):
        state = {
            "resources": [_make_resource("aws_instance", "web", module="module.vpc")]
        }
        result = parse_state(state)
        assert result["resources"][0]["address"] == "module.vpc.aws_instance.web"


class TestParseStateModules:
    def test_root_module_key(self):
        state = {
            "resources": [_make_resource("aws_instance", "web")]
        }
        result = parse_state(state)
        assert "root" in result["modules"]

    def test_named_module_key(self):
        state = {
            "resources": [_make_resource("aws_instance", "web", module="module.app")]
        }
        result = parse_state(state)
        assert "module.app" in result["modules"]

    def test_module_names_sorted(self):
        state = {
            "resources": [
                _make_resource("aws_instance", "a", module="module.z"),
                _make_resource("aws_instance", "b", module="module.a"),
            ]
        }
        result = parse_state(state)
        assert result["module_names"] == sorted(result["module_names"])


class TestParseStateSensitiveAttributes:
    def test_password_is_masked(self):
        inst = {"attributes": {"password": "supersecret", "name": "admin"}}
        state = {
            "resources": [_make_resource("aws_db_instance", "db", instances=[inst])]
        }
        result = parse_state(state)
        attrs = result["resources"][0]["instances"][0]["attributes"]
        assert attrs["password"] == "****** (sensitive)"
        assert attrs["name"] == "admin"

    def test_token_is_masked(self):
        inst = {"attributes": {"api_token": "tok-abc"}}
        state = {
            "resources": [_make_resource("github_token", "t", instances=[inst])]
        }
        result = parse_state(state)
        attrs = result["resources"][0]["instances"][0]["attributes"]
        assert attrs["api_token"] == "****** (sensitive)"

    def test_non_sensitive_key_preserved(self):
        inst = {"attributes": {"region": "us-east-1"}}
        state = {
            "resources": [_make_resource("aws_instance", "web", instances=[inst])]
        }
        result = parse_state(state)
        attrs = result["resources"][0]["instances"][0]["attributes"]
        assert attrs["region"] == "us-east-1"
