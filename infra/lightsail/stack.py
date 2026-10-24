"""Small, restricted Lightsail environment. No lookups, assets, or secrets."""
from pathlib import Path

from aws_cdk import (
    Aws, CfnOutput, CfnParameter, CfnRule, CfnRuleAssertion, Fn, RemovalPolicy, Stack,
    aws_budgets as budgets, aws_lightsail as lightsail,
)
from constructs import Construct


class LightsailStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        self.template_options.description = (
            "Restricted StockPilotAI development/demo on one Ohio Lightsail server; "
            "Compose runs the app and PostgreSQL. Stateful resources are retained."
        )
        self.template_options.metadata = {
            "AWSToolsMetrics": {"AWSAgentToolkit": "aws-cloudformation@3"},
            "com.aws.cloudformation.Context": {
                "ref": ["docs/aws-architecture.md", "infra/README.md"],
                "must": ["Ohio only", "No secrets in parameters or user data"],
            },
        }
        CfnRule(self, "SelectedRegionOnly", assertions=[CfnRuleAssertion(
            assert_=Fn.condition_equals(Aws.REGION, "us-east-2"),
            assert_description="Deploy only in the selected Region, us-east-2.",
        )])
        octet = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
        operator = CfnParameter(
            self, "OperatorIpv4Cidr", type="String",
            allowed_pattern=rf"{octet}(?:\.{octet}){{3}}/32",
            constraint_description="A single trusted IPv4 address with /32; no public ranges.",
            description="Required later: trusted operator IP for SSH and HTTPS access.",
        )
        key = CfnParameter(
            self, "SshKeyPairName", type="String", min_length=1,
            allowed_pattern=r"[A-Za-z0-9_.-]+",
            description="Existing Lightsail SSH key pair in Ohio; no private key in the stack.",
        )
        blueprint = CfnParameter(
            self, "UbuntuBlueprintId", type="String", default="ubuntu_24_04",
            allowed_values=["ubuntu_24_04"],
            description="Verify blueprint availability in Ohio before deployment.",
        )
        budget_amount = CfnParameter(
            self, "MonthlyBudgetUsd", type="Number", min_value=1,
            description="Required later: project-wide monthly gross AWS cost alert threshold.",
        )
        budget_email = CfnParameter(
            self, "BudgetEmail", type="String",
            allowed_pattern=r"[^\s@]+@[^\s@]+\.[^\s@]+",
            description="Required later: email to receive budget alerts (not a spending cap).",
        )

        def port(number: int, cidrs: list[str]):
            return lightsail.CfnInstance.PortProperty(
                from_port=number, to_port=number, protocol="tcp",
                access_direction="inbound", cidrs=cidrs, ipv6_cidrs=[],
            )

        instance = lightsail.CfnInstance(
            self, "Server",
            instance_name=Fn.sub("${AWS::StackName}-server"),
            blueprint_id=blueprint.value_as_string, bundle_id="small_3_0",
            key_pair_name=key.value_as_string,
            networking=lightsail.CfnInstance.NetworkingProperty(ports=[
                port(22, [operator.value_as_string]),
                # Caddy serves ACME HTTP-01 and redirects here, never the application.
                port(80, ["0.0.0.0/0"]),
                port(443, [operator.value_as_string]),
            ]),
            add_ons=[lightsail.CfnInstance.AddOnProperty(
                add_on_type="AutoSnapshot", status="Enabled",
                auto_snapshot_add_on_request=lightsail.CfnInstance.AutoSnapshotAddOnProperty(
                    snapshot_time_of_day="06:00",
                ),
            )],
            user_data=(Path(__file__).parents[2] / "deploy/lightsail/bootstrap.sh").read_text(),
        )
        instance.apply_removal_policy(RemovalPolicy.RETAIN)
        instance.cfn_options.metadata = {"com.aws.cloudformation.Context": {
            "why": "Low-traffic demo; app and DB share host to avoid ALB/RDS baseline cost",
            "must": ["SSH/HTTPS restricted to operator /32", "80 for ACME/redirect only",
                     "No public database port or IPv6 ingress", "Back up before replacement"],
            "mutable": "review-required",
        }}
        address = lightsail.CfnStaticIp(
            self, "Address", static_ip_name=Fn.sub("${AWS::StackName}-ip"),
            attached_to=instance.ref,
        )
        address.apply_removal_policy(RemovalPolicy.RETAIN)
        address.cfn_options.metadata = {"com.aws.cloudformation.Context": {
            "why": "Stable DNS target; one address included with attached IPv4 bundle",
            "must": ["Retained address can keep billing; explicitly review cleanup"],
        }}
        budget = budgets.CfnBudget(
            self, "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=budget_amount.value_as_number, unit="USD"),
                cost_types=budgets.CfnBudget.CostTypesProperty(
                    include_credit=False, include_refund=False,
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN", notification_type="ACTUAL",
                        threshold=threshold, threshold_type="PERCENTAGE",
                    ),
                    subscribers=[budgets.CfnBudget.SubscriberProperty(
                        address=budget_email.value_as_string, subscription_type="EMAIL")],
                ) for threshold in [80, 100]
            ],
        )
        budget.cfn_options.metadata = {"com.aws.cloudformation.Context": {
            "why": "Gross project spending alerts remain visible while credits offset charges",
            "must": ["Alerts only; not a hard cap", "Scope is all project AWS costs"],
        }}
        CfnOutput(self, "StaticIpv4", value=address.attr_ip_address)
        CfnOutput(self, "InstanceName", value=instance.ref)
