'use client'

import Link from 'next/link'
import { BookOpen, ArrowRight, Clock, Sparkles } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { BlurFade } from '@/components/ui/blur-fade'
import { MagicCard } from '@/components/ui/magic-card'

interface Lesson {
  slug:    string
  title:   string
  section: 'start' | 'growth' | 'profi' | 'legal'
  icon:    string
  time:    string
  desc:    string
}

const LESSONS: Lesson[] = [
  { slug: 'choose-niche',       section: 'start',  icon: '🔰', time: '8 мин',  title: 'Как выбрать нишу для старта на WB/Ozon',               desc: 'Пошаговый метод оценки ниши за 30 минут без платных сервисов' },
  { slug: 'ip-vs-selfemployed', section: 'start',  icon: '📋', time: '5 мин',  title: 'Самозанятый vs ИП: что выбрать в 2026 году',           desc: 'Налоговые режимы, ограничения и что подходит для маркетплейсов' },
  { slug: 'first-delivery',     section: 'start',  icon: '📦', time: '10 мин', title: 'Первая поставка: чек-лист из 10 шагов',                desc: 'Что сделать до, во время и после первой поставки на склад МП' },
  { slug: 'card-optimization',  section: 'start',  icon: '🎯', time: '12 мин', title: 'Как заполнить карточку товара, чтобы она попала в топ', desc: 'SEO-оптимизация, фото и характеристики для высокой позиции' },
  { slug: 'reviews-strategy',   section: 'growth', icon: '⭐', time: '7 мин',  title: 'Как работать с отзывами: стратегия 4.9 рейтинга',      desc: 'Инструменты для сбора отзывов и работа с негативом' },
  { slug: 'pricing-no-dumping', section: 'growth', icon: '💰', time: '9 мин',  title: 'Ценообразование без демпинга: сохраняем маржу',         desc: 'Конкурируйте по ценности, не уничтожая прибыль' },
  { slug: 'internal-ads',       section: 'growth', icon: '📢', time: '11 мин', title: 'Внутренняя реклама WB/Ozon: как не слить бюджет',       desc: 'Настройка кампаний, ставки и оценка эффективности' },
  { slug: 'second-marketplace', section: 'profi',  icon: '🚀', time: '9 мин',  title: 'Выход на второй маркетплейс: плюсы и риски',            desc: 'Когда и как масштабироваться на Ozon, WB или Яндекс Маркет' },
  { slug: 'unit-economics',     section: 'profi',  icon: '📊', time: '13 мин', title: 'Юнит-экономика для селлера: считаем чистую прибыль',    desc: 'Как посчитать реальную прибыль с одной единицы товара' },
  { slug: 'law-169',            section: 'legal',  icon: '⚖️', time: '6 мин',  title: 'Закон 169-ФЗ: какие слова запрещены в карточках',       desc: 'Что нельзя писать, чтобы не получить штраф и блокировку' },
  { slug: 'honest-sign',        section: 'legal',  icon: '🏷️', time: '7 мин',  title: 'Честный ЗНАК: для каких товаров обязательна маркировка', desc: 'Актуальный список товарных групп с обязательной маркировкой' },
  { slug: 'certification-2026', section: 'legal',  icon: '📜', time: '8 мин',  title: 'Новые требования к сертификации с 2026 года',            desc: 'Какие документы нужны для продажи на WB и Ozon и как их получить' },
]

const SECTIONS: { key: Lesson['section']; label: string; color: string; text: string }[] = [
  { key: 'start',  label: '🔰 Старт',  color: 'rgba(26,115,232,0.08)', text: '#1A73E8' },
  { key: 'growth', label: '📈 Рост',   color: 'rgba(26,115,232,0.08)', text: '#1A73E8' },
  { key: 'profi',  label: '🚀 Профи',  color: 'rgba(99,102,241,0.1)',  text: '#6366F1' },
  { key: 'legal',  label: '⚖️ Право',  color: 'rgba(220,38,38,0.07)',  text: '#dc2626' },
]

export default function AcademyPage() {
  return (
    <AppShell>
      <div className="flex-1 px-4 sm:px-6 py-8" style={{ background: '#F6F9FC' }}>
        <div className="max-w-5xl mx-auto">

          {/* Hero */}
          <BlurFade inView className="mb-8">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-11 h-11 rounded-2xl flex items-center justify-center shadow-stripe" style={{ background: '#1A73E8' }}>
                <BookOpen size={20} style={{ color: 'white' }} />
              </div>
              <h1 className="text-3xl font-bold" style={{ color: '#0A2540', letterSpacing: '-0.03em' }}>Академия</h1>
            </div>
            <p className="text-muted-foreground text-base max-w-lg leading-relaxed">
              12 уроков от выбора ниши до юридической защиты бизнеса. Читайте бесплатно.
            </p>
          </BlurFade>

          {/* Upgrade banner */}
          <BlurFade inView delay={0.05} className="mb-8">
            <Card className="shadow-stripe-lg border-2" style={{ borderColor: 'rgba(26,115,232,0.25)', background: 'linear-gradient(135deg, rgba(26,115,232,0.03) 0%, white 100%)' }}>
              <CardContent className="p-6 flex flex-col sm:flex-row items-start gap-4">
                <div className="w-11 h-11 rounded-2xl flex items-center justify-center shrink-0" style={{ background: 'rgba(26,115,232,0.1)', border: '1px solid rgba(26,115,232,0.2)' }}>
                  <Sparkles size={20} style={{ color: '#1A73E8' }} />
                </div>
                <div className="flex-1">
                  <h2 className="font-bold text-lg mb-1.5" style={{ color: '#0A2540' }}>
                    Получите персональный бизнес-план за 30 минут
                  </h2>
                  <p className="text-muted-foreground text-sm leading-relaxed mb-4">
                    Ниша, площадка, карточка товара и первые шаги к продажам — всё автоматически.
                  </p>
                  <Link
                    href="/checkout?plan=start"
                    className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm text-white transition-all hover:-translate-y-0.5"
                    style={{ background: '#1A73E8', boxShadow: '0 4px 14px rgba(26,115,232,0.3)' }}
                  >
                    Попробовать бесплатно <ArrowRight size={14} />
                  </Link>
                </div>
              </CardContent>
            </Card>
          </BlurFade>

          {/* Lesson sections */}
          {SECTIONS.map((sec, secIdx) => {
            const lessons = LESSONS.filter(l => l.section === sec.key)
            return (
              <section key={sec.key} className="mb-10">
                <BlurFade inView delay={secIdx * 0.04}>
                  <div className="flex items-center gap-2 mb-5">
                    <span className="px-3 py-1 rounded-full text-sm font-semibold" style={{ background: sec.color, color: sec.text }}>
                      {sec.label}
                    </span>
                    <span className="text-xs text-muted-foreground">{lessons.length} урока</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {lessons.map((lesson, idx) => (
                      <BlurFade key={lesson.slug} delay={secIdx * 0.04 + idx * 0.06} inView>
                        <Link href={`/academy/${lesson.slug}`} className="block group h-full" style={{ textDecoration: 'none' }}>
                          <MagicCard className="h-full p-5 cursor-pointer transition-all hover:-translate-y-1 hover:shadow-stripe-lg">
                            <div className="flex items-start justify-between mb-3">
                              <span style={{ fontSize: '1.75rem', lineHeight: 1 }}>{lesson.icon}</span>
                              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                <Clock size={10} /> {lesson.time}
                              </div>
                            </div>
                            <h3 className="font-semibold text-sm leading-snug mb-2" style={{ color: '#0A2540' }}>
                              {lesson.title}
                            </h3>
                            <p className="text-xs text-muted-foreground leading-relaxed mb-4">{lesson.desc}</p>
                            <span className="flex items-center gap-1 text-xs font-semibold opacity-0 group-hover:opacity-100 transition-opacity" style={{ color: '#1A73E8' }}>
                              Читать урок <ArrowRight size={11} />
                            </span>
                          </MagicCard>
                        </Link>
                      </BlurFade>
                    ))}
                  </div>
                </BlurFade>
              </section>
            )
          })}

        </div>
      </div>
    </AppShell>
  )
}
