'use client'

import { useState, useRef } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Users, TrendingUp, Package, Sparkles, ArrowRight, CheckCircle2, ChevronDown } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectOption } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { BlurFade } from '@/components/ui/blur-fade'
import { ShimmerButton } from '@/components/ui/shimmer-button'
import { MagicCard } from '@/components/ui/magic-card'

const CATEGORIES = ['Одежда', 'Электроника', 'Handmade', 'Товары для дома', 'Другое']
const SEGMENTS   = ['Эконом', 'Средний', 'Премиум']

const RECS: Record<string, { niche: string; growth: string; platform: string; margin: string }> = {
  'Одежда':          { niche: 'базовые трикотажные изделия (термобельё, носки)',   growth: 'Спрос вырос на 22% за полгода, конкуренция умеренная',  platform: 'Wildberries', margin: '20–28%' },
  'Электроника':     { niche: 'аксессуары для смартфонов (держатели, кабели)',     growth: 'Стабильно высокий спрос, конкуренция средняя',           platform: 'Ozon',        margin: '18–25%' },
  'Handmade':        { niche: 'декоративные свечи ручной работы',                 growth: 'Спрос вырос на 45% за полгода, конкуренция низкая',      platform: 'Ozon',        margin: '35–50%' },
  'Товары для дома': { niche: 'домашний текстиль — подушки и пледы',              growth: 'Спрос вырос на 30% за полгода, конкуренция низкая',      platform: 'Ozon',        margin: '25–30%' },
  'Другое':          { niche: 'органайзеры и товары для хранения',                growth: 'Спрос стабильно растёт, конкуренция умеренная',          platform: 'Яндекс Маркет', margin: '22–30%' },
}

export default function StartupPage() {
  const router  = useRouter()
  const formRef = useRef<HTMLDivElement>(null)
  const recRef  = useRef<HTMLDivElement>(null)

  const [form,    setForm]    = useState({ category: '', segment: '', experience: '' })
  const [showRec, setShowRec] = useState(false)

  function scrollToForm() { formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }

  function handleSubmit() {
    if (!form.category || !form.segment || !form.experience) return
    setShowRec(true)
    setTimeout(() => recRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 150)
  }

  const rec       = RECS[form.category] || RECS['Товары для дома']
  const canSubmit = !!(form.category && form.segment && form.experience)

  return (
    <div className="min-h-screen" style={{ background: '#F6F9FC' }}>

      {/* Nav */}
      <nav className="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-border/50">
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-sm" style={{ background: '#1A73E8', color: 'white' }}>П</div>
            <span className="font-bold text-lg tracking-tight" style={{ color: '#0A2540' }}>ПУЛЬТ</span>
          </Link>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild><Link href="/login">Войти</Link></Button>
            <Button size="sm" asChild><Link href="/register">Начать бесплатно</Link></Button>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-5xl mx-auto px-6 pt-20 pb-16 text-center">
        <BlurFade inView>
          <div className="inline-flex items-center gap-2 mb-6 px-4 py-1.5 rounded-full" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.2)', color: '#1A73E8' }}>
            <Sparkles size={13} />
            <span className="text-xs font-semibold tracking-widest uppercase">Точка старта</span>
          </div>
          <h1 className="font-bold mb-5 leading-tight" style={{ fontSize: 'clamp(2rem, 5vw, 3rem)', color: '#0A2540', letterSpacing: '-0.03em' }}>
            Маркетплейсы — это ваш шанс.<br />
            <span style={{ color: '#1A73E8' }}>Мы поможем его не упустить.</span>
          </h1>
          <p className="max-w-xl mx-auto mb-10 text-muted-foreground text-lg leading-relaxed">
            От идеи до готовой карточки товара — за 30 дней с Бизнес‑Пультом.
          </p>
          <ShimmerButton onClick={scrollToForm} className="px-8 py-4">
            Найти свой товар <ChevronDown size={16} className="ml-2" />
          </ShimmerButton>
        </BlurFade>
      </section>

      {/* Why section */}
      <section className="max-w-5xl mx-auto px-6 pb-16">
        <BlurFade inView>
          <h2 className="text-center font-bold text-3xl mb-10" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>Почему маркетплейсы?</h2>
        </BlurFade>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
          {[
            { Icon: Users,      title: '100+ млн покупателей', desc: 'Готовая аудитория на WB, Ozon и Яндекс Маркет — не нужно привлекать трафик самому.', delay: 0.05 },
            { Icon: TrendingUp, title: 'Вход от 15 000 ₽',    desc: 'Начать можно с небольшой тестовой партии — риски минимальны, результат виден быстро.', delay: 0.1 },
            { Icon: Package,    title: 'Не нужен свой сайт',   desc: 'Маркетплейс берёт на себя логистику, платежи и возвраты. Вы фокусируетесь на товаре.', delay: 0.15 },
          ].map(({ Icon, title, desc, delay }) => (
            <BlurFade key={title} inView delay={delay}>
              <MagicCard className="p-6 h-full">
                <div className="w-11 h-11 rounded-xl flex items-center justify-center mb-4" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.15)' }}>
                  <Icon size={20} style={{ color: '#1A73E8' }} />
                </div>
                <h3 className="font-semibold text-base mb-2" style={{ color: '#0A2540' }}>{title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{desc}</p>
              </MagicCard>
            </BlurFade>
          ))}
        </div>
      </section>

      {/* Form / Recommendation */}
      <section ref={formRef} className="max-w-5xl mx-auto px-6 pb-24">
        <BlurFade inView delay={0.1}>
          <Card className="shadow-stripe-lg">
            <CardContent className="p-8">
              {!showRec ? (
                <>
                  <h2 className="font-bold text-2xl mb-2" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>Что вы будете продавать?</h2>
                  <p className="text-muted-foreground mb-8">Заполните форму — мы подберём лучшую нишу и площадку именно для вас.</p>
                  <div className="space-y-5">
                    <div className="space-y-1.5">
                      <Label>Категория товара</Label>
                      <Select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
                        <SelectOption value="">Выберите категорию</SelectOption>
                        {CATEGORIES.map(c => <SelectOption key={c} value={c}>{c}</SelectOption>)}
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label>Ценовой сегмент</Label>
                      <Select value={form.segment} onChange={e => setForm(f => ({ ...f, segment: e.target.value }))}>
                        <SelectOption value="">Выберите сегмент</SelectOption>
                        {SEGMENTS.map(s => <SelectOption key={s} value={s}>{s}</SelectOption>)}
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <Label>Есть ли опыт в продажах?</Label>
                      <div className="flex gap-3">
                        {['Да', 'Нет'].map(v => (
                          <button
                            key={v} type="button"
                            onClick={() => setForm(f => ({ ...f, experience: v }))}
                            className="flex-1 py-3 rounded-xl text-sm font-medium transition-all border"
                            style={form.experience === v
                              ? { background: 'rgba(26,115,232,0.08)', color: '#1A73E8', borderColor: 'rgba(26,115,232,0.3)' }
                              : { background: 'transparent', color: '#425466', borderColor: 'hsl(var(--border))' }
                            }
                          >
                            {v}
                          </button>
                        ))}
                      </div>
                    </div>
                    <Button className="w-full" size="lg" onClick={handleSubmit} disabled={!canSubmit}>
                      Получить рекомендацию <ArrowRight size={15} className="ml-1.5" />
                    </Button>
                  </div>
                </>
              ) : (
                <div ref={recRef}>
                  {/* AI recommendation */}
                  <div className="mb-6 p-5 rounded-2xl" style={{ background: 'rgba(26,115,232,0.05)', border: '1px solid rgba(26,115,232,0.2)' }}>
                    <div className="flex items-center gap-2 mb-3">
                      <Sparkles size={14} style={{ color: '#1A73E8' }} />
                      <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#1A73E8' }}>Персональная рекомендация</span>
                    </div>
                    <p className="text-base leading-relaxed" style={{ color: '#0A2540' }}>
                      Сейчас выгодно продавать <strong>{rec.niche}</strong> — {rec.growth.toLowerCase()}.
                    </p>
                    <p className="mt-2 text-sm text-muted-foreground">
                      Лучшая площадка: <strong style={{ color: '#1A73E8' }}>{rec.platform}</strong>. Расчётная маржинальность: <strong>{rec.margin}</strong>.
                    </p>
                  </div>

                  {/* Pricing */}
                  <Card className="shadow-stripe border-2" style={{ borderColor: 'rgba(26,115,232,0.25)' }}>
                    <CardContent className="p-6">
                      <h3 className="font-bold text-xl mb-2" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>
                        Готовы запустить свой бизнес на маркетплейсе?
                      </h3>
                      <p className="text-muted-foreground mb-4 leading-relaxed text-sm">
                        Мы проведём вас через 6 шагов: от регистрации до первой карточки товара.
                      </p>
                      <div className="flex flex-wrap items-baseline gap-2 mb-5">
                        <span className="font-bold text-2xl" style={{ color: '#0A2540' }}>9 990 ₽</span>
                        <span className="text-muted-foreground">тариф «Старт»</span>
                        <Badge variant="success" className="text-xs">+ 1 мес. «Мастера» (6 990 ₽) бесплатно</Badge>
                      </div>
                      <ul className="space-y-2 mb-6">
                        {['6 шагов от идеи до первой продажи', 'Персональная рекомендация ниши', 'Готовый бизнес-план за 30 минут', 'SEO-карточка товара от ИИ'].map(f => (
                          <li key={f} className="flex items-center gap-2 text-sm text-muted-foreground">
                            <CheckCircle2 size={14} style={{ color: '#1A73E8', flexShrink: 0 }} /> {f}
                          </li>
                        ))}
                      </ul>
                      <div className="flex flex-col sm:flex-row gap-3">
                        <Button className="flex-1" size="lg" onClick={() => router.push('/checkout?plan=start')}>
                          Оформить «Старт» <ArrowRight size={15} className="ml-1.5" />
                        </Button>
                        <Button variant="ghost" className="flex-1" size="lg" onClick={() => router.push('/academy')}>
                          Вернуться к бесплатной версии
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              )}
            </CardContent>
          </Card>
        </BlurFade>
      </section>

      {/* Footer */}
      <footer className="border-t border-border py-6 text-center">
        <p className="text-sm text-muted-foreground">
          © 2025 Бизнес‑Пульт ·{' '}
          <Link href="/login" className="hover:underline" style={{ color: '#1A73E8' }}>Войти</Link>
          {' · '}
          <Link href="/register" className="hover:underline" style={{ color: '#1A73E8' }}>Зарегистрироваться</Link>
        </p>
      </footer>
    </div>
  )
}
