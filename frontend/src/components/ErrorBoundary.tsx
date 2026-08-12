import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

// Keeps one bad panel from taking the whole dashboard down. Wrapped around the
// tab content with key={tab}, so switching tabs mounts a fresh boundary and the
// nav always stays usable — the failure mode is a dead panel, never a dead app.

type Props = { children: ReactNode; label?: string; hint?: string }
type State = { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', this.props.label ?? 'app', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="rounded-2xl border p-8 space-y-4"
        style={{ background: 'var(--surface)', borderColor: 'rgba(255,59,48,0.25)' }}>
        <div className="space-y-1">
          <div className="text-sm font-medium" style={{ color: 'var(--red)' }}>
            ⚠ This panel failed to render
          </div>
          <div className="text-xs" style={{ color: '#888' }}>
            {this.props.hint ?? 'The rest of the dashboard still works — switch tabs to keep going.'}
          </div>
        </div>

        <pre className="text-xs font-mono whitespace-pre-wrap rounded-lg p-3 overflow-x-auto"
          style={{ background: 'var(--bg)', color: '#777' }}>
          {error.message || String(error)}
        </pre>

        <div className="flex gap-2">
          <button
            onClick={() => this.setState({ error: null })}
            className="px-4 py-1.5 rounded-lg text-xs font-medium cursor-pointer"
            style={{ background: 'var(--surface-2)', color: 'white' }}
          >
            Try again
          </button>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-1.5 rounded-lg text-xs font-medium cursor-pointer"
            style={{ color: '#666' }}
          >
            Reload
          </button>
        </div>
      </div>
    )
  }
}
