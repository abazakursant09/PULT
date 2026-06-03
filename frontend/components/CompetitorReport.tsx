import { type CompetitorReport, type Competitor } from '@/lib/api'
import { CompetitorCard } from './CompetitorCard'

interface Props { report: CompetitorReport }

const SECTIONS = [
  {
    key:      'direct'      as const,
    label:    'Прямые конкуренты',
    subtitle: 'Товары с аналогичными характеристиками и ценой',
    dotColor: '#3B82F6',
    badge:    'badge badge-direct',
  },
  {
    key:      'significant' as const,
    label:    'Значимые конкуренты',
    subtitle: 'Близкие альтернативы, влияющие на позиционирование',
    dotColor: 'rgba(26,115,232,0.5)',
    badge:    'badge badge-significant',
  },
  {
    key:      'minor'       as const,
    label:    'Незначительные конкуренты',
    subtitle: 'Слабо пересекаются с вашим предложением',
    dotColor: '#9A9897',
    badge:    'badge badge-minor',
  },
]

export function CompetitorReportView({ report }: Props) {
  return (
    <div className="space-y-10">
      {SECTIONS.map(s => {
        const items: Competitor[] = report[s.key]
        return (
          <section key={s.key} className="animate-fade-in">
            <div className="flex items-center gap-3 mb-5">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.dotColor }} />
              <div className="flex-1 min-w-0">
                <h2 className="text-sm font-semibold tracking-tight" style={{ color: '#202124' }}>{s.label}</h2>
                <p className="text-[11px] mt-0.5" style={{ color: 'rgba(0,0,0,0.38)' }}>{s.subtitle}</p>
              </div>
              <span className={s.badge}>{items.length}</span>
            </div>

            {items.length === 0 ? (
              <p className="text-sm pl-5 py-2" style={{ color: 'rgba(0,0,0,0.38)' }}>
                Нет конкурентов в этой категории
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pl-5">
                {items.map(c => <CompetitorCard key={c.id} competitor={c} />)}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
