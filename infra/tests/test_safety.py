import importlib.util
import re
from pathlib import Path
import sys

import aws_cdk as cdk
from aws_cdk.assertions import Template
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
from lightsail.stack import LightsailStack

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    stack = LightsailStack(app, "SafetyTest", env=cdk.Environment(region="us-east-2"))
    return Template.from_stack(stack).to_json()


def test_firewall_does_not_publish_app_database_or_ipv6(template):
    server = next(r for r in template["Resources"].values() if r["Type"] == "AWS::Lightsail::Instance")
    ports = server["Properties"]["Networking"]["Ports"]
    assert {p["FromPort"] for p in ports} == {22, 80, 443}
    for port in ports:
        assert port["Ipv6Cidrs"] == []
        assert port["ToPort"] == port["FromPort"]
        if port["FromPort"] != 80:
            assert port["Cidrs"] == [{"Ref": "OperatorIpv4Cidr"}]
    pattern = template["Parameters"]["OperatorIpv4Cidr"]["AllowedPattern"]
    assert re.fullmatch(pattern, "203.0.113.8/32")
    for unsafe in ["0.0.0.0/0", "203.0.113.8/24", "999.0.0.1/32", "::/0"]:
        assert not re.fullmatch(pattern, unsafe)
    assert "Default" not in template["Parameters"]["OperatorIpv4Cidr"]


def test_state_retention_snapshots_and_deploy_region(template):
    for resource in template["Resources"].values():
        if resource["Type"] in {"AWS::Lightsail::Instance", "AWS::Lightsail::StaticIp"}:
            assert resource["DeletionPolicy"] == "Retain"
            assert resource["UpdateReplacePolicy"] == "Retain"
        if resource["Type"] == "AWS::Lightsail::Instance":
            assert resource["Properties"]["AddOns"][0]["Status"] == "Enabled"
            assert "OPENAI_API_KEY" not in resource["Properties"]["UserData"]
    assert template["Rules"]["SelectedRegionOnly"]["Assertions"][0]["Assert"] == {
        "Fn::Equals": [{"Ref": "AWS::Region"}, "us-east-2"]
    }


def test_alerts_count_costs_before_credits(template):
    budget = next(r for r in template["Resources"].values() if r["Type"] == "AWS::Budgets::Budget")
    assert budget["Properties"]["Budget"]["CostTypes"]["IncludeCredit"] is False
    assert "Default" not in template["Parameters"]["MonthlyBudgetUsd"]


def test_only_caddy_publishes_ports_and_migration_is_explicit():
    services = yaml.safe_load((ROOT / "deploy/lightsail/compose.yaml").read_text())["services"]
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name in ["web", "db", "migrate"]:
        assert "ports" not in services[name]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["web"]["environment"]["APP_ENV"] == "production"


spec = importlib.util.spec_from_file_location("validate_env", ROOT / "deploy/lightsail/validate_env.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def write_env(tmp_path, extra=""):
    path = tmp_path / "runtime.env"
    path.write_text("SITE_DOMAIN=demo.example.com\nPOSTGRES_PASSWORD=" + "a" * 64
                    + "\nOPENAI_API_KEY=sk-test-only\n" + extra)
    path.chmod(0o600)
    return path


def test_valid_runtime_and_reject_unsafe_permissions(tmp_path):
    path = write_env(tmp_path)
    assert validator.validate(path)["SITE_DOMAIN"] == "demo.example.com"
    path.chmod(0o644)
    with pytest.raises(ValueError):
        validator.validate(path)


@pytest.mark.parametrize("extra", [
    "OPENAI_API_KEY=sk-duplicate\n", "APP_IMAGE=evil\n",
    "OTHER=$(echo-danger)\n", "OPENAI_DEFAULT_MODEL='quoted'\n",
])
def test_reject_ambiguous_or_unexpected_runtime_values(tmp_path, extra):
    with pytest.raises(ValueError):
        validator.validate(write_env(tmp_path, extra))
