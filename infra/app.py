"""Synthesize locally; deployment is a separate, explicitly reviewed action."""
import os

import aws_cdk as cdk

from lightsail.stack import LightsailStack

region = os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
if region != "us-west-2":
    raise ValueError("StockPilotAI deployment target is Oregon: us-west-2")

app = cdk.App()
LightsailStack(
    app,
    "StockPilotLightsail",
    env=cdk.Environment(region=region),
    termination_protection=True,
)
app.synth()
