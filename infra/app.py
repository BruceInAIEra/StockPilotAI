"""Synthesize locally; deployment is a separate, explicitly reviewed action."""
import os

import aws_cdk as cdk

from lightsail.stack import LightsailStack

region = os.environ.get("CDK_DEFAULT_REGION", "us-east-2")
if region != "us-east-2":
    raise ValueError("StockPilotAI resources must stay in the selected Region: us-east-2")

app = cdk.App()
LightsailStack(
    app,
    "StockPilotLightsail",
    env=cdk.Environment(region=region),
    termination_protection=True,
)
app.synth()
