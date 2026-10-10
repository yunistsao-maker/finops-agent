import type { AgentCoreApplication } from '@aws/agentcore-cdk';
import { CfnOutput, Duration, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as amplify from 'aws-cdk-lib/aws-amplify';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { IdentityPool, UserPoolAuthenticationProvider } from 'aws-cdk-lib/aws-cognito-identitypool';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface FinopsUiProps {
  application: AgentCoreApplication;
  /** Amplify branch the web build is deployed to (manual zip deploys, see scripts/deploy-ui.sh). */
  branchName?: string;
  /**
   * No sign-in: every visitor gets guest credentials from the identity pool (same InvokeAgentRuntime-only policy).
   * Anyone with the URL can then read the cost data and ask questions (model tokens) - put Amplify access control
   * in front, or set this to false to require Cognito sign-in again. Default true.
   */
  guestAccess?: boolean;
}

/**
 * Web UI of the FinOps agent (web/):
 *   Cognito user pool   sign-in, no self sign-up (admins create users)
 *   identity pool       signed-in users (or guests, see guestAccess) get temporary credentials that can only
 *                       InvokeAgentRuntime on the agent
 *   Amplify app         static hosting; the browser calls AgentCore directly (its endpoint allows CORS)
 */
export class FinopsUi extends Construct {
  constructor(scope: Construct, id: string, props: FinopsUiProps) {
    super(scope, id);
    const branchName = props.branchName ?? 'main';
    const guestAccess = props.guestAccess ?? true;

    const userPool = new cognito.UserPool(this, 'Users', {
      selfSignUpEnabled: false,
      signInAliases: { username: true, email: true },
      passwordPolicy: { minLength: 12, requireSymbols: false },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: RemovalPolicy.RETAIN, // keep users if the UI is removed by mistake
    });
    const client = userPool.addClient('Web', {
      authFlows: { userSrp: true },
      generateSecret: false,
      preventUserExistenceErrors: true,
      idTokenValidity: Duration.hours(8),
      accessTokenValidity: Duration.hours(8),
    });

    const identityPool = new IdentityPool(this, 'Identities', {
      allowUnauthenticatedIdentities: guestAccess,
      // guests use the basic (classic) flow: the enhanced flow scopes unauthenticated sessions down to a short
      // list of services that does not include bedrock-agentcore
      allowClassicFlow: guestAccess,
      authenticationProviders: {
        userPools: [new UserPoolAuthenticationProvider({ userPool, userPoolClient: client })],
      },
    });

    const runtimeArns = [...props.application.environments.values()].map(env => env.runtime.runtimeArn);
    if (runtimeArns.length) {
      const invoke = new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: runtimeArns.flatMap(arn => [arn, `${arn}/runtime-endpoint/*`]),
      });
      identityPool.authenticatedRole.addToPrincipalPolicy(invoke);
      if (guestAccess) identityPool.unauthenticatedRole.addToPrincipalPolicy(invoke);
      new CfnOutput(this, 'RuntimeArn', { value: runtimeArns[0] });
    }

    const app = new amplify.CfnApp(this, 'Hosting', {
      name: `${Stack.of(this).stackName}-web`,
      platform: 'WEB',
      customRules: [
        // single-page app: everything that is not a file goes to index.html
        {
          source: '</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>',
          target: '/index.html',
          status: '200',
        },
      ],
    });
    new amplify.CfnBranch(this, 'Branch', { appId: app.attrAppId, branchName, stage: 'PRODUCTION' });

    new CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: client.userPoolClientId });
    new CfnOutput(this, 'IdentityPoolId', { value: identityPool.identityPoolId });
    new CfnOutput(this, 'GuestAccess', { value: String(guestAccess) });
    new CfnOutput(this, 'GuestRoleArn', { value: identityPool.unauthenticatedRole.roleArn });
    new CfnOutput(this, 'AmplifyAppId', { value: app.attrAppId });
    new CfnOutput(this, 'WebUrl', { value: `https://${branchName}.${app.attrDefaultDomain}` });
  }
}
