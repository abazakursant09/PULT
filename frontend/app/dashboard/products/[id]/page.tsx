'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  RefreshCw, TrendingUp, Users, BarChart3, Tag, MessageSquare, Sparkles,
  DollarSign, SlidersHorizontal, Zap, Check, BarChart2, FileText, Scale,
  Eye, ExternalLink, Plus, Star, X, Trophy, Copy, Send, Loader2, ArrowLeft,
} from 'lucide-react'
import { api, type Product, type CompetitorReport, type ReviewResponse,
         type PricingRule, type PriceChangeLog, type PriceCheckResult,
         type FinancialSnapshot, type LegalCase } from '@/lib/api'
import { CompetitorReportView } from '@/components/CompetitorReport'
import { ReviewCard } from '@/components/ReviewCard'
import { PriceHistory } from '@/components/PriceHistory'
import { FinanceChart } from '@/components/FinanceChart'
import { LegalCaseCard } from '@/components/LegalCaseCard'
import { ShareSuccessModal } from '@/components/ShareSuccessModal'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

type Tab = 'competitors' | 'reviews' | 'pricing' | 'finance' | 'legal' | 'card'

const VALID_TABS: Tab[] = ['competitors', 'reviews', 'pricing', 'finance', 'legal', 'card']

interface SeoCard {
  seoTitle:        string
  description:     string
  characteristics: Array<{ key: string; value: string }>
  keywords:        string
  infographic:     string[]
}

interface ProductGroup { id: string; name: string; parentId: string; childIds: string[] }

function buildSeoCard(name: string, category: string): SeoCard {
  const cat = category.toLowerCase()
  const isClothing    = /одежд|обувь|текстиль|футболк|платье|джинс|свитер|куртк/.test(cat)
  const isElectronics = /электрон|гаджет|аудио|техник|смартфон|наушник|ноутбук|планшет/.test(cat)
  const base = {
    keywords:    `${name}, купить ${name}, ${name} цена, ${name} с доставкой`,
    infographic: ['Гарантия качества', 'Быстрая доставка', 'Выгодная цена', 'Лёгкий возврат'],
  }
  if (isClothing) return {
    ...base,
    seoTitle:        `${name} — купить с доставкой по России`.slice(0, 100),
    description:     `${name} — стильный выбор для вашего гардероба. Только качественные изделия с тщательным контролем.\n\nВысококачественный состав обеспечивает комфорт и долговечность. Удобный крой подойдёт для повседневного использования.\n\nДоставляем по всей России. Возврат в течение 14 дней.`,
    characteristics: [{ key: 'Материал', value: '95% хлопок, 5% эластан' }, { key: 'Страна', value: 'Россия' }, { key: 'Уход', value: 'Стирка при 30°C' }, { key: 'Сезон', value: 'Всесезонный' }],
  }
  if (isElectronics) return {
    ...base,
    seoTitle:        `${name} — официальная гарантия, быстрая доставка`.slice(0, 100),
    description:     `${name} — современное устройство с передовыми характеристиками. Идеальное решение для дома и офиса.\n\nПроизводительные компоненты гарантируют надёжную работу. В комплекте всё необходимое для немедленного использования.\n\nОфициальная гарантия 12 месяцев.`,
    characteristics: [{ key: 'Гарантия', value: '12 месяцев' }, { key: 'Цвет', value: 'Чёрный / Белый' }, { key: 'Комплектация', value: 'Устройство + кабель + инструкция' }, { key: 'Страна', value: 'Китай' }],
  }
  return {
    ...base,
    seoTitle:        `${name} — выгодная цена, доставка по России`.slice(0, 100),
    description:     `${name} — качественный товар по выгодной цене. Подойдёт как для личного использования, так и в подарок.\n\nМы работаем напрямую с производителями, поэтому предлагаем лучшие условия без переплат.\n\nДоставляем по всей России.`,
    characteristics: [{ key: 'Состояние', value: 'Новое' }, { key: 'Страна', value: 'Россия' }, { key: 'Гарантия', value: '12 месяцев' }, { key: 'Вес', value: 'Уточняется' }],
  }
}

function fmt(n: number) { return n.toLocaleString('ru-RU') }

type AdFormat = 'telegram' | 'vk' | 'reels'
interface AdPost { text: string; caption: string; hashtags: string; script?: string }

function buildAdPost(name: string, description: string, price: number | null, format: AdFormat): AdPost {
  const priceStr  = price ? `всего за ${fmt(price)} ₽` : 'по выгодной цене'
  const shortDesc = description.split('\n')[0].slice(0, 110)
  const tag       = name.replace(/\s+/g, '')
  const hashtags  = `#${tag} #купитьонлайн #маркетплейс #wildberries #скидки #новинка`

  if (format === 'telegram') return {
    text:     `🛍 **${name}**\n\n${shortDesc}\n\n💰 Цена: ${priceStr}\n✅ Быстрая доставка по всей России\n🔄 Возврат без вопросов 14 дней\n\n👉 Ищите «${name}» на Wildberries прямо сейчас!`,
    caption:  `${name} — ${priceStr}. Успейте заказать!`,
    hashtags,
  }
  if (format === 'vk') return {
    text:     `✨ Представляем: ${name}\n\n${shortDesc}\n\n📦 Заказывайте ${priceStr} с доставкой по всей России.\nКачество проверено — гарантия включена!\n\nНайдите нас на маркетплейсе и оформите заказ сегодня 👇`,
    caption:  `${name} — теперь доступен! ${priceStr}.`,
    hashtags: hashtags + ' #шоппинг #покупки',
  }
  // reels
  return {
    text:   `📹 Сценарий рилса (15–30 сек)\n\n⏱ 0–3 с: Крупный план — ${name}. Динамичный переход.\n⏱ 3–8 с: Ключевая особенность товара. Текст на экране: «${shortDesc.slice(0, 55)}…»\n⏱ 8–18 с: Демонстрация использования. Закадровый голос:\n   «${name} — твой выбор на каждый день»\n⏱ 18–25 с: Цена крупным планом: ${priceStr}. Призыв к действию.\n⏱ 25–30 с: Логотип / название магазина + ссылка на маркетплейс.`,
    caption: `${name} ${priceStr} 🔥 Ссылка в описании!`,
    hashtags: hashtags + ' #reels #shorts #тренды',
    script:   `🎙 Голос за кадром:\n«Устали переплачивать? ${name} — это именно то, что вы искали. Высокое качество ${priceStr}. Заказывайте прямо сейчас — доставка по всей России!»`,
  }
}

export default function ProductPage() {
  const router    = useRouter()
  const params    = useParams()
  const productId = params.id as string

  const [product,    setProduct]    = useState<Product | null>(null)
  const [report,     setReport]     = useState<CompetitorReport | null>(null)
  const [reviews,    setReviews]    = useState<ReviewResponse[]>([])
  const [tab,        setTab]        = useState<Tab>('competitors')
  const [loading,    setLoading]    = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error,      setError]      = useState('')

  const [pricingRule,    setPricingRule]    = useState<PricingRule | null>(null)
  const [pricingHistory, setPricingHistory] = useState<PriceChangeLog[]>([])
  const [recommendation, setRecommendation] = useState<PriceCheckResult | null>(null)
  const [pricingLoaded,  setPricingLoaded]  = useState(false)
  const [savingRule,     setSavingRule]     = useState(false)
  const [checking,       setChecking]       = useState(false)
  const [applying,       setApplying]       = useState(false)
  const [pricingError,   setPricingError]   = useState('')
  const [ruleForm, setRuleForm] = useState({
    min_price: '', max_price: '',
    target_position: 'below_top_3', target_percent: '5',
    reaction_threshold: '3', frequency: 'once_per_day', auto_mode: false,
  })

  const [financeSnapshots,  setFinanceSnapshots]  = useState<FinancialSnapshot[]>([])
  const [financeLoaded,     setFinanceLoaded]     = useState(false)
  const [financeLoading,    setFinanceLoading]    = useState(false)
  const [generatingFinance, setGeneratingFinance] = useState(false)
  const [financeError,      setFinanceError]      = useState('')
  const [pdfRequested,      setPdfRequested]      = useState(false)

  const [legalCases,      setLegalCases]      = useState<LegalCase[]>([])
  const [legalLoaded,     setLegalLoaded]     = useState(false)
  const [legalLoading,    setLegalLoading]    = useState(false)
  const [auditingCard,    setAuditingCard]    = useState(false)
  const [analyzingReview, setAnalyzingReview] = useState(false)
  const [legalError,      setLegalError]      = useState('')
  const [reviewTextInput, setReviewTextInput] = useState('')
  const [showLawyerForm,  setShowLawyerForm]  = useState(false)
  const [lawyerFormSent,  setLawyerFormSent]  = useState(false)

  const [cardData,       setCardData]       = useState<SeoCard | null>(null)
  const [userPlan,       setUserPlan]       = useState<string>('master')
  const [showShare,      setShowShare]      = useState(false)
  const [showAddVariant, setShowAddVariant] = useState(false)

  const [showAdForm,  setShowAdForm]  = useState(false)
  const [adFormat,    setAdFormat]    = useState<AdFormat>('telegram')
  const [adPost,      setAdPost]      = useState<AdPost | null>(null)
  const [adCopied,    setAdCopied]    = useState(false)
  const [addingVariant,  setAddingVariant]  = useState(false)
  const [variantName,    setVariantName]    = useState('')
  const [variantSku,     setVariantSku]     = useState('')
  const [variantPrice,   setVariantPrice]   = useState('')

  function switchTab(t: Tab) {
    setTab(t)
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href)
      url.searchParams.set('tab', t)
      window.history.replaceState({}, '', url.toString())
    }
  }

  function isAuthError(msg: string) {
    return (
      msg.includes('401') || msg.includes('403') ||
      msg.includes('Not authenticated') ||
      msg.includes('Недействительный токен') ||
      msg.includes('Пользователь не найден')
    )
  }

  const loadReviews = useCallback(async () => {
    try {
      const [rv, lv] = await Promise.allSettled([
        api.reviews.list(productId),
        api.legal.list(productId),
      ])
      if (rv.status === 'fulfilled') setReviews(rv.value)
      if (lv.status === 'fulfilled') { setLegalCases(lv.value); setLegalLoaded(true) }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : ''
      if (isAuthError(msg)) router.push('/login')
    }
  }, [productId, router])

  async function load() {
    setLoading(true); setError('')
    try {
      const [products, rep] = await Promise.all([
        api.products.list(),
        api.competitors.report(productId),
      ])
      const p = products.find(x => x.id === productId)
      if (!p) throw new Error('Товар не найден')
      setProduct(p); setReport(rep)
      await loadReviews()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка загрузки'
      if (isAuthError(msg)) router.push('/login')
      else setError(msg)
    } finally {
      setLoading(false)
    }
  }

  // Read ?tab= from URL on mount
  useEffect(() => {
    if (typeof window === 'undefined') return
    const sp = new URLSearchParams(window.location.search)
    const t = sp.get('tab')
    if (t && (VALID_TABS as string[]).includes(t)) setTab(t as Tab)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Load card data + user plan from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem(`bp_seo_${productId}`)
      if (raw) setCardData(JSON.parse(raw))
    } catch {}
    try {
      const userRaw = localStorage.getItem('user')
      if (userRaw) setUserPlan(JSON.parse(userRaw).plan ?? 'master')
    } catch {}
  }, [productId])

  useEffect(() => {
    if (!localStorage.getItem('token')) { router.push('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId])

  useEffect(() => {
    function onAssistantTab(e: Event) {
      const { tab: t } = (e as CustomEvent<{ tab: Tab }>).detail
      switchTab(t)
    }
    window.addEventListener('assistant-tab', onAssistantTab)
    return () => window.removeEventListener('assistant-tab', onAssistantTab)
  }, [])

  // Must be declared before early returns to satisfy Rules of Hooks
  const legalCaseByReview = useMemo(() => {
    const m: Record<string, typeof legalCases[0]> = {}
    for (const lc of legalCases) { if (lc.review_id) m[lc.review_id] = lc }
    return m
  }, [legalCases])

  async function handleRefresh() {
    setRefreshing(true)
    try {
      await api.competitors.refresh(productId)
      await new Promise(r => setTimeout(r, 2000))
      setReport(await api.competitors.report(productId))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка обновления'
      if (isAuthError(msg)) router.push('/login')
      else setError(msg)
    } finally { setRefreshing(false) }
  }

  async function handleGenerate() {
    setGenerating(true)
    try {
      await api.reviews.generate(productId)
      await new Promise(r => setTimeout(r, 3000))
      const [rv, lv] = await Promise.allSettled([
        api.reviews.list(productId),
        api.legal.list(productId),
      ])
      if (rv.status === 'fulfilled') setReviews(rv.value)
      if (lv.status === 'fulfilled') setLegalCases(lv.value)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка генерации'
      if (isAuthError(msg)) router.push('/login')
      else setError(msg)
    } finally { setGenerating(false) }
  }

  const loadPricing = useCallback(async () => {
    if (pricingLoaded) return
    try {
      const [rule, history] = await Promise.all([
        api.pricing.getRule(productId).catch(() => null),
        api.pricing.getHistory(productId).catch(() => [] as PriceChangeLog[]),
      ])
      if (rule) {
        setPricingRule(rule)
        setRuleForm({
          min_price:          String(rule.min_price),
          max_price:          String(rule.max_price),
          target_position:    rule.target_position,
          target_percent:     String(rule.target_percent),
          reaction_threshold: String(rule.reaction_threshold),
          frequency:          rule.frequency,
          auto_mode:          rule.auto_mode,
        })
      }
      setPricingHistory(history)
      setPricingLoaded(true)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : ''
      if (isAuthError(msg)) router.push('/login')
    }
  }, [productId, pricingLoaded, router])

  useEffect(() => { if (tab === 'pricing') loadPricing() }, [tab, loadPricing])

  async function handleSaveRule(e: React.FormEvent) {
    e.preventDefault()
    setSavingRule(true); setPricingError('')
    try {
      const saved = await api.pricing.upsertRule(productId, {
        min_price:          parseFloat(ruleForm.min_price),
        max_price:          parseFloat(ruleForm.max_price),
        target_position:    ruleForm.target_position,
        target_percent:     parseFloat(ruleForm.target_percent),
        reaction_threshold: parseFloat(ruleForm.reaction_threshold),
        frequency:          ruleForm.frequency,
        auto_mode:          ruleForm.auto_mode,
      })
      setPricingRule(saved)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка сохранения'
      if (isAuthError(msg)) router.push('/login')
      else setPricingError(msg)
    } finally { setSavingRule(false) }
  }

  async function handleCheck() {
    setChecking(true); setRecommendation(null); setPricingError('')
    try {
      const result = await api.pricing.check(productId)
      setRecommendation(result)
      if (result.auto_applied) {
        const history = await api.pricing.getHistory(productId).catch(() => pricingHistory)
        setPricingHistory(history)
        if (history.length > 0) setProduct(p => p ? { ...p, price: history[0].new_price } : p)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка проверки'
      if (isAuthError(msg)) router.push('/login')
      else setPricingError(msg)
    } finally { setChecking(false) }
  }

  async function handleApply() {
    setApplying(true); setPricingError('')
    try {
      const entry = await api.pricing.apply(productId)
      setPricingHistory(h => [entry, ...h])
      setProduct(p => p ? { ...p, price: entry.new_price } : p)
      setRecommendation(null)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка применения'
      if (isAuthError(msg)) router.push('/login')
      else setPricingError(msg)
    } finally { setApplying(false) }
  }

  const loadFinance = useCallback(async () => {
    if (financeLoaded) return
    setFinanceLoading(true)
    try {
      setFinanceSnapshots(await api.finance.list(productId))
      setFinanceLoaded(true)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : ''
      if (isAuthError(msg)) router.push('/login')
      else setFinanceError(msg)
    } finally { setFinanceLoading(false) }
  }, [productId, financeLoaded, router])

  useEffect(() => { if (tab === 'finance') loadFinance() }, [tab, loadFinance])

  async function handleGenerateFinance() {
    setGeneratingFinance(true); setFinanceError('')
    try {
      const snaps = await api.finance.generate(productId)
      setFinanceSnapshots(snaps)
      setFinanceLoaded(true)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка генерации'
      if (isAuthError(msg)) router.push('/login')
      else setFinanceError(msg)
    } finally { setGeneratingFinance(false) }
  }

  const loadLegal = useCallback(async () => {
    if (legalLoaded) return
    setLegalLoading(true)
    try {
      setLegalCases(await api.legal.list(productId))
      setLegalLoaded(true)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : ''
      if (isAuthError(msg)) router.push('/login')
      else setLegalError(msg)
    } finally { setLegalLoading(false) }
  }, [productId, legalLoaded, router])

  useEffect(() => { if (tab === 'legal') loadLegal() }, [tab, loadLegal])

  async function handleCardAudit() {
    setAuditingCard(true); setLegalError('')
    try {
      const cases = await api.legal.cardAudit(productId)
      setLegalCases(prev => [...cases, ...prev.filter(c => c.case_type !== 'card_audit')])
      setLegalLoaded(true)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка аудита'
      if (isAuthError(msg)) router.push('/login')
      else setLegalError(msg)
    } finally { setAuditingCard(false) }
  }

  async function handleAnalyzeReview() {
    if (!reviewTextInput.trim()) return
    setAnalyzingReview(true); setLegalError('')
    try {
      const newCase = await api.legal.analyzeReview(productId, reviewTextInput)
      setLegalCases(prev => [newCase, ...prev])
      setReviewTextInput('')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка анализа'
      if (isAuthError(msg)) router.push('/login')
      else setLegalError(msg)
    } finally { setAnalyzingReview(false) }
  }

  async function handleUpdateLegalCase(caseId: string, data: { status?: string; user_response?: string }) {
    try {
      const updated = await api.legal.updateCase(productId, caseId, data)
      setLegalCases(cs => cs.map(c => c.id === caseId ? updated : c))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка обновления'
      if (isAuthError(msg)) router.push('/login')
      else setLegalError(msg)
    }
  }

  async function handleAddVariant() {
    if (!variantName.trim() || !product) return
    setAddingVariant(true)
    try {
      await api.products.create({
        name:        `${product.name} — ${variantName.trim()}`,
        marketplace: product.marketplace,
        category:    product.category ?? undefined,
        sku:         variantSku.trim() || undefined,
        price:       variantPrice ? parseFloat(variantPrice) : undefined,
      })
      // Update group in localStorage
      const raw = localStorage.getItem('bp_product_groups')
      const groups: ProductGroup[] = raw ? JSON.parse(raw) : []
      // (child product added; dashboard will reflect on next load)
      localStorage.setItem('bp_product_groups', JSON.stringify(groups))
      setShowAddVariant(false)
      setVariantName(''); setVariantSku(''); setVariantPrice('')
      alert(`Вариант «${variantName.trim()}» создан. Перейдите в «Мои товары», чтобы добавить его в группу.`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка создания'
      if (isAuthError(msg)) router.push('/login')
      else alert(msg)
    } finally { setAddingVariant(false) }
  }

  async function handlePublish(reviewId: string) {
    const updated = await api.reviews.update(productId, reviewId, { status: 'published' })
    setReviews(rs => rs.map(r => r.id === reviewId ? updated : r))
  }
  async function handleSkip(reviewId: string) {
    const updated = await api.reviews.update(productId, reviewId, { status: 'skipped' })
    setReviews(rs => rs.map(r => r.id === reviewId ? updated : r))
  }
  async function handleEdit(reviewId: string, text: string) {
    const updated = await api.reviews.update(productId, reviewId, { response_text: text, status: 'approved' })
    setReviews(rs => rs.map(r => r.id === reviewId ? updated : r))
  }
  async function handlePublishWithText(reviewId: string, text: string) {
    const updated = await api.reviews.update(productId, reviewId, { response_text: text, status: 'published' })
    setReviews(rs => rs.map(r => r.id === reviewId ? updated : r))
    // Auto-resolve linked legal case when response is published
    const linked = legalCases.find(c => c.review_id === reviewId && c.status === 'open')
    if (linked) {
      try {
        const resolved = await api.legal.updateCase(productId, linked.id, { status: 'resolved' })
        setLegalCases(cs => cs.map(c => c.id === linked.id ? resolved : c))
      } catch {}
    }
  }
  async function handleIgnoreLegal(caseId: string) {
    try {
      const updated = await api.legal.updateCase(productId, caseId, { status: 'skipped' })
      setLegalCases(cs => cs.map(c => c.id === caseId ? updated : c))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка'
      if (isAuthError(msg)) router.push('/login')
    }
  }

  /* Loading */
  if (loading) {
    return (
      <>
        <div className="flex-1 flex items-center justify-center" style={{ background: '#09090B' }}>
          <Loader2 size={28} className="animate-spin" style={{ color: '#7C3AED' }} />
        </div>
      </>
    )
  }

  /* Error */
  if (error) {
    return (
      <>
        <div className="flex-1 flex items-center justify-center flex-col gap-5" style={{ background: '#09090B' }}>
          <div className="px-5 py-4 rounded-xl text-sm" style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.22)', color: '#FCA5A5' }}>{error}</div>
          <Button variant="ghost" onClick={() => router.push('/dashboard')}>← Назад к товарам</Button>
        </div>
      </>
    )
  }

  const allCompetitors = report ? [...report.direct, ...report.significant, ...report.minor] : []
  const avgPrice = allCompetitors.length > 0
    ? allCompetitors.reduce((s, c) => s + c.price, 0) / allCompetitors.length
    : 0

  const stats = [
    { icon: <Users    size={16} style={{ color: '#7C3AED' }} />, label: 'Всего конкурентов', value: report?.total_competitors ?? 0 },
    { icon: <BarChart3 size={16} style={{ color: '#7C3AED' }} />, label: 'Прямых',           value: report?.direct.length ?? 0 },
    { icon: <TrendingUp size={16} style={{ color: '#7C3AED' }} />, label: 'Значимых',        value: report?.significant.length ?? 0 },
    { icon: <Tag size={16} style={{ color: '#7C3AED' }} />,       label: 'Средняя цена',     value: avgPrice > 0 ? `${fmt(Math.round(avgPrice))} ₽` : '—' },
  ]

  return (
    <>
    <div className="flex-1" style={{ background: '#09090B' }}>
      <main className="max-w-[1200px] mx-auto px-5 sm:px-8 py-8">

        {/* Back */}
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-[13px] mb-6"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#71717A', padding: 0 }}
          onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
          onMouseLeave={e => { e.currentTarget.style.color = '#71717A' }}
        >
          <ArrowLeft size={14} /> Назад
        </button>

        {/* Product header */}
        <div className="rounded-[10px] p-7 sm:p-10 mb-10 overflow-hidden" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-6">
            <div className="flex-1 min-w-0">
              <h1
                className="font-bold leading-snug mb-2.5"
                style={{ fontSize: 'clamp(1.25rem, 2.5vw, 1.75rem)', fontWeight: 700, color: '#FFFFFF' }}
              >
                {product?.name}
              </h1>
              <div className="flex flex-wrap items-center gap-3" style={{ fontSize: '1rem', color: '#71717A' }}>
                {product?.category && <span>{product.category}</span>}
                {product?.sku && (
                  <span
                    className="mono text-xs px-2 py-0.5 rounded-md"
                    style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.20)', color: '#7C3AED' }}
                  >
                    {product.sku}
                  </span>
                )}
              </div>
            </div>

            <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-3 sm:gap-4 sm:shrink-0">
              {product?.price && (
                <div>
                  <span className="text-xs font-semibold uppercase tracking-wide mb-1" style={{ color: '#6B6B72' }}>Ваша цена</span>
                  <div className="mono text-xl sm:text-2xl font-semibold leading-none" style={{ color: '#FFFFFF' }}>
                    {fmt(product.price)} ₽
                  </div>
                </div>
              )}
              <Button
                variant="ghost"
                onClick={() => setShowAddVariant(v => !v)}
                className="w-full sm:w-auto"
              >
                {showAddVariant ? <><X size={13} /> Отмена</> : <><Plus size={13} /> Добавить вариант</>}
              </Button>
              {tab === 'competitors' && (
                <Button variant="ghost" onClick={handleRefresh} disabled={refreshing} className="w-full sm:w-auto">
                  <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
                  {refreshing ? 'Сбор...' : 'Обновить'}
                </Button>
              )}
              {tab === 'reviews' && (
                <Button onClick={handleGenerate} disabled={generating} className="w-full sm:w-auto">
                  <Sparkles size={13} className={generating ? 'animate-spin' : ''} />
                  {generating ? 'Генерируем...' : 'Сгенерировать отзывы'}
                </Button>
              )}
              {tab === 'finance' && (
                <Button variant="ghost" onClick={handleGenerateFinance} disabled={generatingFinance} className="w-full sm:w-auto">
                  <RefreshCw size={13} className={generatingFinance ? 'animate-spin' : ''} />
                  {generatingFinance ? 'Формируем...' : 'Сформировать отчёт'}
                </Button>
              )}
              {tab === 'legal' && (
                <Button variant="ghost" onClick={handleCardAudit} disabled={auditingCard} className="w-full sm:w-auto">
                  <Scale size={13} className={auditingCard ? 'animate-spin' : ''} />
                  {auditingCard ? 'Анализируем...' : 'Запустить аудит'}
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* Add variant inline form */}
        {showAddVariant && (
          <Card className="p-6 mb-6 animate-slide-up">
            <h3 className="font-semibold text-base mb-4" style={{ color: '#FFFFFF' }}>Новый вариант товара</h3>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
              <div>
                <Label className="mb-2">Вариант *</Label>
                <Input placeholder="Красный / XL"
                  value={variantName} onChange={e => setVariantName(e.target.value)} />
              </div>
              <div>
                <Label className="mb-2">Артикул</Label>
                <Input placeholder="SKU-RED-XL"
                  value={variantSku} onChange={e => setVariantSku(e.target.value)} />
              </div>
              <div>
                <Label className="mb-2">Цена, ₽</Label>
                <Input type="number" placeholder="2 990" min="0"
                  value={variantPrice} onChange={e => setVariantPrice(e.target.value)} />
              </div>
            </div>
            <div className="flex gap-3">
              <Button type="button" variant="ghost" onClick={() => setShowAddVariant(false)}>
                Отмена
              </Button>
              <Button
                type="button"
                onClick={handleAddVariant}
                disabled={!variantName.trim() || addingVariant}
                loading={addingVariant}
              >
                {!addingVariant && <><Plus size={13} /> Добавить вариант</>}
              </Button>
            </div>
          </Card>
        )}

        {/* Stats (competitors tab) */}
        {tab === 'competitors' && report && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mb-10">
            {stats.map((s, i) => (
              <div key={i} className="stat-card">
                <div className="flex items-center gap-2 mb-4">{s.icon}</div>
                <div
                  className="mono font-semibold leading-none mb-1.5 truncate"
                  style={{ fontSize: 'clamp(1.1rem, 2vw, 1.375rem)', color: '#FFFFFF' }}
                >
                  {s.value}
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'rgba(0,0,0,0.38)' }}>{s.label}</div>
              </div>
            ))}
          </div>
        )}

        {/* Tabs */}
        <div className="flex items-center gap-1 mb-10 p-1 rounded-[10px] w-full overflow-x-auto" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
          {([
            { key: 'competitors' as Tab, label: 'Конкуренты', icon: <TrendingUp    size={14} /> },
            { key: 'reviews'     as Tab, label: 'Отзывы',     icon: <MessageSquare size={14} /> },
            { key: 'pricing'     as Tab, label: 'Цена',       icon: <DollarSign    size={14} /> },
            { key: 'finance'     as Tab, label: 'Финансы',    icon: <BarChart2     size={14} /> },
            { key: 'legal'       as Tab, label: 'Юрист',      icon: <Scale         size={14} /> },
            { key: 'card'        as Tab, label: 'Карточка',   icon: <Eye           size={14} /> },
          ]).map(t => (
            <button
              key={t.key}
              onClick={() => switchTab(t.key)}
              className="flex flex-1 items-center justify-center gap-1.5 px-3 py-2 rounded-[8px] font-medium transition-all duration-200 text-sm whitespace-nowrap"
              style={tab === t.key
                ? { background: '#7C3AED', color: 'white', boxShadow: '0 1px 4px rgba(110,106,252,0.30)' }
                : { background: 'transparent', color: '#71717A' }
              }
            >
              {t.icon}
              {t.label}
              {t.key === 'reviews' && reviews.length > 0 && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: tab === t.key ? 'rgba(255,255,255,0.25)' : 'rgba(110,106,252,0.14)', color: tab === t.key ? 'white' : '#7C3AED' }}>
                  {reviews.length}
                </span>
              )}
              {t.key === 'pricing' && pricingRule && <span style={{ fontSize: 10 }}>✓</span>}
              {t.key === 'finance' && financeSnapshots.length > 0 && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: tab === t.key ? 'rgba(255,255,255,0.25)' : 'rgba(110,106,252,0.14)', color: tab === t.key ? 'white' : '#7C3AED' }}>
                  {financeSnapshots.length}
                </span>
              )}
              {t.key === 'legal' && legalCases.filter(c => c.status === 'open' && c.risk_level === 'high').length > 0 && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: tab === t.key ? 'rgba(255,255,255,0.25)' : 'rgba(110,106,252,0.14)', color: tab === t.key ? 'white' : '#7C3AED' }}>
                  {legalCases.filter(c => c.status === 'open' && c.risk_level === 'high').length}
                </span>
              )}
              {t.key === 'card' && cardData && <span style={{ fontSize: 10 }}>✓</span>}
            </button>
          ))}
        </div>

        {/* ── Competitors ── */}
        {tab === 'competitors' && (
          <>
            {report?.total_competitors === 0 ? (
              <div className="text-center py-24 animate-fade-in">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-5"
                     style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.12)' }}>
                  <RefreshCw size={22} style={{ color: 'rgba(110,106,252,0.40)' }} />
                </div>
                <p className="font-semibold text-base mb-2" style={{ color: '#71717A' }}>Данные ещё собираются</p>
                <p className="text-sm" style={{ color: '#71717A' }}>Нажмите «Обновить» через несколько секунд</p>
              </div>
            ) : report ? (
              <CompetitorReportView report={report} />
            ) : null}
            {report && (
              <p className="text-center text-[11px] mt-10 mono" style={{ color: '#71717A' }}>
                Отчёт сформирован: {new Date(report.generated_at).toLocaleString('ru-RU')}
              </p>
            )}
          </>
        )}

        {/* ── Reviews ── */}
        {tab === 'reviews' && (
          <>
            {reviews.length === 0 ? (
              <div className="text-center py-24 animate-fade-in">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-5"
                     style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.12)' }}>
                  <MessageSquare size={22} style={{ color: 'rgba(110,106,252,0.40)' }} />
                </div>
                <p className="font-semibold text-base mb-2" style={{ color: '#71717A' }}>Отзывов пока нет</p>
                <p className="text-sm mb-6" style={{ color: '#71717A' }}>
                  Нажмите «Сгенерировать отзывы», чтобы получить ответы
                </p>
                <Button onClick={handleGenerate} disabled={generating}>
                  <Sparkles size={13} />
                  {generating ? 'Генерируем...' : 'Сгенерировать отзывы'}
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 animate-fade-in">
                {reviews.map(r => (
                  <ReviewCard
                    key={r.id}
                    review={r}
                    legalCase={legalCaseByReview[r.id]}
                    onPublish={handlePublish}
                    onSkip={handleSkip}
                    onEdit={handleEdit}
                    onPublishWithText={handlePublishWithText}
                    onIgnoreLegal={handleIgnoreLegal}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* ── Pricing ── */}
        {tab === 'pricing' && (
          <div className="space-y-6 animate-fade-in">

            {pricingError && (
              <div className="flex items-start justify-between gap-3 px-4 py-3 rounded-xl text-sm"
                   style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626' }}>
                <span>{pricingError}</span>
                <button onClick={() => setPricingError('')} className="shrink-0 opacity-60 hover:opacity-100 text-xs mt-0.5">✕</button>
              </div>
            )}

            <Card className="p-5 sm:p-7">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
                     style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.16)' }}>
                  <SlidersHorizontal size={15} style={{ color: '#7C3AED' }} />
                </div>
                <div>
                  <h2 className="font-semibold text-base leading-none" style={{ color: '#FFFFFF' }}>Правило ценообразования</h2>
                  <p className="text-[11px] mt-0.5" style={{ color: '#71717A' }}>Настройте стратегию управления ценой</p>
                </div>
              </div>

              <form onSubmit={handleSaveRule} className="space-y-5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <Label className="mb-2">Минимальная цена, ₽</Label>
                    <Input type="number" placeholder="1 000" min="0" step="0.01" required
                           value={ruleForm.min_price} onChange={e => setRuleForm(f => ({ ...f, min_price: e.target.value }))} />
                  </div>
                  <div>
                    <Label className="mb-2">Максимальная цена, ₽</Label>
                    <Input type="number" placeholder="10 000" min="0" step="0.01" required
                           value={ruleForm.max_price} onChange={e => setRuleForm(f => ({ ...f, max_price: e.target.value }))} />
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <Label className="mb-2">Целевая позиция</Label>
                    <select className="input" value={ruleForm.target_position}
                            onChange={e => setRuleForm(f => ({ ...f, target_position: e.target.value }))}>
                      <option value="below_top_3">Ниже топ-3 конкурентов</option>
                      <option value="equal_top_1">По лучшей цене рынка</option>
                      <option value="custom">Произвольный %</option>
                    </select>
                  </div>
                  <div>
                    <Label className="mb-2">Отклонение от рынка, %</Label>
                    <Input type="number" placeholder="5" step="0.1" required
                           value={ruleForm.target_percent} onChange={e => setRuleForm(f => ({ ...f, target_percent: e.target.value }))} />
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <Label className="mb-2">Порог реакции, %</Label>
                    <Input type="number" placeholder="3" min="0" step="0.1" required
                           value={ruleForm.reaction_threshold} onChange={e => setRuleForm(f => ({ ...f, reaction_threshold: e.target.value }))} />
                  </div>
                  <div>
                    <Label className="mb-2">Частота проверки</Label>
                    <select className="input" value={ruleForm.frequency}
                            onChange={e => setRuleForm(f => ({ ...f, frequency: e.target.value }))}>
                      <option value="once_per_day">Раз в день</option>
                      <option value="once_per_12h">Раз в 12 часов</option>
                      <option value="manual">Вручную</option>
                    </select>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-4 py-1">
                  <div className="min-w-0">
                    <p className="text-sm font-medium" style={{ color: '#FFFFFF' }}>Авто-режим</p>
                    <p className="text-[11px] mt-0.5" style={{ color: '#71717A' }}>
                      Применять рекомендации автоматически при отклонении выше порога
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setRuleForm(f => ({ ...f, auto_mode: !f.auto_mode }))}
                    className="relative shrink-0 w-10 h-[22px] rounded-full transition-all duration-200"
                    style={{ background: ruleForm.auto_mode ? '#7C3AED' : 'rgba(110,106,252,0.14)' }}
                  >
                    <span className="absolute top-[3px] w-4 h-4 bg-white rounded-full shadow transition-all duration-200"
                          style={{ left: ruleForm.auto_mode ? 'calc(100% - 19px)' : '3px' }} />
                  </button>
                </div>

                <div className="flex justify-end pt-2" style={{ borderTop: '1px solid rgba(110,106,252,0.12)' }}>
                  <Button type="submit" loading={savingRule} className="w-full sm:w-auto">
                    {!savingRule && <><Check size={13} /> Сохранить правило</>}
                  </Button>
                </div>
              </form>
            </Card>

            <Card className="p-5 sm:p-7">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
                       style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.16)' }}>
                    <Zap size={15} style={{ color: '#7C3AED' }} />
                  </div>
                  <div>
                    <h2 className="font-semibold text-base leading-none" style={{ color: '#FFFFFF' }}>Анализ рынка</h2>
                    <p className="text-[11px] mt-0.5" style={{ color: '#71717A' }}>
                      {recommendation ? 'Последняя рекомендация' : 'Проверьте актуальность вашей цены'}
                    </p>
                  </div>
                </div>
                <Button variant="ghost" onClick={handleCheck} disabled={checking || !pricingRule} className="w-full sm:w-auto">
                  <RefreshCw size={13} className={checking ? 'animate-spin' : ''} />
                  {checking ? 'Проверяем...' : 'Проверить рынок'}
                </Button>
              </div>

              {recommendation ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                    {[
                      { label: 'Рыночная цена',    value: `${fmt(Math.round(recommendation.market_price))} ₽`,    color: '#FFFFFF' },
                      { label: 'Рекомендуемая',    value: `${fmt(Math.round(recommendation.recommended_price))} ₽`, color: '#7C3AED' },
                      { label: 'Отклонение',       value: `${recommendation.deviation_percent.toFixed(1)}%`,       color: '#7C3AED' },
                    ].map((s, i) => (
                      <div key={i} className="stat-card col-span-1">
                        <div className="mono text-lg sm:text-[22px] font-semibold leading-none mb-1" style={{ color: s.color }}>{s.value}</div>
                        <div className="text-[11px]" style={{ color: '#71717A' }}>{s.label}</div>
                      </div>
                    ))}
                  </div>
                  <div className="px-4 py-3 rounded-xl"
                       style={{ background: '#18181B', border: '1px solid rgba(110,106,252,0.12)' }}>
                    <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Причина</span>
                    <p className="text-sm mt-1.5 leading-relaxed" style={{ color: '#71717A' }}>{recommendation.reason}</p>
                  </div>
                  {recommendation.auto_applied && (
                    <div className="flex items-center gap-2 px-4 py-3 rounded-xl text-sm"
                         style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.18)', color: '#7C3AED' }}>
                      <Zap size={14} /> Авто-режим: цена обновлена автоматически
                    </div>
                  )}
                  {!recommendation.auto_applied && recommendation.should_change && (
                    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                      <p className="text-sm" style={{ color: '#71717A' }}>Рекомендуем изменить цену</p>
                      <Button onClick={handleApply} loading={applying} className="w-full sm:w-auto">
                        {!applying && <><Zap size={13} /> Применить цену</>}
                      </Button>
                    </div>
                  )}
                  {!recommendation.should_change && !recommendation.auto_applied && (
                    <p className="text-sm text-center py-1" style={{ color: '#71717A' }}>
                      Ваша цена оптимальна — отклонение ниже порога реакции
                    </p>
                  )}
                </div>
              ) : (
                <div className="text-center py-10">
                  <div className="w-12 h-12 rounded-2xl flex items-center justify-center mx-auto mb-4"
                       style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.12)' }}>
                    <DollarSign size={20} style={{ color: 'rgba(110,106,252,0.40)' }} />
                  </div>
                  <p className="text-sm" style={{ color: '#71717A' }}>
                    {pricingRule ? 'Нажмите «Проверить рынок», чтобы получить рекомендацию' : 'Сначала сохраните правило ценообразования'}
                  </p>
                </div>
              )}
            </Card>

            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold" style={{ color: '#FFFFFF' }}>История изменений цены</h2>
                {pricingHistory.length > 0 && (
                  <span className="mono text-[11px]" style={{ color: '#71717A' }}>{pricingHistory.length} записей</span>
                )}
              </div>
              <PriceHistory history={pricingHistory} />
            </div>

          </div>
        )}

        {/* ── Finance ── */}
        {tab === 'finance' && (
          <div className="space-y-6 animate-fade-in">
            {financeLoading && (
              <div className="flex justify-center py-16">
                <Loader2 size={24} className="animate-spin text-muted-foreground" />
              </div>
            )}
            {financeError && (
              <div className="flex items-start justify-between gap-3 px-4 py-3 rounded-xl text-sm"
                   style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626' }}>
                <span>{financeError}</span>
                <button onClick={() => setFinanceError('')} className="shrink-0 opacity-60 hover:opacity-100 text-xs mt-0.5">✕</button>
              </div>
            )}
            {!financeLoading && financeSnapshots.length === 0 && !financeError && (
              <div className="text-center py-20">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-5"
                     style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.12)' }}>
                  <BarChart2 size={22} style={{ color: 'rgba(110,106,252,0.40)' }} />
                </div>
                <p className="font-semibold text-base mb-2" style={{ color: '#71717A' }}>Финансовых данных нет</p>
                <p className="text-sm mb-6" style={{ color: '#71717A' }}>Нажмите «Сформировать отчёт»</p>
                <Button onClick={handleGenerateFinance} disabled={generatingFinance}>
                  <RefreshCw size={13} className={generatingFinance ? 'animate-spin' : ''} />
                  {generatingFinance ? 'Формируем...' : 'Сформировать отчёт'}
                </Button>
              </div>
            )}
            {!financeLoading && financeSnapshots.length > 0 && (() => {
              const latest = financeSnapshots[financeSnapshots.length - 1]
              const totalRev    = financeSnapshots.reduce((s, x) => s + x.revenue,    0)
              const totalProfit = financeSnapshots.reduce((s, x) => s + x.net_profit, 0)
              const avgMargin   = financeSnapshots.reduce((s, x) => s + x.margin_percent, 0) / financeSnapshots.length
              const chartSegments = [
                { label: 'Себестоимость',  value: latest.cogs,                    color: '#4A4760' },
                { label: 'Комиссия МП',    value: latest.marketplace_fee,         color: '#5A56C0' },
                { label: 'Реклама',        value: latest.ad_spend,                color: '#7C3AED' },
                { label: 'Чистая прибыль', value: Math.max(latest.net_profit, 0), color: '#22C55E' },
              ]
              return (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                    {[
                      { label: 'Выручка (3 мес.)', value: `${fmt(Math.round(totalRev))} ₽`,  color: '#FFFFFF' },
                      { label: 'Чистая прибыль',   value: `${totalProfit >= 0 ? '+' : ''}${fmt(Math.round(totalProfit))} ₽`, color: '#7C3AED' },
                      { label: 'Средняя маржа',     value: `${avgMargin.toFixed(1)} %`,       color: '#7C3AED' },
                    ].map((s, i) => (
                      <div key={i} className="stat-card col-span-1">
                        <div className="mono text-lg sm:text-[22px] font-semibold leading-none mb-1 truncate" style={{ color: s.color }}>{s.value}</div>
                        <div className="text-[11px]" style={{ color: '#71717A' }}>{s.label}</div>
                      </div>
                    ))}
                  </div>
                  <Card className="p-5 sm:p-7">
                    <div className="flex items-center gap-3 mb-6">
                      <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
                           style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.16)' }}>
                        <BarChart2 size={15} style={{ color: '#7C3AED' }} />
                      </div>
                      <div>
                        <h2 className="font-semibold text-base leading-none" style={{ color: '#FFFFFF' }}>Структура расходов</h2>
                        <p className="text-[11px] mt-0.5 mono" style={{ color: '#71717A' }}>Последний период · {latest.period}</p>
                      </div>
                    </div>
                    <FinanceChart segments={chartSegments} />
                  </Card>
                  <Card className="overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm min-w-[520px]">
                        <thead>
                          <tr style={{ borderBottom: '1px solid rgba(110,106,252,0.12)' }}>
                            {['Период', 'Выручка', 'Себест.', 'Комиссия', 'Реклама', 'Прибыль', 'Маржа'].map(h => (
                              <th key={h} className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground whitespace-nowrap ${h === 'Период' ? 'text-left' : 'text-right'}`}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {[...financeSnapshots].reverse().map((s, i) => (
                            <tr key={s.id} style={{ borderBottom: i < financeSnapshots.length - 1 ? '1px solid rgba(0,0,0,0.06)' : 'none' }}>
                              <td className="px-4 py-3 mono text-xs" style={{ color: '#71717A' }}>{s.period}</td>
                              <td className="px-4 py-3 text-right mono text-xs" style={{ color: '#FFFFFF' }}>{fmt(Math.round(s.revenue))} ₽</td>
                              <td className="px-4 py-3 text-right mono text-xs" style={{ color: '#71717A' }}>{fmt(Math.round(s.cogs))} ₽</td>
                              <td className="px-4 py-3 text-right mono text-xs" style={{ color: '#71717A' }}>{fmt(Math.round(s.marketplace_fee))} ₽</td>
                              <td className="px-4 py-3 text-right mono text-xs" style={{ color: '#71717A' }}>{fmt(Math.round(s.ad_spend))} ₽</td>
                              <td className="px-4 py-3 text-right mono text-xs font-semibold" style={{ color: s.net_profit >= 0 ? '#7C3AED' : '#8A8986' }}>
                                {s.net_profit >= 0 ? '+' : ''}{fmt(Math.round(s.net_profit))} ₽
                              </td>
                              <td className="px-4 py-3 text-right">
                                <span className="mono text-xs font-bold" style={{ color: '#7C3AED' }}>{s.margin_percent.toFixed(1)} %</span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                  <div className="rounded-2xl px-5 py-4"
                       style={{ background: 'rgba(26,115,232,0.04)', border: '1px solid rgba(110,106,252,0.12)' }}>
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold" style={{ color: '#FFFFFF' }}>Пакет для бухгалтера</p>
                        <p className="text-xs mt-0.5" style={{ color: '#71717A' }}>Выгрузка доходов и расходов в PDF / Excel</p>
                      </div>
                      <Button
                        onClick={() => { setPdfRequested(true); setTimeout(() => setPdfRequested(false), 3000) }}
                        className="shrink-0 w-full sm:w-auto"
                      >
                        <FileText size={13} />
                        {pdfRequested ? 'В разработке' : 'Сформировать пакет'}
                      </Button>
                    </div>
                  </div>

                  {/* Share success */}
                  <div className="rounded-2xl px-5 py-4"
                       style={{ background: 'rgba(26,115,232,0.04)', border: '1px solid rgba(110,106,252,0.18)' }}>
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold" style={{ color: '#FFFFFF' }}>🏆 Поделиться успехом</p>
                        <p className="text-xs mt-0.5" style={{ color: '#71717A' }}>Расскажите о своих результатах в Обзоре рынка</p>
                      </div>
                      <Button
                        variant="ghost"
                        onClick={() => setShowShare(true)}
                        className="shrink-0 w-full sm:w-auto"
                        style={{ borderColor: 'rgba(26,115,232,0.3)', color: '#7C3AED' }}
                      >
                        <Trophy size={13} />
                        Поделиться успехом
                      </Button>
                    </div>
                  </div>

                  {showShare && (() => {
                    const revenue = financeSnapshots.reduce((s, x) => s + x.revenue, 0)
                    const profit  = financeSnapshots.reduce((s, x) => s + x.net_profit, 0)
                    const autoTitle = profit > 0
                      ? `Вышел на ${Math.round(profit / financeSnapshots.length / 1000)}K ₽/мес чистой прибыли`
                      : `Выручка ${Math.round(revenue / 1000)}K ₽ за ${financeSnapshots.length} мес.`
                    return <ShareSuccessModal autoTitle={autoTitle} onClose={() => setShowShare(false)} />
                  })()}
                </>
              )
            })()}
          </div>
        )}

        {/* ── Legal ── */}
        {tab === 'legal' && (
          <div className="space-y-6 animate-fade-in">
            {legalLoading && (
              <div className="flex justify-center py-16">
                <Loader2 size={24} className="animate-spin text-muted-foreground" />
              </div>
            )}
            {legalError && (
              <div className="flex items-start justify-between gap-3 px-4 py-3 rounded-xl text-sm"
                   style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626' }}>
                <span>{legalError}</span>
                <button onClick={() => setLegalError('')} className="shrink-0 opacity-60 hover:opacity-100 text-xs mt-0.5">✕</button>
              </div>
            )}
            <Card className="p-5 sm:p-7">
              <div className="flex items-center gap-3 mb-5">
                <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
                     style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.16)' }}>
                  <MessageSquare size={15} style={{ color: '#7C3AED' }} />
                </div>
                <div>
                  <h2 className="font-semibold text-base leading-none" style={{ color: '#FFFFFF' }}>Анализ отзыва</h2>
                  <p className="text-[11px] mt-0.5" style={{ color: '#71717A' }}>ИИ оценит правовые риски и предложит грамотный ответ</p>
                </div>
              </div>
              <Textarea
                rows={3}
                className="w-full resize-none mb-3"
                placeholder="Вставьте текст отзыва покупателя..."
                value={reviewTextInput}
                onChange={e => setReviewTextInput(e.target.value)}
                style={{ fontFamily: 'inherit', fontSize: '0.875rem' }}
              />
              <Button
                onClick={handleAnalyzeReview}
                disabled={analyzingReview || !reviewTextInput.trim()}
                className="w-full sm:w-auto"
              >
                <Scale size={13} className={analyzingReview ? 'animate-spin' : ''} />
                {analyzingReview ? 'Анализируем...' : 'Проанализировать'}
              </Button>
            </Card>

            {!legalLoading && legalCases.length === 0 && !legalError && (
              <div className="text-center py-20">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-5"
                     style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.12)' }}>
                  <Scale size={22} style={{ color: 'rgba(110,106,252,0.40)' }} />
                </div>
                <p className="font-semibold text-base mb-2" style={{ color: '#71717A' }}>Правовых кейсов нет</p>
                <p className="text-sm" style={{ color: '#71717A' }}>Нажмите «Аудит карточки» для проверки товара</p>
              </div>
            )}

            {!legalLoading && legalCases.length > 0 && (
              <div className="space-y-4">
                {legalCases.map(c => (
                  <LegalCaseCard
                    key={c.id}
                    legalCase={c}
                    onResolve={id => handleUpdateLegalCase(id, { status: 'resolved' })}
                    onEscalate={id => handleUpdateLegalCase(id, { status: 'escalated' })}
                    onSaveResponse={(id, text) => handleUpdateLegalCase(id, { user_response: text })}
                  />
                ))}
              </div>
            )}

            <div className="rounded-2xl px-5 py-4"
                 style={{ background: 'rgba(26,115,232,0.04)', border: '1px solid rgba(110,106,252,0.12)' }}>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold" style={{ color: '#FFFFFF' }}>Нужен живой юрист?</p>
                  <p className="text-xs mt-0.5" style={{ color: '#71717A' }}>Партнёры-юристы, специализирующиеся на маркетплейсах</p>
                </div>
                <Button
                  variant="ghost"
                  onClick={() => { setShowLawyerForm(v => !v); setLawyerFormSent(false) }}
                  className="shrink-0 w-full sm:w-auto"
                >
                  <Scale size={13} />
                  {showLawyerForm ? 'Скрыть' : 'Оставить заявку'}
                </Button>
              </div>
              {showLawyerForm && !lawyerFormSent && (
                <div className="mt-4 space-y-3">
                  <Input type="text" className="w-full" placeholder="Ваше имя" />
                  <Input type="text" className="w-full" placeholder="Telegram / телефон" />
                  <Textarea rows={2} className="w-full resize-none" placeholder="Кратко опишите проблему..."
                            style={{ fontFamily: 'inherit', fontSize: '0.875rem' }} />
                  <Button onClick={() => setLawyerFormSent(true)} className="w-full sm:w-auto">
                    Отправить заявку
                  </Button>
                </div>
              )}
              {showLawyerForm && lawyerFormSent && (
                <div className="mt-4 px-4 py-3 rounded-xl text-sm"
                     style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(26,115,232,0.18)', color: '#7C3AED' }}>
                  Заявка принята. Юрист свяжется с вами в течение рабочего дня.
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Card preview ── */}
        {tab === 'card' && (
          <div className="space-y-6 animate-fade-in">
            {!cardData ? (
              <div className="text-center py-20">
                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-5"
                     style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.12)' }}>
                  <Eye size={22} style={{ color: 'rgba(110,106,252,0.40)' }} />
                </div>
                <p className="font-semibold text-base mb-2" style={{ color: '#71717A' }}>Карточка не сгенерирована</p>
                <p className="text-sm mb-1" style={{ color: '#71717A' }}>
                  Создайте SEO-карточку через форму «Добавить товар»
                </p>
                <p className="text-sm mb-6" style={{ color: '#71717A' }}>или сгенерируйте прямо здесь</p>
                <Button
                  onClick={() => {
                    const card = buildSeoCard(product?.name ?? '', product?.category ?? '')
                    localStorage.setItem(`bp_seo_${productId}`, JSON.stringify(card))
                    setCardData(card)
                  }}
                >
                  <Sparkles size={13} />
                  Сгенерировать карточку
                </Button>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Photo + infographic */}
                  <div className="space-y-4">
                    <div
                      className="rounded-2xl flex items-center justify-center"
                      style={{ height: 280, background: 'linear-gradient(135deg, rgba(110,106,252,0.10) 0%, rgba(26,115,232,0.03) 100%)', border: '1px solid rgba(110,106,252,0.14)' }}
                    >
                      <div className="text-center">
                        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-3"
                             style={{ background: 'rgba(110,106,252,0.12)' }}>
                          <Eye size={22} style={{ color: '#7C3AED' }} />
                        </div>
                        <p className="text-xs font-medium" style={{ color: '#71717A' }}>Фото товара</p>
                        <p className="text-[11px] mt-0.5" style={{ color: 'rgba(255,255,255,0.15)' }}>Добавьте изображения в маркетплейсе</p>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {cardData.infographic.filter(Boolean).map((tag, i) => (
                        <span key={i} className="flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-semibold"
                          style={{ background: 'rgba(110,106,252,0.12)', color: '#7C3AED', border: '1px solid rgba(26,115,232,0.2)' }}>
                          <Check size={10} />
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Card details */}
                  <div className="space-y-5">
                    <div>
                      <h2 className="font-bold leading-snug mb-2" style={{ fontSize: '1.1875rem', color: '#FFFFFF' }}>
                        {cardData.seoTitle}
                      </h2>
                      <div className="flex items-center gap-3">
                        {product?.price && (
                          <span className="mono font-bold text-xl" style={{ color: '#FFFFFF' }}>
                            {fmt(product.price)} ₽
                          </span>
                        )}
                        <div className="flex items-center gap-0.5">
                          {[1,2,3,4,5].map(i => (
                            <Star key={i} size={13} style={{ fill: i <= 4 ? '#F5A623' : 'none', color: '#F5A623' }} />
                          ))}
                          <span className="text-xs ml-1" style={{ color: '#71717A' }}>4.8</span>
                        </div>
                      </div>
                    </div>

                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Описание</p>
                      <div className="text-sm leading-relaxed" style={{ color: '#71717A', whiteSpace: 'pre-line' }}>
                        {cardData.description}
                      </div>
                    </div>

                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Характеристики</p>
                      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid rgba(110,106,252,0.12)' }}>
                        {cardData.characteristics.map((ch, i) => (
                          <div key={i} className="flex"
                            style={{ borderBottom: i < cardData.characteristics.length - 1 ? '1px solid rgba(110,106,252,0.10)' : 'none' }}>
                            <div className="px-4 py-2.5 text-xs font-medium shrink-0"
                                 style={{ color: '#8A8986', width: '40%', background: '#18181B' }}>
                              {ch.key}
                            </div>
                            <div className="px-4 py-2.5 text-xs" style={{ color: '#FFFFFF' }}>
                              {ch.value}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Ключевые слова</p>
                      <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)', lineHeight: 1.7 }}>
                        {cardData.keywords}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex flex-col sm:flex-row gap-3 pt-4"
                     style={{ borderTop: '1px solid rgba(110,106,252,0.12)' }}>
                  <Button
                    onClick={() => alert('Экспорт карточки в разработке')}
                    className="w-full sm:w-auto"
                  >
                    <FileText size={13} />
                    Скачать карточку
                  </Button>
                  <a
                    href={
                      product?.marketplace === 'ozon'
                        ? 'https://seller.ozon.ru/app/barcodes'
                        : product?.marketplace === 'yandex'
                        ? 'https://partner.market.yandex.ru/supplier/barcodes'
                        : 'https://seller.wildberries.ru/supplies-and-orders/orders'
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn btn-ghost w-full sm:w-auto"
                  >
                    <ExternalLink size={13} />
                    Скачать штрихкоды
                  </a>
                  <div className="relative group w-full sm:w-auto">
                    <Button variant="ghost" disabled className="w-full"
                            style={{ opacity: 0.5, cursor: 'not-allowed' }}>
                      <ExternalLink size={13} />
                      Опубликовать на маркетплейс
                    </Button>
                    <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
                         style={{ background: '#1A1A1A', color: '#fff', zIndex: 10 }}>
                      Подключите API в настройках
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      const card = buildSeoCard(product?.name ?? '', product?.category ?? '')
                      localStorage.setItem(`bp_seo_${productId}`, JSON.stringify(card))
                      setCardData(card)
                    }}
                    className="w-full sm:w-auto"
                  >
                    <Sparkles size={13} />
                    Перегенерировать
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => { setShowAdForm(v => !v); setAdPost(null) }}
                    className="w-full sm:w-auto"
                    style={{ borderColor: 'rgba(26,115,232,0.3)', color: '#7C3AED' }}
                  >
                    <Send size={13} />
                    {showAdForm ? 'Скрыть' : 'Рекламный пост'}
                  </Button>
                </div>

                {/* ── Ad content generator ── */}
                {showAdForm && (
                  <div className="rounded-2xl p-6 space-y-5 animate-fade-in"
                       style={{ background: 'rgba(26,115,232,0.03)', border: '1px solid rgba(26,115,232,0.14)' }}>
                    <div className="flex items-center gap-2 mb-1">
                      <Send size={15} style={{ color: '#7C3AED' }} />
                      <h3 className="font-semibold text-sm" style={{ color: '#FFFFFF' }}>
                        Рекламный пост
                      </h3>
                    </div>

                    {/* Format picker */}
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Формат</p>
                      <div className="flex flex-wrap gap-2">
                        {([
                          { id: 'telegram', label: 'Telegram', emoji: '✈️' },
                          { id: 'vk',       label: 'VK',       emoji: '💬' },
                          { id: 'reels',    label: 'Рилс',     emoji: '🎬' },
                        ] as { id: AdFormat; label: string; emoji: string }[]).map(f => (
                          <button
                            key={f.id}
                            type="button"
                            onClick={() => { setAdFormat(f.id); setAdPost(null) }}
                            className="px-4 py-2 rounded-xl text-sm font-medium transition-all duration-150"
                            style={{
                              background:   adFormat === f.id ? '#7C3AED'                     : 'rgba(0,0,0,0.04)',
                              color:        adFormat === f.id ? '#F8F9FA'                      : '#8A8986',
                              border:       `1px solid ${adFormat === f.id ? '#7C3AED' : 'rgba(110,106,252,0.14)'}`,
                            }}
                          >
                            {f.emoji} {f.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    <Button
                      type="button"
                      onClick={() => {
                        const p = buildAdPost(
                          product?.name ?? '',
                          cardData?.description ?? product?.name ?? '',
                          product?.price ?? null,
                          adFormat,
                        )
                        setAdPost(p)
                        setAdCopied(false)
                      }}
                      className="w-full sm:w-auto"
                    >
                      <Sparkles size={13} />
                      Сгенерировать
                    </Button>

                    {/* Preview */}
                    {adPost && (
                      <div className="space-y-4 animate-fade-in">
                        {/* Main text */}
                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{adFormat === 'reels' ? 'Сценарий' : 'Текст поста'}</p>
                            <button
                              type="button"
                              onClick={() => {
                                const full = [adPost.text, '\n\n📸 ' + adPost.caption, '\n\n' + adPost.hashtags, adPost.script ? '\n\n' + adPost.script : ''].join('')
                                navigator.clipboard.writeText(full.trim()).then(() => {
                                  setAdCopied(true)
                                  setTimeout(() => setAdCopied(false), 2000)
                                })
                              }}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-150"
                              style={{
                                background: adCopied ? 'rgba(110,106,252,0.18)' : 'rgba(110,106,252,0.10)',
                                color:      adCopied ? '#60A5FA'               : '#7C3AED',
                                border:     `1px solid ${adCopied ? 'rgba(110,106,252,0.40)' : 'rgba(26,115,232,0.22)'}`,
                              }}
                            >
                              {adCopied ? <><Check size={11} /> Скопировано</> : <><Copy size={11} /> Скопировать текст</>}
                            </button>
                          </div>
                          <pre
                            className="text-sm leading-relaxed rounded-xl p-4 overflow-x-auto"
                            style={{
                              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                              background: '#18181B', border: '1px solid rgba(110,106,252,0.12)',
                              color: '#FFFFFF', fontFamily: 'inherit',
                            }}
                          >
                            {adPost.text}
                          </pre>
                        </div>

                        {/* Caption */}
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Подпись к фото</p>
                          <div className="text-sm px-4 py-3 rounded-xl"
                               style={{ background: '#18181B', border: '1px solid rgba(110,106,252,0.12)', color: '#8A8986' }}>
                            {adPost.caption}
                          </div>
                        </div>

                        {/* Hashtags */}
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Хештеги</p>
                          <div className="flex flex-wrap gap-1.5">
                            {adPost.hashtags.split(' ').filter(Boolean).map((h, i) => (
                              <span key={i} className="px-2.5 py-1 rounded-full text-xs font-medium"
                                style={{ background: 'rgba(110,106,252,0.12)', color: '#7C3AED', border: '1px solid rgba(26,115,232,0.2)' }}>
                                {h}
                              </span>
                            ))}
                          </div>
                        </div>

                        {/* Reels voice script */}
                        {adPost.script && (
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">Голос за кадром</p>
                            <div className="text-sm px-4 py-3 rounded-xl"
                                 style={{ background: '#18181B', border: '1px solid rgba(110,106,252,0.12)', color: '#8A8986', whiteSpace: 'pre-line' }}>
                              {adPost.script}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

      </main>
    </div>
    </>
  )
}
