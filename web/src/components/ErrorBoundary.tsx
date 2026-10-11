import { Component, type ErrorInfo, type ReactNode } from 'react';

/**
 * Contains a render error to the part of the page that failed. Without it, React unmounts the whole root on an
 * uncaught render error and the page goes blank - which is what one malformed tool result used to do.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; fallback?: (error: Error, reset: () => void) => ReactNode; label?: string; resetKey?: unknown },
  { error: Error | null; resetKey?: unknown }
> {
  state: { error: Error | null; resetKey?: unknown } = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(props: { resetKey?: unknown }, state: { error: Error | null; resetKey?: unknown }) {
    // new data for the same slot (e.g. another report period) gets a fresh try
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[finops-ui] ${this.props.label ?? 'view'} failed to render`, error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="banner error" role="alert">
        Could not display {this.props.label ?? 'this part'}: {error.message}{' '}
        <button className="link" onClick={this.reset}>Try again</button>
      </div>
    );
  }
}
