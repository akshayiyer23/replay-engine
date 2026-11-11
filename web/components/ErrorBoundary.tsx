"use client";

import { Component, ReactNode, ErrorInfo } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary] render crash:", error.message, info.componentStack?.slice(0, 400));
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="px-4 py-3 rounded-lg bg-red-50 border border-red-200">
            <p className="text-sm text-red-700 font-medium">
              The run was interrupted — try again or pick one of the 11 webinars above.
            </p>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
