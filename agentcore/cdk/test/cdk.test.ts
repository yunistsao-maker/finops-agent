import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/cdk-stack';

test('AgentCoreStack synthesizes with empty spec', () => {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec: {
      name: 'testproject',
      version: 1,
      managedBy: 'CDK' as const,
      runtimes: [],
      memories: [],
      credentials: [],
      evaluators: [],
      onlineEvalConfigs: [],
      configBundles: [],
      policyEngines: [],
      payments: [],
      agentCoreGateways: [],
      mcpRuntimeTools: [],
      unassignedTargets: [],
      datasets: [],
      knowledgeBases: [],
    },
  });
  const template = Template.fromStack(stack);
  template.hasOutput('StackNameOutput', {
    Description: 'Name of the CloudFormation Stack',
  });
});

const emptySpec = () => ({
  name: 'testproject', version: 1, managedBy: 'CDK' as const, runtimes: [], memories: [], credentials: [],
  evaluators: [], onlineEvalConfigs: [], configBundles: [], policyEngines: [], payments: [], agentCoreGateways: [],
  mcpRuntimeTools: [], unassignedTargets: [], datasets: [], knowledgeBases: [],
});

test('FOCUS ingestion is opt-in', () => {
  const plain = Template.fromStack(new AgentCoreStack(new cdk.App(), 'Plain', { spec: emptySpec() }));
  expect(Object.keys(plain.findResources('AWS::Lambda::Function')).some(k => k.includes('FocusIngest'))).toBe(false);

  process.env.FINOPS_FOCUS_PROVIDERS = 'azure,gcp';
  try {
    const multi = Template.fromStack(new AgentCoreStack(new cdk.App(), 'Multi', { spec: emptySpec() }));
    multi.hasResourceProperties('AWS::Lambda::Function', { Handler: 'finops.focus_handler.handler' });
    expect(Object.keys(multi.findOutputs('*')).some(k => k.includes('FocusLandingUri'))).toBe(true);
    // one notification per run marker, on the landing prefix only
    const notif = JSON.stringify(multi.findResources('Custom::S3BucketNotifications'));
    expect(notif).toContain('manifest.json');
    expect(notif).toContain('_SUCCESS');
    const policies = JSON.stringify(multi.findResources('AWS::IAM::Policy'));
    expect(policies).toContain('finops/v1/azure/*');
    expect(policies).toContain('finops/v1/gcp/*');
    process.env.FINOPS_FOCUS_PROVIDERS = 'azure,gpc';
    expect(() => new AgentCoreStack(new cdk.App(), 'Typo', { spec: emptySpec() })).toThrow(/gpc/);
  } finally {
    delete process.env.FINOPS_FOCUS_PROVIDERS;
  }
});
