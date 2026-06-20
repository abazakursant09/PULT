'use client'
import { fmtRub, type Money } from '@/lib/pultProduct'

/** MoneyStrip — прибыль сегодня / 7 дней / 30 дней + изменение. Центр «расту/падаю». */
export function MoneyStrip({ money }: { money: Money }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 12, marginBottom: 22 }}>
      <Stat label="Прибыль · 30 дней" value={fmtRub(money.d30)} big pos={money.d30 >= 0}
        delta={`${money.d30Delta >= 0 ? '▲' : '▼'} ${Math.abs(money.d30Delta)}% к прошлому периоду`} />
      <Stat label="Сегодня" value={fmtRub(money.today)} pos={money.today >= 0} />
      <Stat label="7 дней" value={fmtRub(money.d7)} pos={money.d7 >= 0} />
    </div>
  )
}

function Stat({ label, value, big, pos, delta }: { label: string; value: string; big?: boolean; pos: boolean; delta?: string }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-3)', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: big ? 30 : 22, fontWeight: 800, lineHeight: 1, color: pos ? 'var(--success)' : 'var(--danger)' }}>{value}</div>
      {delta && <div style={{ fontSize: 12, fontWeight: 700, color: pos ? 'var(--success)' : 'var(--danger)', marginTop: 7 }}>{delta}</div>}
    </div>
  )
}
