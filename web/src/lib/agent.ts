import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from '@aws-sdk/client-bedrock-agentcore';
import { CognitoIdentityClient, GetIdCommand, GetOpenIdTokenCommand } from '@aws-sdk/client-cognito-identity';
import { AssumeRoleWithWebIdentityCommand, STSClient } from '@aws-sdk/client-sts';
import { fetchAuthSession } from 'aws-amplify/auth';
import { config } from '../config';
import type { AgentEvent, Report, SystemStatus } from './types';

interface AwsCredentialIdentity {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
  expiration?: Date;
}

let client: BedrockAgentCoreClient | undefined;
let guest: AwsCredentialIdentity | undefined;

/**
 * Guest credentials via the identity pool's basic (classic) flow: GetId -> GetOpenIdToken -> AssumeRoleWithWebIdentity.
 * The enhanced flow (what Amplify uses) scopes guest sessions down to services that exclude bedrock-agentcore.
 */
async function guestCredentials(): Promise<AwsCredentialIdentity> {
  if (guest?.expiration && guest.expiration.getTime() - Date.now() > 5 * 60_000) return guest;
  const ci = new CognitoIdentityClient({ region: config.region });
  let identityId = localStorage.getItem('finops-identity-id') ?? undefined;
  if (!identityId) {
    identityId = (await ci.send(new GetIdCommand({ IdentityPoolId: config.identityPoolId }))).IdentityId!;
    localStorage.setItem('finops-identity-id', identityId);
  }
  const { Token } = await ci.send(new GetOpenIdTokenCommand({ IdentityId: identityId }));
  const sts = new STSClient({ region: config.region });
  const { Credentials: c } = await sts.send(new AssumeRoleWithWebIdentityCommand({
    RoleArn: config.guestRoleArn, RoleSessionName: 'finops-web-guest', WebIdentityToken: Token,
  }));
  guest = { accessKeyId: c!.AccessKeyId!, secretAccessKey: c!.SecretAccessKey!, sessionToken: c!.SessionToken, expiration: c!.Expiration };
  return guest;
}

function agentClient(): BedrockAgentCoreClient {
  // identity-pool credentials (guest or signed-in user); the role may only InvokeAgentRuntime on this runtime
  client ??= new BedrockAgentCoreClient({
    region: config.region,
    credentials: async () => {
      if (config.guestAccess) return guestCredentials();
      const { credentials } = await fetchAuthSession();
      if (!credentials) throw new Error('not signed in');
      return credentials;
    },
  });
  return client;
}

/** Runtime session ids must be at least 33 characters. */
export const newSessionId = () => `finops-web-${crypto.randomUUID()}`;

/** Invoke the agent and hand every streamed event (SSE "data:" line, or the whole JSON body) to onEvent. */
export async function invokeAgent(
  payload: Record<string, unknown>,
  sessionId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (config.localAgent) return invokeLocal(payload, sessionId, onEvent, signal);
  const out = await agentClient().send(
    new InvokeAgentRuntimeCommand({
      agentRuntimeArn: config.runtimeArn,
      runtimeSessionId: sessionId,
      contentType: 'application/json',
      payload: new TextEncoder().encode(JSON.stringify(payload)),
    }),
    { abortSignal: signal },
  );
  if (!out.response) return;
  await readEvents(out.response.transformToWebStream(), out.contentType ?? '', onEvent);
}

/**
 * Local development (VITE_LOCAL_AGENT=true): the agent runs on this machine (`python main.py`, port 8080) and
 * Vite proxies /invocations to it - same payloads and event stream as the deployed runtime, no AWS sign-in.
 */
async function invokeLocal(
  payload: Record<string, unknown>,
  sessionId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/invocations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) throw new Error(`local agent: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
  if (!res.body) return;
  await readEvents(res.body, res.headers.get('content-type') ?? '', onEvent);
}

/** Every streamed event (SSE "data:" line, or the whole JSON body) to onEvent. */
async function readEvents(
  stream: ReadableStream<Uint8Array>,
  contentType: string,
  onEvent: (event: AgentEvent) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const sse = contentType.includes('text/event-stream');
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: !done });
    if (sse) {
      const lines = buf.split('\n');
      buf = done ? '' : (lines.pop() ?? '');
      for (const line of lines) if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)));
    }
    if (done) break;
  }
  if (!sse && buf.trim()) onEvent(JSON.parse(buf));
}

/** Deterministic overview (no model call): what the dashboard is drawn from. */
export async function fetchReport(days: number, sessionId: string): Promise<Report> {
  let result: unknown;
  await invokeAgent({ action: 'report', days }, sessionId, e => (result = e));
  if (!result || typeof result !== 'object') throw new Error('empty report');
  if ('error' in result) throw new Error(String((result as { error: unknown }).error));
  return result as Report;
}

/** Configured clouds, data freshness, tools and model (no model call): the "How it works" page. */
export async function fetchStatus(sessionId: string): Promise<SystemStatus> {
  let result: unknown;
  await invokeAgent({ action: 'status' }, sessionId, e => (result = e));
  if (!result || typeof result !== 'object') throw new Error('empty status');
  if ('error' in result) throw new Error(String((result as { error: unknown }).error));
  return result as SystemStatus;
}
