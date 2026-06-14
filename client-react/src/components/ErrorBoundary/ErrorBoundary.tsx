import { Component, type ErrorInfo, type ReactNode } from "react";
import { clearGraphCache } from "../../utils/graphCache";
import "./ErrorBoundary.css";

// One automatic recovery per browsing session. A render crash is most often a
// poisoned graph cache (a stale/mismatched topology painted against fresh
// votes), so the first crash clears that cache and reloads ONCE. The flag stops
// a genuinely-reproducible crash from reload-looping ("a problem repeatedly
// occurred") — the second time we show a manual fallback instead.
const RECOVERY_FLAG = "graph-cache-recovery-attempted";

interface Props {
  children: ReactNode;
}

interface State {
  crashed: boolean;
  recovering: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { crashed: false, recovering: false };
  private healthyTimer: number | undefined;

  static getDerivedStateFromError(): Partial<State> {
    return { crashed: true };
  }

  componentDidMount(): void {
    // Survived ~12s without crashing → consider the session healthy and clear the
    // recovery flag, so a much-later, unrelated crash can still auto-recover once.
    this.healthyTimer = window.setTimeout(() => {
      try {
        sessionStorage.removeItem(RECOVERY_FLAG);
      } catch {
        // sessionStorage may be unavailable (private mode) — ignore.
      }
    }, 12000);
  }

  componentWillUnmount(): void {
    if (this.healthyTimer) window.clearTimeout(this.healthyTimer);
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Render crash caught by ErrorBoundary:", error, info.componentStack);

    let alreadyTried = false;
    try {
      alreadyTried = sessionStorage.getItem(RECOVERY_FLAG) === "1";
      sessionStorage.setItem(RECOVERY_FLAG, "1");
    } catch {
      // No sessionStorage → fall through to a single best-effort reload.
    }

    if (alreadyTried) {
      // Second crash this session — don't loop. Show the manual fallback.
      this.setState({ recovering: false });
      return;
    }

    // First crash — wipe the persisted graph cache (the usual culprit) and reload
    // fresh. Reload only after the clear resolves so the next load starts clean.
    this.setState({ recovering: true });
    clearGraphCache().finally(() => {
      window.location.reload();
    });
  }

  private handleManualReload = (): void => {
    try {
      sessionStorage.removeItem(RECOVERY_FLAG);
    } catch {
      // ignore
    }
    clearGraphCache().finally(() => window.location.reload());
  };

  render(): ReactNode {
    if (!this.state.crashed) return this.props.children;

    return (
      <div className="error-boundary">
        <div className="error-boundary-card">
          <h1 className="error-boundary-title">Something went wrong</h1>
          <p className="error-boundary-body">
            {this.state.recovering
              ? "Refreshing the map with a clean cache…"
              : "The map hit an unexpected error. Reloading with a cleared cache usually fixes it."}
          </p>
          {!this.state.recovering && (
            <button className="error-boundary-button" onClick={this.handleManualReload}>
              Reload the map
            </button>
          )}
        </div>
      </div>
    );
  }
}
