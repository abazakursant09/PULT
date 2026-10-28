'use client'

import Link from 'next/link'
import { Activity, ArrowLeft, ArrowRight, ChevronDown, Info, Mail } from 'lucide-react'
import { PultMark } from '@/components/brand/PultMark'
import styles from './support.module.css'

const FAQ = [
  {
    question: 'Как добавить кабинет?',
    answer: 'Откройте «Магазины» → «Добавить кабинет». Выберите маркетплейс, название и способ работы: CSV или API. Доступные возможности зависят от маркетплейса.',
    href: '/dashboard/stores', action: 'Открыть «Магазины»',
  },
  {
    question: 'Где загрузить отчёт?',
    answer: 'Откройте «Магазины» и выберите «Загрузить CSV» у нужного магазина. Файл всегда загружается в конкретный магазин.',
    href: '/dashboard/stores', action: 'Перейти к магазинам →',
  },
  {
    question: 'Как изменить тему кабинета?',
    answer: 'В навигации кабинета откройте «Оформление». Можно выбрать светлую, тёмную или системную тему. Выбор сохраняется на этом устройстве.',
    href: '/dashboard', action: 'Открыть кабинет →',
  },
  {
    question: 'Как восстановить доступ?',
    answer: 'На странице входа выберите «Забыли пароль?». Дальнейшие действия и результат запроса будут показаны на странице восстановления.',
    href: '/forgot-password', action: 'Восстановить доступ →',
  },
] as const

export default function SupportPage() {
  return (
    <div className={styles.page} id="top">
      <header className={styles.top}>
        <div className={styles.topInner}>
          <Link className={styles.brand} href="/" aria-label="Пульт — на главную">
            <span className={styles.brandTile}><PultMark approvedReference className={styles.mark} width={36} height={41} /></span>
            <span><span className={styles.brandName}>ПУЛЬТ</span><span className={styles.brandCaption}>ОПЕРАЦИОННАЯ СИСТЕМА СЕЛЛЕРА</span></span>
          </Link>
          <nav className={styles.nav} aria-label="Основная навигация">
            <Link className={styles.home} href="/"><ArrowLeft aria-hidden="true" strokeWidth={1.6} />На главную</Link>
            <Link className={styles.login} href="/login">Войти</Link>
          </nav>
        </div>
      </header>
      <main className={styles.main}>
        <div className={styles.eyebrow}>ПОДДЕРЖКА</div>
        <h1>Помощь по работе<br />{' '}с Пультом</h1>
        <p className={styles.intro}>Найдите ответ о работе с кабинетом.<br />Если вопрос остался — можно написать <a href="#contact">по электронной почте</a>.</p>
        <div className={styles.workspace}>
          <section className={[styles.panel, styles.faq].join(' ')} aria-labelledby="faq-title">
            <h2 className={styles.panelTitle} id="faq-title">Частые вопросы</h2>
            <p className={styles.panelSub}>Коротко о том, что можно сделать в кабинете.</p>
            {FAQ.map((item, index) => (
              <details key={item.question} open={index === 0}>
                <summary>{item.question}<ChevronDown aria-hidden="true" strokeWidth={1.6} /></summary>
                <div className={styles.answer}><p>{item.answer}</p><Link href={item.href}>{item.action}{index === 0 && <ArrowRight aria-hidden="true" strokeWidth={1.6} />}</Link></div>
              </details>
            ))}
          </section>
          <aside className={[styles.panel, styles.contact].join(' ')} id="contact" aria-labelledby="contact-title">
            <div className={styles.mailIcon} aria-hidden="true"><Mail strokeWidth={1.5} /></div>
            <h2 className={styles.panelTitle} id="contact-title">Связаться по почте</h2>
            <p className={styles.contactLead}>Адрес для обращения в поддержку:</p>
            <a className={styles.email} href="mailto:hello@biznes-pult.ru">hello@biznes-pult.ru</a>
            <a className={styles.primary} href="mailto:hello@biznes-pult.ru">Открыть почтовое приложение<ArrowRight aria-hidden="true" strokeWidth={1.7} /></a>
            <p className={styles.mailExplain}>Письмо нужно отправить в вашем почтовом приложении самостоятельно. Эта кнопка только открывает его.</p>
            <div className={styles.delivery} role="note" aria-label="Отправка через сайт недоступна">
              <p className={styles.deliveryTitle}><Info aria-hidden="true" strokeWidth={1.5} />Отправка через сайт недоступна</p>
              <p>Сообщения на этой странице не собираются и не отправляются.</p>
            </div>
          </aside>
        </div>
        <section className={styles.status} aria-label="Сведения о состоянии сервисов">
          <div className={styles.statusIcon} aria-hidden="true"><Activity strokeWidth={1.5} /></div>
          <div><h2>Состояние сервисов</h2><p>На этой странице нет подключения к мониторингу доступности.</p></div>
          <span className={styles.statusLabel}>Статус не проверяется</span>
        </section>
      </main>
      <footer className={styles.footer}><span><strong>Пульт</strong> · Операционная система селлера</span><a href="#top">Наверх ↑</a></footer>
    </div>
  )
}
