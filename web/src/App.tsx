import { getCurrentUser, signOut } from 'aws-amplify/auth';
import { useEffect, useState } from 'react';
import { AskPanel } from './components/AskPanel';
import { Dashboard } from './components/Dashboard';
import { ErrorBoundary } from './components/ErrorBoundary';
import { HowItWorks } from './components/HowItWorks';
import { Login } from './components/Login';
import { config } from './config';

type Theme = 'light' | 'dark' | 'system';

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('theme') as Theme) ?? 'system');
  useEffect(() => {
    if (theme === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);
  return [theme, setTheme];
}

type Tab = 'dashboard' | 'how-it-works';
const tabFromHash = (): Tab => (window.location.hash.startsWith('#how-it-works') ? 'how-it-works' : 'dashboard');

function useTab(): [Tab, (t: Tab) => void] {
  const [tab, setTab] = useState<Tab>(tabFromHash);
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (t: Tab) => {
    window.location.hash = t === 'dashboard' ? '' : t;
    setTab(t);
  };
  return [tab, go];
}

export default function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [pending, setPending] = useState<string | null>(null);
  const [theme, setTheme] = useTheme();
  const [tab, setTab] = useTab();

  const refresh = () => config.guestAccess || config.localAgent ? Promise.resolve(setUser('')) : getCurrentUser().then(u => setUser(u.signInDetails?.loginId ?? u.username)).catch(() => setUser(null));
  useEffect(() => { void refresh(); }, []);

  if (user === undefined) return null;
  if (user === null) return <Login onSignedIn={refresh} />;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <div className="brand"><img src="/favicon.svg" alt="" width={22} height={22} /> FinOps</div>
          <nav className="tabs" role="tablist" aria-label="Views">
            <button role="tab" aria-selected={tab === 'dashboard'} className={tab === 'dashboard' ? 'on' : ''}
                    onClick={() => setTab('dashboard')}>Dashboard</button>
            <button role="tab" aria-selected={tab === 'how-it-works'} className={tab === 'how-it-works' ? 'on' : ''}
                    onClick={() => setTab('how-it-works')}>How it works</button>
          </nav>
        </div>
        <div className="topbar-right">
          <select value={theme} onChange={e => setTheme(e.target.value as Theme)} aria-label="Theme">
            <option value="system">System theme</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
          {config.localAgent && <span className="muted">local agent</span>}
          {!config.guestAccess && !config.localAgent && (
            <>
              <span className="muted">{user}</span>
              <button className="link" onClick={() => signOut().then(() => setUser(null))}>Sign out</button>
            </>
          )}
        </div>
      </header>
      <div className="layout">
        {/* the dashboard stays mounted (keeps its period and data) while "How it works" is shown */}
        <div className="view" hidden={tab !== 'dashboard'}>
          <ErrorBoundary label="the dashboard"><Dashboard onAsk={setPending} /></ErrorBoundary>
        </div>
        {tab === 'how-it-works' && (
          <div className="view"><ErrorBoundary label="How it works"><HowItWorks /></ErrorBoundary></div>
        )}
        <ErrorBoundary label="the conversation"><AskPanel pending={pending} onPendingTaken={() => setPending(null)} /></ErrorBoundary>
      </div>
    </div>
  );
}
