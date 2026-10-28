'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  ArrowRight, BarChart3, Boxes, ChevronDown, Eye, FileText, GaugeCircle,
  Home, Layers3, LogOut, Megaphone, MessageSquare, Package, Search, Settings,
  ShieldCheck, ShoppingCart, Sparkles, TrendingUp, Upload, Zap,
} from 'lucide-react'
import { PultMark } from '@/components/brand/PultMark'
import { NotificationBell } from '@/components/NotificationBell'
import { api, type User } from '@/lib/api'
import { clearSession } from '@/lib/session'
import styles from './landing.module.css'

const MARKETPLACES = [
  { src: '/logos/wildberries.svg', alt: 'Wildberries', width: 132 },
  { src: '/logos/ozon.svg', alt: 'Ozon', width: 92 },
  { src: '/logos/yandex-market.svg', alt: 'Яндекс Маркет', width: 146 },
]

const CONTOUR_STAGES = [
  { icon: Search, label: 'Спрос', value: '2 843', meta: 'заказа', delta: '+18%' },
  { icon: ShoppingCart, label: 'Продажи', value: '1 247 290 ₽', meta: 'выручка', delta: '+21%' },
  { icon: TrendingUp, label: 'Маржа', value: '23,7%', meta: 'маржинальность', delta: '+2,1 п. п.' },
  { icon: Package, label: 'Остатки', value: '31 день', meta: 'оборачиваемость', delta: '+4 дня' },
]

const CAPABILITIES = [
  { icon: BarChart3, index: '01', title: 'Экономика', text: 'Продажи, комиссии, логистика и реклама сходятся в финансовой картине товара.' },
  { icon: Eye, index: '02', title: 'Рынок', text: 'Изменения спроса, предложения и цены становятся видимыми в одном контексте.' },
  { icon: Megaphone, index: '03', title: 'Реклама', text: 'Расходы рассматриваются вместе с маржой и продажами, а не в отдельном кабинете.' },
  { icon: Boxes, index: '04', title: 'Остатки', text: 'Пульт связывает запас со скоростью продаж и показывает зоны внимания.' },
  { icon: MessageSquare, index: '05', title: 'Отзывы', text: 'Рейтинг, динамика и вопросы покупателей собираются в рабочую очередь.' },
  { icon: FileText, index: '06', title: 'Карточки', text: 'Контент товара получает понятные подсказки на основе доступных сигналов.' },
]

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className={styles.brand}>
      <span className={styles.brandMark}><PultMark approvedReference /></span>
      <span className={styles.brandCopy}>
        <strong>ПУЛЬТ</strong>
        {!compact && <small>ОПЕРАЦИОННАЯ СИСТЕМА СЕЛЛЕРА</small>}
      </span>
    </span>
  )
}

function LandingNav({ user }: { user: User | null }) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (!open) return
    const onPointer = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  function logout() {
    api.auth.logout().catch(() => {}).finally(() => {
      clearSession()
      router.push('/login')
    })
  }

  return (
    <nav className={`${styles.nav} ${scrolled ? styles.navScrolled : ''}`} aria-label="Основная навигация">
      <div className={styles.navInner}>
        <Link href="/" className={styles.logoLink} aria-label="ПУЛЬТ — на главную"><Brand /></Link>
        <div className={styles.navLinks}>
          <a href="#product">Как работает</a>
          <a href="#possibilities">Возможности</a>
          <a href="#access">Доступ</a>
        </div>
        <div className={styles.navActions}>
          {user ? (
            <>
              <NotificationBell dropdownSide="down" />
              <div className={styles.accountWrap} ref={menuRef}>
                <button type="button" className={styles.accountButton} aria-label={`Меню пользователя ${user.name}`}
                  aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
                  <span className={styles.avatar}>{user.name.charAt(0).toUpperCase()}</span>
                  <span className={styles.accountName}>{user.name}</span><ChevronDown size={14} aria-hidden="true" />
                </button>
                {open && (
                  <div className={styles.accountMenu} role="menu">
                    <Link href="/dashboard" role="menuitem" onClick={() => setOpen(false)}><Home size={15} aria-hidden="true" /> Пульт</Link>
                    <Link href="/dashboard/account" role="menuitem" onClick={() => setOpen(false)}><Settings size={15} aria-hidden="true" /> Настройки</Link>
                    <button type="button" role="menuitem" onClick={logout}><LogOut size={15} aria-hidden="true" /> Выйти</button>
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <Link href="/login" className={styles.ghostButton}>Войти</Link>
              <Link href="/register" className={styles.navCta}>Создать аккаунт <ArrowRight size={15} aria-hidden="true" /></Link>
            </>
          )}
        </div>
      </div>
    </nav>
  )
}

function OperationalDemo() {
  return (
    <div className={styles.demoShell} aria-label="Демонстрация операционного контура Пульта">
      <div className={styles.demoPrism} aria-hidden="true" />
      <div className={styles.demoTopbar}>
        <div><span className={styles.demoEyebrow}>ДЕМО-СЦЕНАРИЙ</span><strong>Сегодня в бизнесе</strong></div>
        <span className={styles.liveBadge}><i /> Контур собран</span>
      </div>
      <div className={styles.contourHeader}>
        <span>Операционный контур</span>
        <small>Состояние бизнеса в одной линии</small>
      </div>
      <div className={styles.contour}>
        <div className={styles.contourTrack} aria-hidden="true"><i /></div>
        {CONTOUR_STAGES.map(({ icon: Icon, label, value, meta, delta }) => (
          <article className={styles.stage} key={label}>
            <div className={styles.stageHead}><Icon size={16} aria-hidden="true" /><span>{label}</span></div>
            <strong>{value}</strong>
            <div className={styles.stageMeta}><span>{meta}</span><b>{delta}</b></div>
          </article>
        ))}
      </div>
      <div className={styles.decision}>
        <span className={styles.decisionIcon}><Sparkles size={18} aria-hidden="true" /></span>
        <div className={styles.decisionCopy}><small>ГЛАВНОЕ РЕШЕНИЕ</small><strong>Пополнить остатки крема</strong><span>23 шт. · дефицит через 3 дня</span></div>
        <span className={styles.decisionImpact}><small>ОЦЕНКА ЭФФЕКТА</small><strong>38 400 ₽</strong></span>
        <span className={styles.decisionAction}>Открыть <ArrowRight size={15} aria-hidden="true" /></span>
      </div>
    </div>
  )
}

function SectionTitle({ eyebrow, children, lead }: { eyebrow: string; children: React.ReactNode; lead?: string }) {
  return <header className={styles.sectionTitle}><p>{eyebrow}</p><h2>{children}</h2>{lead && <span>{lead}</span>}</header>
}

function FaqItem({ question, children }: { question: string; children: React.ReactNode }) {
  return <details className={styles.faqItem}><summary>{question}<span aria-hidden="true">+</span></summary><div>{children}</div></details>
}

export default function LandingPage() {
  const [user, setUser] = useState<User | null>(null)
  useEffect(() => {
    const stored = localStorage.getItem('user')
    if (!stored) return
    try { setUser(JSON.parse(stored)) } catch {}
  }, [])
  const startHref = user ? '/dashboard' : '/register'

  return (
    <div className={styles.page}>
      <a className={styles.skipLink} href="#main">Перейти к содержанию</a>
      <LandingNav user={user} />
      <main id="main">
        <section className={styles.hero}>
          <div className={styles.heroAtmosphere} aria-hidden="true"><i /><i /><i /></div>
          <div className={styles.container}>
            <div className={styles.heroGrid}>
              <div className={styles.heroCopy}>
                <p className={styles.kicker}><span>ПУЛЬТ</span> / Операционная система селлера</p>
                <h1>Не смотреть на бизнес.<br /><em>Управлять им.</em></h1>
                <p className={styles.heroLead}>Пульт связывает спрос, продажи, маржу и остатки — чтобы вместо разрозненных отчётов вы видели главное действие на сегодня.</p>
                <div className={styles.heroActions}>
                  <a href="#product" className={styles.primaryButton}>Посмотреть, как работает <ArrowRight size={17} aria-hidden="true" /></a>
                  <Link href={startHref} className={styles.secondaryButton}>{user ? 'Открыть Пульт' : 'Создать аккаунт'}</Link>
                </div>
                <div className={styles.heroNote}><span>01</span><p>Продукт готовится к коммерческому запуску. Демо ниже показывает принцип работы интерфейса.</p></div>
              </div>
              <div className={styles.heroProduct}>
                <div className={styles.productHalo} aria-hidden="true" />
                <OperationalDemo />
                <span className={`${styles.floatTag} ${styles.floatTagTop}`}><Zap size={14} aria-hidden="true" /> Решение, не отчёт</span>
                <span className={`${styles.floatTag} ${styles.floatTagBottom}`}><GaugeCircle size={14} aria-hidden="true" /> Весь контур</span>
              </div>
            </div>
          </div>
        </section>

        <section className={styles.marketStrip} aria-label="Маркетплейсы в продуктовом контуре">
          <div className={styles.marketInner}>
            <span>ЕДИНЫЙ РАБОЧИЙ КОНТУР ДЛЯ</span>
            {MARKETPLACES.map((market) => <img key={market.alt} src={market.src} alt={market.alt} width={market.width} height={30} />)}
          </div>
        </section>

        <section className={styles.productStory} id="product">
          <div className={styles.container}>
            <SectionTitle eyebrow="СМЫСЛ ПУЛЬТА" lead="Система не просит вас собирать картину вручную. Она показывает связь между показателями и точку следующего действия.">Один экран между данными и решением.</SectionTitle>
            <div className={styles.storyGrid}>
              <article className={styles.noisePanel}>
                <p>До Пульта</p><h3>Каждый показатель живёт отдельно.</h3>
                <div className={styles.noiseStack} aria-hidden="true">
                  <span>Продажи <b>1 247 290 ₽</b></span>
                  <span>Реклама <b>ДРР 18,4%</b></span>
                  <span>Склад <b>23 шт.</b></span>
                  <span>Отзывы <b>3,6 ★</b></span>
                </div>
              </article>
              <div className={styles.storyCore} aria-label="Пульт связывает сигналы в единый приоритет">
                <span className={styles.coreMark}><PultMark approvedReference /></span>
                <p>ПУЛЬТ СВЯЗЫВАЕТ</p><strong>Сигналы становятся контекстом</strong>
                <i aria-hidden="true" />
              </div>
              <article className={styles.focusPanel}>
                <p>В Пульте</p><h3>Один приоритет получает контекст.</h3>
                <div className={styles.focusDecision}>
                  <span><Sparkles size={17} aria-hidden="true" /> СЕГОДНЯ</span>
                  <strong>Пополнить остатки</strong>
                  <p>До дефицита 3 дня</p>
                  <b>Оценка эффекта · 38 400 ₽</b>
                </div>
              </article>
            </div>
          </div>
        </section>

        <section className={styles.systemSection}>
          <div className={styles.container}>
            <div className={styles.systemHeader}>
              <span className={styles.systemIndex}>02 / СИСТЕМА</span>
              <h2>Спрос → продажи → маржа → остатки</h2>
              <p>Не четыре отчёта. Один операционный цикл, в котором видно, как решение в одной части бизнеса влияет на остальные.</p>
            </div>
            <div className={styles.systemLine}>
              {CONTOUR_STAGES.map(({ icon: Icon, label }, index) => (
                <article key={label}><span>{String(index + 1).padStart(2, '0')}</span><Icon aria-hidden="true" /><strong>{label}</strong></article>
              ))}
            </div>
          </div>
        </section>

        <section className={styles.capabilities} id="possibilities">
          <div className={styles.container}>
            <SectionTitle eyebrow="РАБОЧЕЕ ПОЛЕ" lead="Каждый модуль отвечает на понятный вопрос селлера и остаётся частью общей картины.">Весь бизнес — в одной логике управления.</SectionTitle>
            <div className={styles.capabilityGrid}>
              {CAPABILITIES.map(({ icon: Icon, index, title, text }) => (
                <article className={styles.capabilityCard} key={title}>
                  <div><span>{index}</span><Icon size={21} aria-hidden="true" /></div>
                  <h3>{title}</h3><p>{text}</p><i aria-hidden="true" />
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className={styles.workflow} id="how">
          <div className={styles.container}>
            <div className={styles.workflowIntro}>
              <span>03 / ПУТЬ К РЕШЕНИЮ</span>
              <h2>Данные входят.<br />Решение выходит.</h2>
              <p>Пульт последовательно проверяет данные, связывает показатели и формирует рабочую очередь. Вы сохраняете контроль над действием.</p>
            </div>
            <ol className={styles.workflowSteps}>
              <li><span>01</span><div><Upload size={20} aria-hidden="true" /><h3>Передать данные</h3><p>Выбрать доступный способ и источник.</p></div></li>
              <li><span>02</span><div><Layers3 size={20} aria-hidden="true" /><h3>Собрать контекст</h3><p>Связать показатели в единую модель.</p></div></li>
              <li><span>03</span><div><Sparkles size={20} aria-hidden="true" /><h3>Увидеть приоритет</h3><p>Получить действие и объяснение его важности.</p></div></li>
              <li><span>04</span><div><ShieldCheck size={20} aria-hidden="true" /><h3>Принять решение</h3><p>Проверить контекст и действовать осознанно.</p></div></li>
            </ol>
          </div>
        </section>

        <section className={styles.access} id="access">
          <div className={styles.container}>
            <div className={styles.accessPanel}>
              <div className={styles.accessGlow} aria-hidden="true" />
              <span className={styles.accessMark}><PultMark approvedReference /></span>
              <p className={styles.accessEyebrow}>РАННИЙ ДОСТУП</p>
              <h2>Пульт готовится к запуску.</h2>
              <p className={styles.accessLead}>Финальные тарифы, условия доступа и правила обработки данных будут закреплены в оферте и публичных документах до коммерческого запуска.</p>
              <Link href={startHref} className={styles.lightButton}>{user ? 'Открыть Пульт' : 'Создать аккаунт'} <ArrowRight size={17} aria-hidden="true" /></Link>
            </div>
          </div>
        </section>

        <section className={styles.faq}>
          <div className={styles.containerNarrow}>
            <SectionTitle eyebrow="КОРОТКО О ГЛАВНОМ">Что важно знать сейчас.</SectionTitle>
            <div className={styles.faqList}>
              <FaqItem question="Что такое Пульт?">Операционная система селлера: единое рабочее пространство для показателей, сигналов и управленческих решений.</FaqItem>
              <FaqItem question="Что Пульт показывает первым?">Приоритетную зону внимания, связанный с ней контекст и следующий шаг. Показанный на лендинге сценарий помечен как демо.</FaqItem>
              <FaqItem question="Это ещё одна система аналитики?">Аналитика остаётся частью продукта, но структура Пульта строится вокруг решений и связей между показателями.</FaqItem>
              <FaqItem question="Где посмотреть условия?">Финальные условия будут опубликованы до коммерческого запуска. Сейчас продукт и документы находятся на этапе подготовки.</FaqItem>
            </div>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <div className={styles.footerBrand}><Brand /><p>Операционная система селлера.</p></div>
          <nav aria-label="Навигация в подвале"><a href="#product">Как работает</a><a href="#possibilities">Возможности</a><a href="#access">Доступ</a><Link href="/support">Контакты</Link></nav>
          <div className={styles.footerBottom}><span>© 2026 ПУЛЬТ</span><span><Link href="/privacy">Политика</Link><Link href="/offer">Оферта</Link></span></div>
        </div>
      </footer>
    </div>
  )
}
