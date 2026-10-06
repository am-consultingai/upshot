import { Component, type ErrorInfo, type ReactNode } from "react";
import { reportClientError } from "../lib/errors";

/**
 * A screen that throws while rendering shows this instead of a blank window, and the
 * error goes to the local server like any other (D87, C5). The words come in from the
 * caller, which has the language; a class component cannot use the i18n hook.
 */
export default class ErrorBoundary extends Component<
  { title: string; body: string; reload: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    reportClientError("render", error);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div data-testid="error-boundary" role="alert" className="m-auto max-w-md p-8 text-center">
        <h1 className="display mb-2 text-2xl">{this.props.title}</h1>
        <p className="mb-4 text-sm text-secondary">{this.props.body}</p>
        <button
          type="button"
          data-testid="error-reload"
          className="rounded bg-accent px-3 py-1.5 text-sm text-on-accent"
          onClick={() => window.location.reload()}
        >
          {this.props.reload}
        </button>
      </div>
    );
  }
}
