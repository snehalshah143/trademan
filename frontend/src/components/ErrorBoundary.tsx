import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center min-h-screen bg-surface-0 p-8">
          <div className="max-w-2xl w-full bg-surface-1 border border-red-500/30 rounded-lg p-6">
            <h1 className="text-loss font-bold text-lg mb-2">Runtime Error</h1>
            <pre className="text-text-secondary text-xs bg-surface-2 rounded p-4 overflow-auto max-h-64 whitespace-pre-wrap">
              {this.state.error.message}
              {'\n\n'}
              {this.state.error.stack}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-4 px-4 py-2 bg-accent-blue text-white text-sm rounded-md"
            >
              Retry
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
