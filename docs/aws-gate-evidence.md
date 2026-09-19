# AWS Gate Evidence

Ticket #30 is a claim; Ticket #43 is supposed to be the proof, deployed to
staging with a linked artifact per box. **This container has no AWS
account, no credentials, and no network route to AWS endpoints** (see the
egress allowlist in this environment). So the honest status below is
"code-complete and locally validated," not "live-verified" — consistent
with this project's own rule to mark an unverifiable claim as unverified
rather than quietly assume it.

| Gate box | Status | Evidence |
|---|---|---|
| Every AWS service ties to a specific Bible requirement | ✅ Met | See build-map.md §F (AWS service justification table) |
| At least one AWS service sits inside the agent's runtime loop | ✅ Met by design | Bedrock (inference), DynamoDB (state), EventBridge (trigger), Lambda (tool execution) — none is hosting |
| BUILD IT runs fully offline with no AWS account | ✅ Verified | 85 tests pass with zero AWS imports touched; `app/settings.py` boots on an empty `.env` (see `test_settings` warnings test) |
| Credentials never hard-coded | ✅ Verified locally | `app.providers.secrets.scan_for_inline_credentials` run against the full repo returns zero hits; CDK-synthesized templates contain no literal secret values (`SecretsStack` creates placeholder shells only) |
| Every AWS-touching ticket has an acceptance test | ⚠️ Written, not run live | `tests/test_parity.py` exists with real assertions but is `skipif` unless `RUN_AWS_INTEGRATION_TESTS=1` in an environment with real credentials |
| CDK infra deploys and destroys cleanly | ⚠️ Synthesizes, not deployed | `cdk synth` succeeds against all five stacks with zero errors (one expected warning: `ObservabilityStack` is an intentional placeholder — see its docstring). `cdk deploy`/`cdk destroy` were not run; no AWS account is available here. |

## What deploying this would additionally prove

- `EventBridgeScheduler`'s daily rule firing `scheduled_trigger_handler` **with the FastAPI process stopped** — this is the actual differentiator over `LocalScheduler`, and it can only be demonstrated live.
- `DynamoDBStateStore` write-then-read round trip against a real table.
- `BedrockModelProvider` returning a schema-conformant response from a real model.
- The negative IAM test in Ticket #34: the payments Lambda's role cannot read the voice secret.

## Known packaging gap (not yet solved)

`ToolsStack`'s Lambdas point `Code.from_asset("../../lambdas")`, which
bundles only the `lambdas/` directory. The handlers import from `app.*`
(e.g. `app.database`, `app.providers.voice`), so a real deployment needs
either a Lambda layer containing the `app` package and its dependencies,
or a bundled build step (Docker-based CDK bundling). This is a real,
outstanding task for whoever deploys this — flagged here rather than
silently glossed over.

## Next step

Run this in an environment with a real (ideally sandbox/free-tier) AWS
account: `cdk bootstrap`, then `cdk deploy --all`, then set
`RUN_AWS_INTEGRATION_TESTS=1` and re-run `pytest tests/test_parity.py`.
Fill in the "Evidence" column above with real log lines / table items /
rule-invocation records once that's done. If any box fails, the build
map's instruction stands: revise the architecture, don't proceed to a
judge demo on an unhonest check.
