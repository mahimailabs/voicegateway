# 0037: The distributed SFU prober runs on AWS, as a CloudFormation stack per region

- Status: accepted
- Date: 2026-10-29

## Context

The distributed SFU load test runs one coordinator and one prober per vantage region; each prober registers with the coordinator, runs its job and reports, then exits. The repository shipped `deploy/prober/fly.toml` as the deploy template. It was never deployed. On 2026-10-29 Mahimai decided there is no Fly: everything runs on AWS.

A prober needs: an image, the three LiveKit values as secrets, outbound network, a vantage label per region, and a way to start it once or on a schedule. It answers nothing, so it needs no load balancer, service or inbound port.

## Options

1. **CloudFormation template, deployed to each region with StackSets.** AWS's own IaC, nothing to install beyond the AWS CLI. StackSets deploy one template with one set of parameters to many regions, and `AWS::Region` gives each stack its vantage label. ECS Fargate RunTask runs the prober to completion; EventBridge Scheduler (`AWS::Scheduler::Schedule`, ECS target) starts it on a schedule; ECS reads the LiveKit values from a Secrets Manager secret replicated to each region.
2. **Terraform or OpenTofu with terraform-aws-modules (ecs, eventbridge).** Well maintained, but one AWS provider is pinned to one region, so N regions need N provider blocks or a wrapper. It adds a tool, a state backend and its locking to keep.
3. **AWS CDK.** Multi-region is natural, but it adds a Node or Python app, `cdk bootstrap` in every region, and synthesises CloudFormation anyway.
4. **AWS Copilot CLI.** Has scheduled jobs, but its support ended on 2026-06-12 (AWS Containers blog, 2026-03-06): no security patches.
5. **AWS Batch.** Built for queues of batch jobs; a compute environment and job queue per region is more than one task needs.
6. **A shell script calling `aws ecs run-task`.** Hand-written, no record of what exists in each region.

## Decision

Option 1. `deploy/prober/cloudformation.yaml` replaces `fly.toml`. Per region it creates a one-subnet public VPC (tasks take a public IP for egress, so no NAT gateway), an outbound-only security group, an ECS cluster, a Fargate task definition (0.25 vCPU, 512 MiB, like the Fly template's shared-cpu-1x), a log group, the execution role and a schedule that is created DISABLED. The image is pushed once to ECR; the secret holds `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` as JSON keys and is replicated, which keeps its name and suffix in every region. CI checks the template with cfn-lint.

The prober image now pins `voicegateway==0.27.0`. It installed the latest release before, and from 1.0 the `voicegateway` package on PyPI is the agent SDK, which has no `voicegw livekit sfu`.

## Consequences

- Deploying it creates AWS resources and costs money only while tasks run (Fargate time, a public IPv4 address per running task, logs), plus the secret and its replicas monthly. Nothing is deployed by this decision.
- Self-managed StackSets need the two StackSets IAM roles in the account. A single vantage can use `aws cloudformation deploy` instead.
- Every vantage starts together only if their schedules share one cron expression; the coordinator still sets ramp and duration.
- The coordinator is not part of this stack; it must be reachable from every region at `CoordinatorUrl`.
- Changing the prober image or engine version means a new image tag and a stack update.
