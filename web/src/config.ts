/** Deployment ids, injected at build time from the stack outputs (see scripts/deploy-ui.sh). */
export const config = {
  region: import.meta.env.VITE_REGION ?? 'us-east-1',
  userPoolId: import.meta.env.VITE_USER_POOL_ID ?? '',
  userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID ?? '',
  identityPoolId: import.meta.env.VITE_IDENTITY_POOL_ID ?? '',
  runtimeArn: import.meta.env.VITE_RUNTIME_ARN ?? '',
  /** no sign-in: guest credentials from the identity pool (classic flow, assuming this role) */
  guestAccess: import.meta.env.VITE_GUEST_ACCESS === 'true',
  guestRoleArn: import.meta.env.VITE_GUEST_ROLE_ARN ?? '',
  /** dev only: talk to `python main.py` on this machine through the Vite proxy instead of AgentCore Runtime */
  localAgent: import.meta.env.DEV && import.meta.env.VITE_LOCAL_AGENT === 'true',
};
