import { useState, type FormEvent } from 'react';
import { confirmSignIn, signIn } from 'aws-amplify/auth';

/** Username + password sign-in; handles the first-login new-password challenge of admin-created users. */
export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [needsNew, setNeedsNew] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = needsNew
        ? await confirmSignIn({ challengeResponse: newPassword })
        : await signIn({ username: username.trim(), password });
      if (out.isSignedIn) onSignedIn();
      else if (out.nextStep.signInStep === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') setNeedsNew(true);
      else setError(`Unsupported sign-in step: ${out.nextStep.signInStep}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <h1><img src="/favicon.svg" alt="" width={28} height={28} /> FinOps</h1>
        <p className="muted">AWS cost, LLM spend and projections — ask the agent anything.</p>
        {!needsNew ? (
          <>
            <label>Username or email<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" required /></label>
            <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></label>
          </>
        ) : (
          <label>Choose a new password (12+ characters)
            <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} autoComplete="new-password" minLength={12} required />
          </label>
        )}
        {error && <div className="banner error">{error}</div>}
        <button className="btn primary" disabled={busy}>{busy ? 'Signing in…' : needsNew ? 'Set password' : 'Sign in'}</button>
      </form>
    </div>
  );
}
