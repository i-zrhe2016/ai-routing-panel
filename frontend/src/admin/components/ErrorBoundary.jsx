import { Component } from "react";

import { reportRenderError } from "../state/PanelProvider.jsx";

// A render crash must not blank the console: show the failure, report it to the
// same /api/client-errors path the rest of the SPA uses, and keep the shell.
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    reportRenderError(error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <section className="cc-card cc-card--error" role="alert">
          <p className="section-kicker">RENDER ERROR</p>
          <h3>当前工作区渲染失败</h3>
          <p>{this.state.error.message || String(this.state.error)}</p>
          <button className="a-btn secondary" type="button" onClick={() => this.setState({ error: null })}>
            重试渲染
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}
