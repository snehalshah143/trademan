import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { alertRuleService } from '@/services/alertService'

function fmtDatetime(iso: string): string {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export function AlertHistory() {
  const qc = useQueryClient()
  const [ruleIdFilter, setRuleIdFilter] = useState('')
  const [limit, setLimit] = useState(50)

  const { data: history = [], isLoading } = useQuery({
    queryKey: ['alert-history', ruleIdFilter, limit],
    queryFn: () => alertRuleService.getHistory({
      rule_id: ruleIdFilter || undefined,
      limit,
    }),
    staleTime: 15_000,
  })

  const clearMut = useMutation({
    mutationFn: () => alertRuleService.clearHistory(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert-history'] })
      qc.invalidateQueries({ queryKey: ['alert-stats'] })
      toast.success('History cleared')
    },
    onError: () => toast.error('Failed to clear history'),
  })

  return (
    <div className="flex flex-col gap-4 p-6">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <input
          value={ruleIdFilter}
          onChange={e => setRuleIdFilter(e.target.value)}
          placeholder="Filter by rule ID…"
          className="flex-1 px-3 py-1.5 text-xs bg-surface-2 border border-border-default rounded text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
        />
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          className="px-2 py-1.5 text-xs bg-surface-2 border border-border-default rounded text-text-primary focus:outline-none"
        >
          <option value={25}>25</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
          <option value={200}>200</option>
        </select>
        <button
          onClick={() => { if (confirm('Clear all alert history?')) clearMut.mutate() }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-loss border border-loss/40 rounded hover:bg-loss/10 transition-colors"
        >
          <Trash2 size={12} />
          Clear All
        </button>
      </div>

      {/* Table */}
      <div className="bg-surface-2 border border-border-subtle rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-subtle text-text-muted">
              <th className="text-left px-4 py-2.5 font-medium">Time</th>
              <th className="text-left px-4 py-2.5 font-medium">Message</th>
              <th className="text-left px-4 py-2.5 font-medium">Symbol</th>
              <th className="text-left px-4 py-2.5 font-medium">Severity</th>
              <th className="text-left px-4 py-2.5 font-medium">Rule ID</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {isLoading ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-text-muted">Loading…</td>
              </tr>
            ) : history.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-text-muted">No alert history</td>
              </tr>
            ) : history.map(item => (
              <tr key={item.id} className="hover:bg-surface-3/30 transition-colors">
                <td className="px-4 py-2.5 text-text-muted tabular-nums whitespace-nowrap">
                  {fmtDatetime(item.triggered_at)}
                </td>
                <td className="px-4 py-2.5 text-text-primary max-w-xs">
                  <p className="truncate">{item.message}</p>
                </td>
                <td className="px-4 py-2.5 text-text-secondary">{item.symbol ?? '—'}</td>
                <td className="px-4 py-2.5">
                  <span className={cn(
                    'px-1.5 py-0.5 rounded text-[10px] font-medium',
                    item.severity === 'CRITICAL' ? 'bg-loss/10 text-loss' :
                    item.severity === 'WARNING'  ? 'bg-accent-amber/10 text-accent-amber' :
                    'bg-accent-blue/10 text-accent-blue'
                  )}>
                    {item.severity}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-text-muted font-mono text-[10px]">
                  {item.rule_id ? item.rule_id.slice(0, 8) + '…' : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
