'use client'

import { TrendingUp, TrendingDown, Minus, Cpu, User } from 'lucide-react'
import { type PriceChangeLog } from '@/lib/api'

interface Props {
  history: PriceChangeLog[]
}

function fmt(n: number) { return n.toLocaleString('ru-RU') }

export function PriceHistory({ history }: Props) {
  if (history.length === 0) {
    return (
      <div className="card p-8 text-center">
        <p className="text-sm" style={{ color: 'rgba(0,0,0,0.38)' }}>История изменений пуста</p>
      </div>
    )
  }

  return (
    <div className="card overflow-hidden">
      <div className="card-line" />
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[560px]">
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(26,115,232,0.1)' }}>
              {['Дата', 'Было', 'Стало', 'Изм.', 'Причина', 'Источник'].map(h => (
                <th key={h}
                    className={`px-4 py-3 label whitespace-nowrap ${
                      h === 'Было' || h === 'Стало' || h === 'Изм.' ? 'text-right' :
                      h === 'Источник' ? 'text-center' : 'text-left'
                    }`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {history.map((entry, i) => {
              const delta = entry.new_price - entry.old_price
              const pct   = entry.old_price > 0 ? (delta / entry.old_price) * 100 : 0
              const up    = delta > 0
              const flat  = Math.abs(delta) < 0.01
              const deltaColor = flat ? '#9A9897' : up ? '#3B82F6' : '#8A8986'

              return (
                <tr key={entry.id}
                    style={{ borderBottom: i < history.length - 1 ? '1px solid rgba(26,115,232,0.08)' : 'none' }}>
                  <td className="px-4 py-3 text-xs whitespace-nowrap" style={{ color: 'rgba(0,0,0,0.38)' }}>
                    {new Date(entry.created_at).toLocaleString('ru-RU', {
                      day: '2-digit', month: '2-digit', year: '2-digit',
                      hour: '2-digit', minute: '2-digit',
                    })}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs" style={{ color: '#8A8986' }}>
                    {fmt(entry.old_price)} ₽
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs font-semibold" style={{ color: '#202124' }}>
                    {fmt(entry.new_price)} ₽
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="inline-flex items-center justify-end gap-0.5 text-xs font-semibold"
                          style={{ color: deltaColor }}>
                      {flat  ? <Minus        size={10} /> :
                       up    ? <TrendingUp   size={10} /> :
                               <TrendingDown size={10} />}
                      {flat ? '—' : `${up ? '+' : ''}${pct.toFixed(1)}%`}
                    </span>
                  </td>
                  <td className="px-4 py-3" style={{ maxWidth: 240 }}>
                    <span className="text-xs block truncate" style={{ color: '#8A8986' }}
                          title={entry.reason}>
                      {entry.reason}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-md"
                          style={entry.source === 'auto'
                            ? { background: 'rgba(26,115,232,0.1)', color: '#1A73E8',
                                border: '1px solid rgba(26,115,232,0.18)' }
                            : { background: '#F1F3F4', color: '#8A8986',
                                border: '1px solid rgba(26,115,232,0.1)' }
                          }>
                      {entry.source === 'auto'
                        ? <><Cpu  size={9} /> Авто</>
                        : <><User size={9} /> Ручной</>}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
