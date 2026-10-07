import type { AgentCoreApplication } from '@aws/agentcore-cdk';
import { CfnOutput, Duration, IgnoreMode, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export interface FinopsDataProps {
  application: AgentCoreApplication;
  /** Directory holding the `finops` package (shared by the collector Lambda and the agent). */
  codeDir: string;
  /** Cost allocation tag keys tried in order for project grouping. */
  projectTagKeys?: string;
  /** Optional Cost Category (defined in the payer account) that takes precedence over tags. */
  costCategory?: string;
  /** Daily schedule, UTC. Default 01:00 UTC = 09:00 Asia/Taipei. */
  scheduleHourUtc?: number;
}

/**
 * Data plane of the FinOps agent:
 *   S3 bucket          finops/v1/{status.json, months/YYYY-MM.json}
 *   collector Lambda   Cost Explorer + CloudWatch AgentCore usage + tags -> bucket (2-3 CE requests per run)
 *   daily rule         re-reads the last 7 days (Cost Explorer revises recent days)
 * and the agent runtime's read-only access to it.
 */
export class FinopsData extends Construct {
  readonly bucket: s3.Bucket;
  readonly collector: lambda.Function;

  constructor(scope: Construct, id: string, props: FinopsDataProps) {
    super(scope, id);
    const prefix = 'finops/v1/';
    const tagKeys = props.projectTagKeys ?? 'project,Project,agentcore:project-name,app';

    this.bucket = new s3.Bucket(this, 'Data', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: false,
      removalPolicy: RemovalPolicy.RETAIN, // cost history outlives the stack
    });
    const dataUri = `s3://${this.bucket.bucketName}/${prefix}`;

    const logGroup = new logs.LogGroup(this, 'CollectorLogs', {
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.collector = new lambda.Function(this, 'Collector', {
      description: 'Daily FinOps collection: Cost Explorer + AgentCore usage -> S3',
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'finops.lambda_handler.handler',
      // only the pure-Python finops package (boto3 comes with the Lambda runtime)
      code: lambda.Code.fromAsset(props.codeDir, {
        ignoreMode: IgnoreMode.GIT,
        exclude: ['*', '!finops/', '!finops/*.py', 'finops/tools.py'],
      }),
      timeout: Duration.minutes(5),
      memorySize: 512,
      logGroup,
      environment: {
        DATA_URI: dataUri,
        PROJECT_TAG_KEYS: tagKeys,
        COST_CATEGORY: props.costCategory ?? '',
        LOOKBACK_DAYS: '7',
        CE_REGION: 'us-east-1',
      },
    });
    this.bucket.grantReadWrite(this.collector, `${prefix}*`);
    this.collector.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ce:GetCostAndUsage', 'ce:GetTags', 'cloudwatch:ListMetrics', 'cloudwatch:GetMetricData', 'tag:GetResources'],
        resources: ['*'], // these read APIs do not support resource-level permissions
      })
    );

    new events.Rule(this, 'Daily', {
      description: 'Collect AWS cost data for the FinOps agent',
      schedule: events.Schedule.cron({ minute: '0', hour: String(props.scheduleHourUtc ?? 1) }),
      targets: [new targets.LambdaFunction(this.collector, { retryAttempts: 2, maxEventAge: Duration.hours(6) })],
    });

    for (const env of props.application.environments.values()) {
      env.runtime.addEnvironmentVariable('DATA_URI', dataUri);
      env.runtime.addEnvironmentVariable('PROJECT_TAG_KEYS', tagKeys);
      env.runtime.addToPolicy(
        new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [this.bucket.arnForObjects(`${prefix}*`)] })
      );
      env.runtime.addToPolicy(
        new iam.PolicyStatement({
          actions: ['s3:ListBucket'],
          resources: [this.bucket.bucketArn],
          conditions: { StringLike: { 's3:prefix': [`${prefix}*`] } },
        })
      );
      env.runtime.addToPolicy(
        new iam.PolicyStatement({ actions: ['ce:GetCostForecast', 'tag:GetResources'], resources: ['*'] })
      );
      // MODEL_ID is a cross-region inference profile (global.*); the L3 grants foundation-model/* only
      env.runtime.addToPolicy(
        new iam.PolicyStatement({
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          resources: [`arn:aws:bedrock:*:${Stack.of(this).account}:inference-profile/*`],
        })
      );
    }

    new CfnOutput(this, 'DataBucketName', { value: this.bucket.bucketName });
    new CfnOutput(this, 'DataUri', { value: dataUri });
    new CfnOutput(this, 'CollectorFunctionName', { value: this.collector.functionName });
  }
}
