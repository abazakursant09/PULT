'use client'
import { useState, useEffect, useCallback } from 'react'
import { Stamp, AlertTriangle, CheckCircle2, RefreshCw, Search, Info } from 'lucide-react'
import { api } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

const A   = 'var(--violet-text)'
const ABG = 'color-mix(in srgb, var(--violet) 8%, transparent)'

interface ProductStatus {
  product_id: string
  product_name: string
  category: string | null
  requires_marking: boolean
  regulation: string | null
}

interface CheckResult {
  category: string
  requires_marking: boolean
  regulation: string | null
  warning: string | null
}

export default function MarkingPage() {
  const [products,     setProducts]     = useState<ProductStatus[]>([])
  const [loading,      setLoading]      = useState(false)
  const [lastScan,     setLastScan]     = useState<Date | null>(null)
  const [checkCat,     setCheckCat]     = useState('')
  const [checkResult,  setCheckResult]  = useState<CheckResult | null>(null)
  const [checking,     setChecking]     = useState(false)

  const scan = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/marking/scan', {
        credentials: 'include',    // SECURITY-2B-2 — HttpOnly cookie, no Bearer
      })
      if (!r.ok) throw new Error()
      const data: ProductStatus[] = await r.json()
      setProducts(data)
      setLastScan(new Date())
    } catch {
      setProducts([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { scan() }, [scan])

  async function checkCategory() {
    if (!checkCat.trim()) return
    setChecking(true)
    try {
      const r = await fetch(`/api/marking/check?category=${encodeURIComponent(checkCat)}`, {
        credentials: 'include',    // SECURITY-2B-2 — HttpOnly cookie, no Bearer
      })
      const data: CheckResult = await r.json()
      setCheckResult(data)
    } catch {
      setCheckResult(null)
    } finally {
      setChecking(false)
    }
  }

  const flagged = products.filter(p => p.requires_marking)
  const clean   = products.filter(p => !p.requires_marking)

  return (
    <div className="min-h-screen" style={{ position: 'relative' }}>
      <div aria-hidden style={{ position: 'fixed', top: '10%', right: '-6%', width: 380, height: 380, background: 'radial-gradient(circle, color-mix(in srgb, var(--violet) 5%, transparent) 0%, transparent 65%)', animation: 'orbDrift 18s ease-in-out infinite', filter: 'blur(44px)', pointerEvents: 'none', zIndex: 0 }} />
      <main className="max-w-[960px] mx-auto px-4 sm:px-8 py-10 animate-fade-in" style={{ position: 'relative', zIndex: 1 }}>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="font-bold" style={{ fontSize: 'clamp(1.25rem,2.5vw,1.75rem)', color: 'var(--text)' }}>
              Честный ЗНАК
            </h1>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-2)' }}>
              Проверка товаров на обязательную маркировку
              {lastScan && <> · последняя проверка {lastScan.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</>}
            </p>
          </div>
          <Button onClick={scan} loading={loading}>
            {!loading && <><RefreshCw size={14} /> Проверить все товары</>}
          </Button>
        </div>

        <Card className="p-5 mb-6 flex items-start gap-4" style={{ border: '1px solid color-mix(in srgb, var(--violet) 20%, transparent)', background: ABG }}>
          <Info size={18} style={{ color: A, flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: '0.875rem', color: 'var(--text-2)', lineHeight: 1.65 }}>
            <strong style={{ color: 'var(--text)' }}>Что такое Честный ЗНАК?</strong> — государственная система обязательной
            маркировки товаров в России (crpt.ru). Продавцы обязаны наносить QR-код на товары из регулируемых категорий.
            Продажа немаркированного товара грозит штрафом до 300 000 ₽.{' '}
            Проверка обновляется автоматически раз в неделю и при изменении перечня товаров.
          </div>
        </Card>

        <Card className="p-6 mb-6">
          <h2 className="font-semibold mb-4" style={{ fontSize: '1rem', color: 'var(--text)' }}>
            Быстрая проверка категории
          </h2>
          <div className="flex gap-3">
            <Input
              className="flex-1"
              placeholder="Введите категорию, напр. «Обувь», «Парфюмерия», «Молочная продукция»..."
              value={checkCat}
              onChange={e => { setCheckCat(e.target.value); setCheckResult(null) }}
              onKeyDown={e => e.key === 'Enter' && checkCategory()}
            />
            <Button onClick={checkCategory} disabled={!checkCat.trim()} loading={checking}>
              {!checking && <><Search size={14} /> Проверить</>}
            </Button>
          </div>

          {checkResult && (
            <div
              className="mt-4 p-4 rounded-xl flex items-start gap-3"
              style={{
                background: checkResult.requires_marking ? 'rgba(220,38,38,0.05)' : 'color-mix(in srgb, var(--violet) 5%, transparent)',
                border: `1px solid ${checkResult.requires_marking ? 'rgba(220,38,38,0.2)' : 'color-mix(in srgb, var(--violet) 20%, transparent)'}`,
              }}
            >
              {checkResult.requires_marking
                ? <AlertTriangle size={16} style={{ color: 'var(--danger)', flexShrink: 0, marginTop: 2 }} />
                : <CheckCircle2 size={16} style={{ color: 'var(--violet-text)', flexShrink: 0, marginTop: 2 }} />
              }
              <div>
                {checkResult.requires_marking ? (
                  <>
                    <p className="font-semibold" style={{ fontSize: '0.875rem', color: 'var(--danger)' }}>
                      Требуется маркировка «Честный ЗНАК»
                    </p>
                    <p style={{ fontSize: '0.8125rem', color: 'var(--text-2)', marginTop: 4 }}>
                      {checkResult.warning}
                    </p>
                  </>
                ) : (
                  <p className="font-semibold" style={{ fontSize: '0.875rem', color: 'var(--violet-text)' }}>
                    Категория «{checkResult.category}» не требует обязательной маркировки
                  </p>
                )}
              </div>
            </div>
          )}
        </Card>

        {products.length > 0 ? (
          <>
            <div className="grid grid-cols-3 gap-4 mb-6">
              {[
                { label: 'Всего товаров',         value: products.length, color: 'var(--text)' },
                { label: 'Требуют маркировки',    value: flagged.length,  color: flagged.length > 0 ? 'var(--danger)' : 'var(--violet)' },
                { label: 'Не требуют маркировки', value: clean.length,    color: 'var(--violet-text)' },
              ].map(s => (
                <Card key={s.label} className="p-5 text-center">
                  <div className="font-bold" style={{ fontSize: '1.75rem', color: s.color }}>{s.value}</div>
                  <div className="text-xs text-muted-foreground mt-1">{s.label}</div>
                </Card>
              ))}
            </div>

            {flagged.length > 0 && (
              <Card className="overflow-hidden mb-6">
                <div style={{ padding: '16px 24px', borderBottom: '1px solid rgba(220,38,38,0.15)', background: 'rgba(220,38,38,0.03)' }}>
                  <h2 className="font-semibold flex items-center gap-2" style={{ fontSize: '0.9375rem', color: 'var(--danger)' }}>
                    <AlertTriangle size={15} /> Требуют маркировки ({flagged.length})
                  </h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr style={{ borderBottom: '1px solid color-mix(in srgb, var(--violet) 10%, transparent)' }}>
                        {['Товар', 'Категория', 'Основание'].map(h => (
                          <th key={h} className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wide">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {flagged.map((p, i) => (
                        <tr key={p.product_id} style={{ borderBottom: i < flagged.length - 1 ? '1px solid color-mix(in srgb, var(--violet) 8%, transparent)' : 'none' }}>
                          <td className="px-4 py-3" style={{ fontSize: '0.875rem', color: 'var(--text)', fontWeight: 500 }}>{p.product_name}</td>
                          <td className="px-4 py-3">
                            <Badge variant="destructive">{p.category || '—'}</Badge>
                          </td>
                          <td className="px-4 py-3" style={{ fontSize: '0.8125rem', color: 'var(--text-2)' }}>{p.regulation || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {clean.length > 0 && (
              <Card className="overflow-hidden">
                <div style={{ padding: '16px 24px', borderBottom: '1px solid color-mix(in srgb, var(--violet) 12%, transparent)', background: 'color-mix(in srgb, var(--violet) 4%, transparent)' }}>
                  <h2 className="font-semibold flex items-center gap-2" style={{ fontSize: '0.9375rem', color: 'var(--violet-text)' }}>
                    <CheckCircle2 size={15} /> Маркировка не требуется ({clean.length})
                  </h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr style={{ borderBottom: '1px solid color-mix(in srgb, var(--violet) 10%, transparent)' }}>
                        {['Товар', 'Категория'].map(h => (
                          <th key={h} className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wide">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {clean.map((p, i) => (
                        <tr key={p.product_id} style={{ borderBottom: i < clean.length - 1 ? '1px solid color-mix(in srgb, var(--violet) 8%, transparent)' : 'none' }}>
                          <td className="px-4 py-3" style={{ fontSize: '0.875rem', color: 'var(--text)', fontWeight: 500 }}>{p.product_name}</td>
                          <td className="px-4 py-3">
                            <Badge variant="outline" style={{ color: 'var(--violet-text)', borderColor: 'color-mix(in srgb, var(--violet) 20%, transparent)' }}>
                              {p.category || 'без категории'}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>
        ) : !loading && (
          <Card className="p-12 text-center">
            <Stamp size={40} style={{ color: 'color-mix(in srgb, var(--violet) 25%, transparent)', margin: '0 auto 12px' }} />
            <p style={{ fontSize: '0.9375rem', color: 'var(--text-2)' }}>
              Товаров не найдено. Добавьте товары в разделе «Мои товары» — они появятся здесь автоматически.
            </p>
          </Card>
        )}

        <Card className="mt-8 p-5">
          <h3 className="font-semibold mb-3" style={{ fontSize: '0.875rem', color: 'var(--text)' }}>
            Категории, требующие маркировки «Честный ЗНАК»
          </h3>
          <div className="flex flex-wrap gap-2">
            {['Одежда', 'Обувь', 'Парфюмерия / Духи', 'Постельное бельё', 'Шины', 'Фототехника',
              'Молочная продукция', 'Питьевая вода', 'Пиво', 'Табак', 'Лекарства', 'БАДы'].map(cat => (
              <Badge key={cat} variant="outline" style={{ color: 'var(--text-2)', borderColor: 'color-mix(in srgb, var(--violet) 18%, transparent)', fontSize: '0.75rem' }}>
                {cat}
              </Badge>
            ))}
          </div>
          <p className="mt-3" style={{ fontSize: '0.75rem', color: 'var(--text-2)' }}>
            Перечень актуален на дату последнего обновления. Проверяйте изменения на сайте crpt.ru.
          </p>
        </Card>

      </main>
    </div>
  )
}
