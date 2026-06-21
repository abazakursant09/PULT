import { getToken, clearSession } from '@/lib/session'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// ── Timeouts ──────────────────────────────────────────────────────────────────

const TIMEOUT_DEFAULT = 10_000
const TIMEOUT_AI      = 60_000
const TIMEOUT_IMPORT  = 90_000

function _timeout(path: string): number {
  if (path.includes('/ai/') || path.includes('/creative/') || path.includes('/assistant/')) return TIMEOUT_AI
  if (path.includes('/import/')) return TIMEOUT_IMPORT
  return TIMEOUT_DEFAULT
}

// ── Cache (low-volatility read endpoints) ─────────────────────────────────────

const _CACHE_TTL: Record<string, number> = {
  '/api/insights':              30_000,
  '/api/creative/benchmarks':  120_000,
  '/api/rebuild/recommendation': 60_000,
  '/api/import/stats':           60_000,
  '/api/finance/summary':        60_000,
}

const _cache = new Map<string, { data: unknown; ts: number }>()

function _cacheGet<T>(path: string): T | null {
  const ttl = _CACHE_TTL[path]
  if (!ttl) return null
  const entry = _cache.get(path)
  if (entry && Date.now() - entry.ts < ttl) return entry.data as T
  return null
}

function _cachePut(path: string, data: unknown): void {
  if (_CACHE_TTL[path]) _cache.set(path, { data, ts: Date.now() })
}

/** Synchronous cache read — returns stale-or-null without hitting the network. */
export function getSync<T>(path: string): T | null {
  const entry = _cache.get(path)
  return entry ? (entry.data as T) : null
}

// ── In-flight deduplication ───────────────────────────────────────────────────

const _inflight = new Map<string, Promise<unknown>>()

// ── Retry ─────────────────────────────────────────────────────────────────────

const _NO_RETRY = new Set([400, 401, 403, 404, 422])

function _sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms))
}

// ── Headers ───────────────────────────────────────────────────────────────────

function headers(extra?: Record<string, string>) {
  const t = getToken()
  const h: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  }
  if (t) h['Authorization'] = `Bearer ${t}`
  if (extra) Object.assign(h, extra)
  return h
}

// ── Error parsing ─────────────────────────────────────────────────────────────

async function _parseError(res: Response): Promise<string> {
  const text = await res.text()
  let message = `HTTP ${res.status}`
  try {
    const body = JSON.parse(text)
    if (Array.isArray(body.detail)) {
      message = body.detail.map((e: { msg?: string }) => e.msg ?? '').filter(Boolean).join('; ') || message
    } else {
      message = body.detail || body.message || message
    }
  } catch {}
  return message
}

function _handle401(path: string, status: number): void {
  if ((status === 401 || status === 403) && !path.startsWith('/api/auth/')) {
    if (typeof window !== 'undefined') {
      clearSession()
      window.location.href = '/login?reason=session_expired'
    }
  }
}

// ── Core fetch with timeout + retry ──────────────────────────────────────────

async function _fetch<T>(path: string, init: RequestInit | undefined, attempt = 0): Promise<T> {
  const url = `${API}${path}`
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), _timeout(path))

  try {
    const res = await fetch(url, {
      ...init,
      headers: headers(init?.headers as Record<string, string> | undefined),
      mode: 'cors',
      signal: ctrl.signal,
    })
    clearTimeout(timer)

    if (!res.ok) {
      _handle401(path, res.status)
      if (res.status >= 500 && !_NO_RETRY.has(res.status) && attempt < 2) {
        await _sleep(500 * 2 ** attempt)
        return _fetch<T>(path, init, attempt + 1)
      }
      throw new Error(await _parseError(res))
    }

    return res.json() as Promise<T>
  } catch (err) {
    clearTimeout(timer)
    if (err instanceof Error && err.name !== 'AbortError' && !(err.message.startsWith('HTTP ')) && attempt < 2) {
      await _sleep(500 * 2 ** attempt)
      return _fetch<T>(path, init, attempt + 1)
    }
    throw err
  }
}

// ── req: GET cache + dedup, all methods: retry + timeout ─────────────────────

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const isGet = !init?.method || init.method === 'GET'

  if (isGet) {
    const hit = _cacheGet<T>(path)
    if (hit !== null) return hit
    if (_inflight.has(path)) return _inflight.get(path) as Promise<T>
  }

  const promise = _fetch<T>(path, init).then(data => {
    if (isGet) _cachePut(path, data)
    return data
  })

  if (isGet) {
    _inflight.set(path, promise as Promise<unknown>)
    promise.finally(() => _inflight.delete(path))
  }

  return promise
}

// ── reqForm: multipart with timeout + retry ───────────────────────────────────

async function reqForm<T>(path: string, body: FormData, attempt = 0): Promise<T> {
  const url = `${API}${path}`
  const t = getToken()
  const h: Record<string, string> = {}
  if (t) h['Authorization'] = `Bearer ${t}`

  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), _timeout(path))

  try {
    const res = await fetch(url, { method: 'POST', body, headers: h, mode: 'cors', signal: ctrl.signal })
    clearTimeout(timer)

    if (!res.ok) {
      _handle401(path, res.status)
      if (res.status >= 500 && !_NO_RETRY.has(res.status) && attempt < 2) {
        await _sleep(500 * 2 ** attempt)
        return reqForm<T>(path, body, attempt + 1)
      }
      throw new Error(await _parseError(res))
    }

    return res.json() as Promise<T>
  } catch (err) {
    clearTimeout(timer)
    if (err instanceof Error && err.name !== 'AbortError' && !(err.message.startsWith('HTTP ')) && attempt < 2) {
      await _sleep(500 * 2 ** attempt)
      return reqForm<T>(path, body, attempt + 1)
    }
    throw err
  }
}

export interface User {
  id: string
  email: string
  name: string
  plan: 'master' | 'profi' | 'maximum'
  subscription_end_date?: string | null
  chat_violations: number
  chat_blocked: boolean
  is_verified: boolean
  created_at: string
  referral_code?: string
  referred_by_email?: string | null
  referral_discount?: number
  telegram_chat_id?: string | null
}

export interface Payment {
  id: string
  yookassa_payment_id: string
  amount: string
  tariff: string
  plan: string
  status: 'pending' | 'succeeded' | 'canceled'
  created_at: string
}

export interface CreatePaymentResponse {
  confirmation_url: string
  payment_id: string
}

export interface TelegramSettings {
  notify_bad_review:      boolean
  notify_offer_change:    boolean
  notify_price_drop:      boolean
  notify_negative_review: boolean
  notify_trial_end:       boolean
  notify_insights:        boolean
  // Intelligence Loop (Stage 26)
  notify_seo_opportunity:  boolean
  notify_sales_growth:     boolean
  notify_retention:        boolean
  retention_inactive_days: number
  // Rebuild Tracker (Stage 27-29)
  notify_weekly_report:    boolean
  notify_ab_results:       boolean
  // Scheduled reports
  daily_report:           boolean
  daily_report_time:      string
  weekly_summary:         boolean
  weekly_summary_day:     string
  weekly_summary_time:    string
}

export interface SupplierVerificationCreate {
  company_name:   string
  country:        'russia' | 'china'
  inn?:           string | null
  ogrn?:          string | null
  legal_address?: string | null
  phone?:         string | null
  website?:       string | null
  uscc?:          string | null
  business_scope?: string | null
  founded_year?:  number | null
}

export interface SupplierVerification {
  id:                  string
  user_id:             string
  company_name:        string
  country:             string
  inn:                 string | null
  ogrn:                string | null
  legal_address:       string | null
  phone:               string | null
  website:             string | null
  uscc:                string | null
  business_scope:      string | null
  founded_year:        number | null
  status:              'pending' | 'verified' | 'rejected'
  verification_source: string | null
  rejection_reason:    string | null
  verified_at:         string | null
  created_at:          string
}

export interface ReferralInvitee {
  id: string
  email: string
  joined_at: string
  has_paid: boolean
  paid_at: string | null
  is_valid: boolean
  validation_days_left: number
  invalidated: boolean
  invalidation_reason: string | null
}

export interface ReferralStats {
  referral_code: string
  total_invited: number
  total_paid: number
  total_valid: number
  total_pending_validation: number
  discount_percent: number
  referred_by_email: string | null
  milestone: 'yearly' | 'lifetime' | null
  milestone_50_progress: number
  milestone_100_progress: number
}

export interface NotificationItem {
  id: string
  type: 'new_review' | 'offer_change' | 'trial_end' | 'limit_reached' | 'referral_paid'
  title: string
  message: string
  is_read: boolean
  created_at: string
}

export interface NotificationsResponse {
  items: NotificationItem[]
  total: number
  unread_count: number
  page: number
  per_page: number
}

export interface RegisterResponse {
  message: string
  verification_token: string
}

export interface ChatMessage {
  id:         string
  user_id:    string
  user_name:  string
  message:    string
  created_at: string
}

export interface SendMessageResult {
  ok:      boolean
  message: ChatMessage | null
  warning: string | null
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}

export interface MfaRequiredResponse {
  mfa_required: true
  mfa_token: string
}

export type LoginResponse = AuthResponse | MfaRequiredResponse

export interface Product {
  id: string
  user_id: string
  name: string
  marketplace: string
  category: string | null
  sku: string | null
  price: number | null
  created_at: string
}

export interface Competitor {
  id: string
  product_id: string
  competitor_name: string
  competitor_url: string | null
  marketplace: string
  price: number
  rating: number | null
  reviews_count: number | null
  sales_estimate: number | null
  significance: 'direct' | 'significant' | 'minor'
  rank: number | null
  collected_at: string
}

export interface CompetitorReport {
  product_id: string
  total_competitors: number
  direct: Competitor[]
  significant: Competitor[]
  minor: Competitor[]
  generated_at: string
}

export interface ReviewResponse {
  id: string
  product_id: string
  review_text: string
  author: string | null
  rating: number | null
  response_text: string | null
  status: 'pending' | 'approved' | 'published' | 'skipped'
  created_at: string
  updated_at: string
}

export interface PricingRule {
  id:                 string
  product_id:         string
  min_price:          number
  max_price:          number
  target_position:    'below_top_3' | 'equal_top_1' | 'custom'
  target_percent:     number
  reaction_threshold: number
  frequency:          'once_per_day' | 'once_per_12h' | 'manual'
  auto_mode:          boolean
  created_at:         string
  updated_at:         string
}

export interface PriceChangeLog {
  id:         string
  product_id: string
  old_price:  number
  new_price:  number
  reason:     string
  source:     'auto' | 'manual'
  created_at: string
}

export interface PriceCheckResult {
  market_price:      number
  recommended_price: number
  reason:            string
  deviation_percent: number
  should_change:     boolean
  auto_applied:      boolean
}

export interface FinancialSnapshot {
  id:              string
  product_id:      string
  period:          string   // "YYYY-MM"
  revenue:         number
  marketplace_fee: number
  ad_spend:        number
  cogs:            number
  net_profit:      number
  margin_percent:  number
  created_at:      string
}

export interface FinanceSummaryItem {
  product_id:         string
  product_name:       string
  total_revenue:      number
  total_net_profit:   number
  avg_margin_percent: number
  snapshots_count:    number
}

export interface LegalCase {
  id:                string
  product_id:        string
  case_type:         'review_response' | 'card_audit' | 'review'
  status:            'open' | 'resolved' | 'escalated' | 'skipped'
  title:             string
  description:       string
  risk_level:        'high' | 'medium' | 'low'
  ai_recommendation: string
  user_response:     string | null
  review_id?:        string | null
  created_at:        string
  updated_at:        string
}

export interface SuccessStory {
  id:          string
  title:       string
  text:        string
  author_name: string | null
  created_at:  string
}

export interface MonitorEvent {
  id:              string
  title:           string
  description:     string
  source:          'wildberries' | 'ozon' | 'yandex_market' | 'legislation'
  severity:        'critical' | 'important' | 'info'
  affected_module: 'pricing' | 'reviews' | 'legal' | 'general'
  action_required: string
  created_at:      string
}

export interface AssistantResponse {
  message:      string
  module:       string
  action:       string | null
  action_label: string | null
}

export interface InsightImpact {
  label:    string
  estimate: string
  sign:     'negative' | 'positive' | 'neutral'
}

export interface InsightBenchmark {
  metric:    string
  value:     string
  baseline:  string
  deviation: string
}

export interface InsightAction {
  label:  string
  url:    string
  params: Record<string, string> | null
  type:   'primary' | 'secondary'
}

export interface StyleRecommendation {
  style_name:        string
  win_rate:          number
  avg_ctr_uplift:    number
  sample_size:       number
  best_categories:   string[]
  best_marketplaces: string[]
  explanation_lines: string[]
  is_sufficient:     boolean
}

export interface InsightDebug {
  preference_modifier:     number  // net behavioral score delta applied to ranking
  memory_decay:            number  // recency of signals [0–1]; 0 = none, ~1 = all recent
  resurfaced_contextually: boolean // positive preference helped this insight surface
}

export interface InsightItem {
  id:               string
  key:              string
  type:             'warning' | 'positive' | 'info'
  icon:             string
  title:            string
  subtitle:         string | null
  reasons:          string[]
  recommendations:  string[]
  confidence:       number
  confidence_level: 'low' | 'medium' | 'high'
  impact:           InsightImpact | null
  benchmark:        InsightBenchmark | null
  actions:          InsightAction[]
  status:           string
  record_id:        string | null
  product_name:     string | null
  product_sku:      string | null
  marketplace:      string | null
  is_demo:                     boolean
  impact_score:                number | null
  estimated_monthly_loss_rub:  number | null
  style_recommendation:        StyleRecommendation | null
  debug?:                      InsightDebug | null  // dev only; null in production
  // Marketplace operational memory (Part 9)
  automation_level:            'safe_auto' | 'human_required' | 'blocked' | 'delayed' | 'critical_alert' | null
  marketplace_mechanic:        string | null
  marketplace_risk_note:       string | null
  // Historical memory (Part 10) — past patterns, recoveries, rebuild outcomes
  memory_context:              string | null
  // Operational chains — causal relationship to another insight
  is_secondary: boolean        // true = this insight is a consequence, not root cause
  chain_id:     string | null  // OperationalChain.id this insight belongs to
  // Operator learning — silent adaptation note; null = no adaptation applied
  adaptation_note: string | null
  // Trust calibration (Sprint 19)
  weight?:                number | null
  signal_state?:          'temporary' | 'persistent' | 'structural' | null
  resolution_difficulty?: 'easy' | 'moderate' | 'hard' | null
  intervention_tier?:     'monitor' | 'background' | 'attention' | 'immediate' | null
  // Marketplace behavior memory (Sprint 20) — platform mechanics context
  marketplace_patterns:             string[]
  marketplace_behavior_note:        string | null
  marketplace_stabilization_window: number | null
  // Retrospective outcome memory (Sprint 21) — what happened after prior interventions
  outcome_memory_note: string | null
  outcome_state:       string | null  // improved | stabilized | temporary | failed | repeated
  outcome_confidence:  number | null
  // Decision confidence (Sprint 23) — operational certainty across all signals
  decision_confidence_score:  number | null
  decision_confidence_band:   'low' | 'moderate' | 'stable' | 'high' | null
  decision_confidence_reason: string | null
  decision_stability_note:    string | null
  // Signal lifecycle (Sprint 24) — operational phase of this signal
  signal_lifecycle_stage?:  'emerging' | 'confirmed' | 'stabilized' | 'recurring' | 'resolved' | null
  signal_lifecycle_note?:   string | null
  signal_lifecycle_weight?: number | null  // 5 | 15 | 20 | 55 | 85
  signal_operational_age?:  number | null  // days since first seen
  signal_recurrence_count?: number | null
  // Outcome feedback (Sprint 26) — evidence of intervention effectiveness
  outcome_feedback_note?:            string | null
  recommendation_confidence_delta?:  number | null  // +10 | -6 | -12 | 0
  recommended_based_on_history?:     boolean | null
  // Signal age decay (Sprint 27) — temporal freshness of operational evidence
  signal_decay_state?:   'fresh' | 'aging' | 'fading' | 'stale' | 'persistent' | null
  signal_decay_penalty?: number | null   // confidence penalty applied
  signal_decay_note?:    string | null   // shown in expanded section
  signal_age_days?:      number | null
  // Execution sequencing (Sprint 32) — stabilization order
  sequence_stage?:                     number | null
  stabilization_role?:                 string | null
  expected_stabilization_window_days?: number | null
  unlocks_next_stage?:                 boolean | null
  // Operational trajectory (Sprint 33) — pressure direction and reversibility
  trajectory_state?:          string | null  // reversible | stabilizing | persistent | escalating | structurally_accumulating
  trajectory_direction?:      string | null  // improving | stable | worsening | critical
  reversibility_state?:       string | null  // easily_reversible | conditionally_reversible | narrowing_window | structurally_locked
  stabilization_window_days?: number | null  // approx horizon in days; null = high uncertainty
  pressure_accumulation?:     string | null  // dissipating | stable | accumulating | compounding
  trajectory_note?:           string | null
  // Operational tradeoff (Sprint 34) — secondary consequences of intervention
  tradeoff_note?:          string | null  // what temporarily arises after stabilization
  tradeoff_severity?:      string | null  // mild | moderate | significant
  tradeoff_duration_days?: number | null  // approximate secondary-effect duration in days
  reversibility_profile?:  string | null  // reversible | conditionally_reversible | monitor_required
  stabilization_benefit?:  string | null  // primary gain from intervention
  // Operational failure forecast (Sprint 35) — foresight layer
  forecast_escalation_probability?:  number | null  // 0-100
  forecast_fragility_state?:         string | null  // stable | sensitive | fragile | critical
  forecast_next_stage?:              string | null  // probable next operational phase
  forecast_first_failure_mode?:      string | null  // what breaks first if pressure persists
  forecast_note?:                    string | null  // restrained narrative
  forecast_instability_window_days?: number | null  // approximate horizon; null = high uncertainty
  // Operational recovery paths (Sprint 36) — recovery intelligence
  recovery_probability?:          number | null  // 0-100
  recovery_state?:                string | null  // quick | gradual | structural | unstable
  first_recovered_metric?:        string | null  // what normalizes fastest
  lagging_metric?:                string | null  // what stays unstable longest
  expected_recovery_window_days?: number | null  // approximate; null = structural uncertainty
  recovery_note?:                 string | null  // restrained narrative
  recovery_dependency?:           string | null  // precondition for recovery
  // Stabilization lock (Sprint 38) — observation window pacing
  recovery_signal_state?:                string | null  // waiting | stabilizing | reopening | ready
  lock_estimated_recovery_window_days?:  number | null  // days until clean attribution
  lock_reentry_condition?:               string | null  // signal to wait for
  lock_next_safe_action?:                string | null  // first safe action after window
  // Counterfactual pressure (Sprint 39) — inaction cost + timing intelligence
  counterfactual_pressure_state?:              string | null  // stable | narrowing | accelerating | structurally_locked
  counterfactual_transition_window_days?:      number | null  // typical phase-transition horizon
  counterfactual_reversibility_remaining_pct?: number | null  // approximate flexibility remaining
  counterfactual_next_phase?:                  string | null  // likely next instability phase
  counterfactual_operational_time_pressure?:   string | null  // low | moderate | elevated | critical
  counterfactual_note?:                        string | null  // restrained narrative
  // Comparative simulation (Sprint 42) — two-path operational comparison
  path_comparison?: PathComparison | null
  // Observability recovery forecast (Sprint 44)
  obs_recovery_state?:      string | null  // clear | recovering | distorted | fragmented | reset_required
  obs_recovery_window_days?: number | null
  obs_recovery_condition?:  string | null
  obs_blocking_factor?:     string | null
  obs_recovery_note?:       string | null
  // Adaptive intervention timing (Sprint 48) — when to intervene
  timing_state?:                string | null  // observation_phase | stabilization_phase | emerging_window | narrowing_window | immediate | structurally_late | optimal
  intervention_readiness?:      string | null  // ready | nearly_ready | unstable | elevated | late | monitor
  timing_note?:                 string | null
  optimal_window_days?:         number | null  // approximate; always displayed as label, not raw number
  premature_intervention_risk?: string | null  // low | moderate | high
  premature_risk_note?:         string | null
  delayed_intervention_risk?:   string | null  // low | moderate | high | structural
  delayed_risk_note?:           string | null
  waiting_benefit?:             string | null  // shown for observation_phase only
  readiness_condition?:         string | null  // prerequisite for safe intervention
  // Intervention reversal intelligence (Sprint 49) — diminishing returns + rollback economics
  reversal_state?:               string | null  // stable_intervention | diminishing_return | overextended | reversal_window | structurally_locked
  reversal_probability?:         number | null  // 0–100; visibility logic only, never shown as score
  reversal_window_days?:         number | null  // approximate; displayed as label
  reversal_trigger?:             string | null  // what is driving the reversal signal
  reversal_note?:                string | null  // restrained narrative
  rollback_safety?:              string | null  // safe | conditional | risky | blocked
  rollback_effect_expectation?:  string | null
  stabilization_dependency?:     string | null
  // Opportunity cost intelligence (Sprint 45) — economics of delayed decisions
  future_intervention_cost?: string | null  // minimal | moderate | elevated | structural
  reversibility_shift_note?: string | null  // state narrative shown in card footer
  opportunity_cost_note?:    string | null  // broader narrative shown in card body
  dependency_note?:          string | null  // "Вероятно затронет: X" — only if applicable
  // Secondary pressure cascade (Sprint 50) — pressure propagation into adjacent operational zones
  cascade_state?:             string | null  // isolated | shifting_pressure | coupled_instability | structurally_cascading
  cascade_direction?:         string | null  // localized | adjacent | expanding | systemic
  secondary_pressure_target?: string | null  // what operational zone is under secondary pressure
  cascade_probability?:       number | null  // 0–100
  cascade_window_days?:       number | null  // approximate onset horizon; null for isolated
  cascade_note?:              string | null  // restrained narrative
  cascade_offset_note?:       string | null  // timing offset narrative
  // Resilience snapshot (Sprint 51) — point-in-time operational shock absorption capacity
  resilience_state?:          string | null  // adaptive | resilient | moderate | narrowing | brittle | collapsing | exhausted
  absorption_capacity?:       string | null  // high | moderate | narrowing | exhausted
  weakest_operational_layer?: string | null  // most vulnerable operational zone
  resilience_window?:         number | null  // approximate days until state shift
  resilience_score?:          number | null  // 0–100; internal composite
  resilience_note?:           string | null
  // Resilience trajectory (Sprint 52) — how operational elasticity evolves over time
  resilience_trajectory?:            string | null  // recovering | stabilizing | degrading | structurally_degrading
  resilience_trajectory_velocity?:   string | null  // gradual | accelerating (degrading states only)
  resilience_trajectory_note?:       string | null
  absorption_transition_note?:       string | null  // inferred recent absorption capacity movement
  resilience_trajectory_confidence?: number | null  // 0–100
  // Adaptive capacity intelligence (Sprint 53) — direction of operational adaptation over cycles
  adaptive_capacity_state?: string | null  // strengthening | adaptive | plateauing | rigid | deteriorating
  adaptation_direction?:    string | null  // improving | stable | plateauing | constrained | declining
  stabilization_trend?:     string | null
  observability_trend?:     string | null
  recurrence_trend?:        string | null
  adaptation_confidence?:   number | null  // 0–100
  adaptation_cycles?:       number | null
  // Strategic memory drift (Sprint 54) — divergence from historically effective recovery doctrine
  strategic_drift_state?:   string | null  // aligned | drifting | fragmented | historically_disconnected | compounding_repetition
  memory_continuity?:       string | null  // connected | partially_connected | fragmented | disconnected
  doctrine_alignment_note?: string | null
  repetition_pattern_note?: string | null
  drift_note?:              string | null
  drift_confidence?:        number | null  // 0–100
  historical_cycles?:       number | null
}

export interface StyleLeaderboardItem {
  style_name:        string
  win_rate:          number
  avg_ctr_uplift:    number
  sample_size:       number
  total_rebuilds:    number
  best_categories:   string[]
  best_marketplaces: string[]
  winners_count:     number
}

export interface StyleLeaderboardResponse {
  leaderboard:  StyleLeaderboardItem[]
  total_styles: number
  has_data:     boolean
  is_filtered:  boolean
  min_sample:   number
}

export interface StyleDetailResponse {
  style_name:        string
  win_rate:          number
  avg_ctr_uplift:    number
  sample_size:       number
  total_rebuilds:    number
  best_categories:   string[]
  best_marketplaces: string[]
  explanation_lines: string[]
  recent_examples:   {
    product_name: string; category: string; marketplace: string
    delta_ctr: number; winner: boolean; date: string
  }[]
  has_data: boolean
}

export interface OperationalChain {
  id:                      string
  type:                    'degradation' | 'recovery'
  root_insight_key:        string
  consequence_insight_key: string
  root_title:              string
  consequence_title:       string
  chain_text:              string
  evidence:                string[]
  confidence:              number
  confidence_level:        'low' | 'medium' | 'high'
  product_name:            string | null
  marketplace:             string | null
}

export interface OperationalScenario {
  scenario_id:       string
  source_insight:    string
  scenario_type:     string
  path_type:         'conservative' | 'balanced' | 'aggressive'
  assumption:        string
  expected_effect:   string
  tradeoff:          string
  risk_level:        'low' | 'medium' | 'high'
  confidence:        number
  confidence_level:  'low' | 'medium' | 'high'
  time_horizon_days: number
  reversible:        boolean
  causal_chain:      string[]
  evidence_basis:    string
  uncertainty_note:  string
}

export interface PortfolioPattern {
  id:                      string
  pattern_type:            string
  marketplace:             string | null
  category:                string | null
  affected_products:       string[]
  insight_types:           string[]
  operational_summary:     string
  systemic_risk:           string
  confidence:              number
  stabilization_complexity: 'localized' | 'moderate' | 'systemic'
  recommendation_bias:     string | null
  // Sprint 28: root cause hypothesis
  root_cause_hypothesis?:  string | null
  root_cause_note?:        string | null
  root_cause_confidence?:  number | null
  root_cause_band?:        string | null
  // Sprint 28: historical memory
  cross_mp_memory_note?:   string | null
  cross_mp_stability_days?: number | null
}

export interface OperationalSummary {
  summary_type:          'daily' | 'weekly'
  operational_shift:     string
  dominant_pressure:     string | null
  improving_systems:     string[]
  destabilizing_systems: string[]
  recurring_patterns:    string[]
  stabilized_patterns:   string[]
  portfolio_direction:   'stabilizing' | 'unstable' | 'mixed' | 'expanding_pressure'
  operator_load:         'low' | 'moderate' | 'high'
  summary_note:          string
  narrative_lines:       string[]  // pre-built display lines, max 4
  outcome_feedback_line:    string | null  // Sprint 26
  decay_summary_line:       string | null  // Sprint 27
  momentum_summary_line:    string | null  // Sprint 30
  sequencing_summary_line:  string | null  // Sprint 32
  trajectory_summary_line:  string | null  // Sprint 33
}

export interface FocusBlock {
  focus_id:         string
  title:            string
  reason:           string
  root_cause:       string
  expected_impact:  string
  time_sensitivity: 'immediate' | 'this_week' | 'this_month'
  confidence:       number
  is_stable:        boolean
  linked_signals:   string[]
  linked_scenarios: string[]
  linked_chains:    string[]
  primary_action:   string
  secondary_action: string | null
  // Sprint 30: temporal momentum
  focus_momentum:   string | null   // active | slowing | historical | persistent
  effective_weight: number | null
}

export interface StabilizationSequenceItem {
  insight_key:                        string
  sequence_stage:                     number
  sequence_priority:                  number
  stabilization_role:                 string
  expected_stabilization_window_days: number
  unlocks_next_stage:                 boolean
  dependency_reduction:               string[]
  sequencing_confidence:              string
  sequencing_note:                    string
  insight_title:                      string
  insight_product:                    string | null
}

export interface OperationalCapacity {
  capacity_state:              string       // stable | loaded | saturated | overloaded
  operational_bandwidth_score: number       // 0-100
  overload_risk:               string       // low | moderate | high | critical
  defer_categories:            string[]     // categories to temporarily defer
  capacity_note:               string | null
}

export interface ComparativePath {
  action_type:          string
  stabilization_speed:  string   // faster | moderate | slower
  volatility_impact:    string   // lower | moderate | higher
  observability_impact: string   // preserved | reduced | unclear
  operator_load:        string   // lower | moderate | higher
  reversibility_profile: string  // stronger | neutral | weaker
  structural_depth:     string   // tactical | mixed | structural
  path_note:            string
}

export interface PathComparison {
  insight_key:          string
  path_a:               ComparativePath
  path_b:               ComparativePath
  contextual_note:      string
  comparison_dimension: string   // volatility | reversibility | speed | observability | load
}

export interface OperatorStrategyProfile {
  intervention_style:            string   // stable | reactive | aggressive | delayed | oscillating
  pacing_discipline:             string   // strong | moderate | weak
  recovery_patience:             string   // patient | unstable | intervention_prone
  structural_decision_tendency:  string   // balanced | symptom_focused | structurally_avoidant
  operational_volatility_source: string   // market_driven | mixed | operator_driven
  strategic_stability_score:     number   // 0-100
  stability_band:                string   // unstable | elevated | generally_stable | disciplined
  coaching_note:                 string | null
  profile_confidence:            string   // low | moderate | stable | high
}

export interface StrategyShift {
  previous_strategy: string
  current_strategy:  string
  shift_type:        string   // escalation | fragmentation | structural_shift | tactical_switch
  shift_note:        string | null
}

export interface DecisionDrift {
  drift_state:             string  // stable_execution | reactive_switching | fragmented_recovery | oscillating_pressure | stabilization_breakdown
  drift_note:              string  // restrained narrative
  intervention_overlap:    string  // none | low | moderate | high
  sequencing_continuity:   string  // stable | partial | fragmented | broken
  observation_reset_count: number  // signals currently in reset/reopening state
}

export interface StrategyCommitment {
  strategy_type:                    string   // structural_margin_recovery | advertising_stabilization | seo_recovery | volatility_reduction | growth_scaling | inventory_stabilization | mixed_fragmented_strategy
  commitment_state:                 string   // emerging | active | stabilizing | fragmented | abandoned
  interruption_risk:                string   // low | moderate | high
  observability_quality:            string   // clear | sufficient | degraded | unclear
  commitment_score:                 number | null
  commitment_note:                  string | null
  estimated_observation_window_days: number | null
  strategy_shift:                   StrategyShift | null
}

export interface StabilityTopology {
  topology_state:           string  // balanced_stability | compensating_structure | narrowing_support | fragmented_stability | structurally_unbalanced | collapsing_compensation
  dominant_stability_layer: string
  weakest_stability_layer:  string
  compensation_behavior:    string
  structural_balance:       string
  remaining_flexibility:    string
  topology_note:            string
  topology_confidence:      number
}

export interface OperationalPhaseTransition {
  phase:                string  // adaptive_equilibrium | stabilization_cycle | defensive_convergence | structural_pressure_formation | resilience_fragmentation | constrained_operation | recovery_reentry
  transition_direction: string  // stabilizing | restrictive | deteriorating | recovering
  transition_velocity:  string  // stable | gradual | accelerating
  transition_stability: string  // stable | moderate | unstable | fragmented
  transition_driver:    string
  phase_note:           string
  phase_confidence:     number
}

export interface DecisionEnergy {
  energy_state:         string  // lightweight | manageable | draining | disruptive | structurally_exhausting
  coordination_load:    string  // minimal | moderate | elevated | high | structurally_distorted
  observability_load:   string  // isolated | localized | degraded | fragmented | structurally_distorted
  stabilization_burden: string  // absorbable | sustained | cumulative | expanding | structurally_depleting
  execution_complexity: string  // contained | multi-step | cross-functional | systemic | structurally_coupled
  energy_note:          string
  energy_confidence:    number
}

export interface OperationalRegime {
  regime:                 string  // expansion | stabilization | defensive | constrained | containment | recovery_transition
  regime_direction:       string  // stabilizing | deteriorating | recovering | structurally_accumulating | constrained
  operational_posture:    string  // expansion_tolerant | equilibrium_focused | preservation_oriented | flexibility_constrained | deterioration_containment | recovery_rebuilding
  resilience_context:     string
  intervention_tolerance: string  // high | moderate | selective | narrow | minimal
  observability_quality:  string  // strong | moderate | degraded | fragmented
  regime_note:            string
  regime_confidence:      number
}

export interface OperationalDoctrine {
  doctrine_state:             string  // adaptive_execution | recurring_operational_bias | defensive_patterning | stabilization_dependency | structurally_embedded_doctrine | rigid_operational_doctrine
  doctrine_pattern:           string
  adaptation_mode:            string
  institutionalization_level: string
  doctrine_flexibility:       string
  doctrine_note:              string
  doctrine_confidence:        number
}

export interface InstitutionalInertia {
  inertia_state:            string  // flexible_structure | adaptive_inertia | operational_hardening | structural_inertia | locked_operational_behavior | institutional_freeze
  adaptation_resistance:    string
  behavioral_repeatability: string
  structural_elasticity:    string
  recovery_mobility:        string
  inertia_driver:           string
  inertia_window_days:      number | null
  inertia_note:             string
  inertia_confidence:       number
}

export interface StructuralRecoveryCapacity {
  recovery_state:                 string  // structurally_recoverable | recoverable_with_adaptation | constrained_recovery | restructuring_dependent | continuity_without_recovery | structurally_exhausted
  structural_recoverability:      string
  recovery_elasticity:            string
  restructuring_requirement:      string
  continuity_dependence:          string
  structural_recovery_horizon:    string
  recovery_window_days:           number | null
  structural_reversibility_index: number
  recovery_capacity_note:         string
  recovery_capacity_confidence:   number
}

export interface InsightsResponse {
  insights:               InsightItem[]
  focused_insights:       InsightItem[]
  operational_chains:     OperationalChain[]
  operational_scenarios:  OperationalScenario[]
  operational_focus:      FocusBlock | null
  portfolio_patterns:     PortfolioPattern[]
  operational_summary:    OperationalSummary | null  // Sprint 25
  stabilization_sequence: StabilizationSequenceItem[]  // Sprint 32
  operational_capacity:       OperationalCapacity | null          // Sprint 37
  operator_strategy_profile: OperatorStrategyProfile | null       // Sprint 40
  strategy_commitment:        StrategyCommitment | null           // Sprint 43
  decision_drift:             DecisionDrift | null                // Sprint 47
  operational_regime:         OperationalRegime | null            // Sprint 55
  decision_energy:            DecisionEnergy | null               // Sprint 56
  operational_phase_transition: OperationalPhaseTransition | null  // Sprint 57
  stability_topology:           StabilityTopology | null            // Sprint 58
  operational_doctrine:         OperationalDoctrine | null          // Sprint 59
  institutional_inertia:        InstitutionalInertia | null         // Sprint 60
  structural_recovery_capacity: StructuralRecoveryCapacity | null   // Sprint 61
  fatigue_score:              number | null
  stability_credit:       number | null
  is_demo:                boolean
  total_active:           number
  has_data:               boolean
  total_warnings:         number
  total_positive:         number
  estimated_monthly_loss: number
}

export interface RebuildRecommendation {
  has_data:        boolean
  is_demo:         boolean
  best_style_name: string | null
  avg_ctr_delta:   number | null
  total_rebuilds:  number
  rebuild_count:   number
  confidence:      'low' | 'medium' | 'high' | null
  winners_count:   number
  message:         string | null
}

export interface TrackRebuildPayload {
  product_name:      string
  marketplace?:      string
  category?:         string
  preset?:           string
  typography_preset?: string
  rebuild_reason?:   string
  expected_gain_rub?: number | null
  insight_key?:      string | null
  impact_score?:     number | null
}

// ── Marketplace Execution Layer (ME-6 / ME-6.1) ──────────────────────────────
export interface InsightExecuteResult {
  success:              boolean
  status:               'success' | 'dry_run_ok' | 'rejected' | 'failed' | 'needs_input' | 'partial'
  action_type:          string | null
  execution_id:         string | null
  message:              string
  automation_eligible:  boolean
  needs_input:          string[]
  descriptor:           {
    reason?:            string
    action?:            string
    what_will_happen?:  string
    expected_effect?:   string
  }
  results:              Array<{ review_id: string; status: string; execution_id: string | null; error: unknown }>
}

export interface ExecutionLogItem {
  id:          string
  action_type: string
  marketplace: string | null
  mode:        string
  status:      'pending' | 'success' | 'failed' | 'rejected' | 'reverted'
  insight_key: string | null
  error_code:  string | null
  created_at:  string
  finished_at: string | null
}

export interface ExecutionLogDetail extends ExecutionLogItem {
  user_id:         string
  connection_id:   string | null
  payload:         Record<string, unknown>
  api_request_id:  string | null
  result:          Record<string, unknown> | null
  reverted_from:   string | null
  idempotency_key: string | null
}

export const api = {
  auth: {
    login: (email: string, password: string) =>
      req<LoginResponse>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),
    register: (email: string, name: string, password: string, ref_code?: string) =>
      req<RegisterResponse>('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, name, password, ...(ref_code ? { ref_code } : {}) }),
      }),
    verifyEmail: (token: string) =>
      req<AuthResponse>(`/api/auth/verify-email?token=${encodeURIComponent(token)}`),
    forgotPassword: (email: string) =>
      req<{ message: string; reset_token: string | null }>('/api/auth/forgot-password', {
        method: 'POST',
        body: JSON.stringify({ email }),
      }),
    resetPassword: (token: string, password: string) =>
      req<{ message: string }>('/api/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({ token, password }),
      }),
    oauthLogin: (data: { provider: string; provider_user_id: string; email?: string; name?: string }) =>
      req<AuthResponse>('/api/auth/oauth/login', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  products: {
    list: () => req<Product[]>('/api/products'),
    create: (data: { name: string; marketplace: string; category?: string; sku?: string; price?: number }) =>
      req<Product>('/api/products', { method: 'POST', body: JSON.stringify(data) }),
    delete: (id: string) => req<{ message: string }>(`/api/products/${id}`, { method: 'DELETE' }),
  },

  competitors: {
    list: (productId: string) => req<Competitor[]>(`/api/products/${productId}/competitors`),
    refresh: (productId: string) =>
      req<{ message: string }>(`/api/products/${productId}/competitors/refresh`, { method: 'POST' }),
    report: (productId: string) => req<CompetitorReport>(`/api/products/${productId}/report`),
  },

  reviews: {
    list: (productId: string) =>
      req<ReviewResponse[]>(`/api/reviews/${productId}`),
    generate: (productId: string) =>
      req<{ message: string }>(`/api/reviews/${productId}/generate`, { method: 'POST' }),
    update: (productId: string, reviewId: string, data: { response_text?: string; status?: string }) =>
      req<ReviewResponse>(`/api/reviews/${productId}/${reviewId}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
  },

  pricing: {
    getRule: (productId: string) =>
      req<PricingRule>(`/api/products/${productId}/pricing-rule`),
    upsertRule: (productId: string, data: {
      min_price: number; max_price: number; target_position: string;
      target_percent: number; reaction_threshold: number; frequency: string; auto_mode: boolean
    }) =>
      req<PricingRule>(`/api/products/${productId}/pricing-rule`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    getHistory: (productId: string) =>
      req<PriceChangeLog[]>(`/api/products/${productId}/price-history`),
    check: (productId: string) =>
      req<PriceCheckResult>(`/api/products/${productId}/price-check`, { method: 'POST' }),
    apply: (productId: string) =>
      req<PriceChangeLog>(`/api/products/${productId}/price-apply`, { method: 'POST' }),
  },

  finance: {
    list:     (productId: string) =>
      req<FinancialSnapshot[]>(`/api/products/${productId}/finance`),
    generate: (productId: string) =>
      req<FinancialSnapshot[]>(`/api/products/${productId}/finance/generate`, { method: 'POST' }),
    summary:  () =>
      req<FinanceSummaryItem[]>('/api/finance/summary'),
  },

  monitor: {
    list:  () => req<MonitorEvent[]>('/api/monitor/events'),
    get:   (id: string) => req<MonitorEvent>(`/api/monitor/events/${id}`),
    check: () => req<MonitorEvent[]>('/api/monitor/check', { method: 'POST' }),
  },

  legal: {
    list: (productId: string) =>
      req<LegalCase[]>(`/api/products/${productId}/legal/cases`),
    cardAudit: (productId: string) =>
      req<LegalCase[]>(`/api/products/${productId}/legal/card-audit`, { method: 'POST' }),
    analyzeReview: (productId: string, review_text: string, review_id?: string) =>
      req<LegalCase>(`/api/products/${productId}/legal/analyze-review`, {
        method: 'POST',
        body: JSON.stringify({ review_text, ...(review_id ? { review_id } : {}) }),
      }),
    updateCase: (productId: string, caseId: string, data: { status?: string; user_response?: string }) =>
      req<LegalCase>(`/api/products/${productId}/legal/cases/${caseId}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
  },

  successStories: {
    list: () =>
      req<SuccessStory[]>('/api/success-stories'),
    create: (data: { title: string; text: string; author_name?: string }) =>
      req<SuccessStory>('/api/success-stories', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  assistant: {
    ask: (question: string, product_id?: string | null) =>
      req<AssistantResponse>('/api/assistant/ask', {
        method: 'POST',
        body: JSON.stringify({ question, product_id: product_id ?? null }),
      }),
  },

  chat: {
    messages: () =>
      req<ChatMessage[]>('/api/chat/messages'),
    send: (message: string) =>
      req<SendMessageResult>('/api/chat/messages', {
        method: 'POST',
        body: JSON.stringify({ message }),
      }),
  },

  referrals: {
    stats: () =>
      req<ReferralStats>('/api/referrals/me'),
    invitees: () =>
      req<ReferralInvitee[]>('/api/referrals/invitees'),
    markPaid: (inviteeId: string) =>
      req<{ ok: boolean }>(`/api/referrals/mark-paid/${inviteeId}`, { method: 'POST' }),
  },

  account: {
    delete: () =>
      req<{ ok: boolean; message: string }>('/api/account', { method: 'DELETE' }),
  },

  notifications: {
    list: (page = 1, perPage = 20) =>
      req<NotificationsResponse>(`/api/notifications?page=${page}&per_page=${perPage}`),
    unreadCount: () =>
      req<{ count: number }>('/api/notifications/unread-count'),
    markRead: (id: string) =>
      req<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: 'POST' }),
    markAllRead: () =>
      req<{ ok: boolean }>('/api/notifications/read-all', { method: 'POST' }),
    seed: () =>
      req<{ ok: boolean; seeded: boolean }>('/api/notifications/seed', { method: 'POST' }),
  },

  mfa: {
    status: () =>
      req<{ enabled: boolean }>('/api/auth/mfa/status'),
    setup: () =>
      req<{ secret: string; otpauth: string }>('/api/auth/mfa/setup', { method: 'POST' }),
    verify: (code: string) =>
      req<{ message: string }>('/api/auth/mfa/verify', {
        method: 'POST',
        body: JSON.stringify({ code }),
      }),
    disable: (code: string) =>
      req<{ message: string }>('/api/auth/mfa/disable', {
        method: 'DELETE',
        body: JSON.stringify({ code }),
      }),
    loginMfa: (mfa_token: string, code: string) =>
      req<AuthResponse>('/api/auth/login/mfa', {
        method: 'POST',
        body: JSON.stringify({ mfa_token, code }),
      }),
  },

  telegram: {
    getChatId: () =>
      req<{ telegram_chat_id: string | null }>('/api/profile/telegram'),
    updateChatId: (telegram_chat_id: string | null) =>
      req<{ ok: boolean; telegram_chat_id: string | null }>('/api/profile/telegram', {
        method: 'PUT',
        body: JSON.stringify({ telegram_chat_id }),
      }),
    test: () =>
      req<{ ok: boolean }>('/api/telegram/test', { method: 'POST' }),
    getSettings: () =>
      req<TelegramSettings>('/api/telegram/settings'),
    updateSettings: (data: Partial<TelegramSettings>) =>
      req<TelegramSettings>('/api/telegram/settings', {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    triggerInsights: () =>
      req<{ ok: boolean; notifications_sent: number }>('/api/telegram/trigger-insights', { method: 'POST' }),
  },

  suppliers: {
    submit: (data: SupplierVerificationCreate) =>
      req<SupplierVerification>('/api/suppliers/verify', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    my: () =>
      req<SupplierVerification[]>('/api/suppliers/verify/my'),
    get: (id: string) =>
      req<SupplierVerification>(`/api/suppliers/verify/${id}`),
    revoke: (id: string) =>
      req<{ ok: boolean }>(`/api/suppliers/verify/${id}`, { method: 'DELETE' }),
    listVerified: () =>
      req<SupplierVerification[]>('/api/suppliers/verified'),
  },

  catalog: {
    listSuppliers: (params?: {
      industry?: string; region?: string; country?: string;
      verified?: boolean; search?: string; sort?: string;
    }) => {
      const q = new URLSearchParams()
      if (params?.industry)  q.set('industry',  params.industry)
      if (params?.region)    q.set('region',    params.region)
      if (params?.country)   q.set('country',   params.country)
      if (params?.verified !== undefined) q.set('verified', String(params.verified))
      if (params?.search)    q.set('search',    params.search)
      if (params?.sort)      q.set('sort',      params.sort)
      return req<SupplierEntry[]>(`/api/catalog/suppliers?${q}`)
    },
    getSupplier: (id: string) => req<SupplierEntry>(`/api/catalog/suppliers/${id}`),
  },

  logistics: {
    listCompanies: (params?: { region?: string; delivery_type?: string }) => {
      const q = new URLSearchParams()
      if (params?.region)        q.set('region',        params.region)
      if (params?.delivery_type) q.set('delivery_type', params.delivery_type)
      return req<TransportCompanyEntry[]>(`/api/logistics/companies?${q}`)
    },
    compare: (data: {
      weight_kg: number; volume_m3: number; from_city: string; to_city: string; delivery_type?: string;
    }) => req<DeliveryResult[]>('/api/logistics/compare', { method: 'POST', body: JSON.stringify(data) }),
  },

  deals: {
    create: (data: {
      supplier_id: string; product_name: string; specification?: string;
      price_per_unit: number; quantity: number; deadline?: string;
    }) => req<DealEntry>('/api/deals', { method: 'POST', body: JSON.stringify(data) }),
    my: () => req<DealEntry[]>('/api/deals/my'),
    get: (id: string) => req<DealEntry>(`/api/deals/${id}`),
    updateStatus: (id: string, status: string) =>
      req<DealEntry>(`/api/deals/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    sign: (id: string) =>
      req<DealEntry>(`/api/deals/${id}/sign`, { method: 'POST' }),
  },

  supplierReviews: {
    create: (data: { target_type: string; target_id: string; deal_id?: string; rating: number; text?: string }) =>
      req<SupplierReviewEntry>('/api/supplier-reviews', { method: 'POST', body: JSON.stringify(data) }),
    list: (target_type: string, target_id: string, limit = 20, offset = 0) =>
      req<SupplierReviewEntry[]>(`/api/supplier-reviews/${target_type}/${target_id}?limit=${limit}&offset=${offset}`),
  },

  promo: {
    validate: (code: string, plan: string) =>
      req<PromoValidateResult>('/api/promo/validate', {
        method: 'POST', body: JSON.stringify({ code, plan }),
      }),
    apply: (code: string, plan: string) =>
      req<PromoValidateResult & { ok: boolean }>('/api/promo/apply', {
        method: 'POST', body: JSON.stringify({ code, plan }),
      }),
    adminList: () => req<PromoEntry[]>('/api/admin/promo'),
    adminCreate: (data: {
      code: string; type: string; value: number; description?: string;
      applicable_plans?: string; max_activations?: number;
      blogger_name?: string; expires_at?: string;
    }) => req<PromoEntry>('/api/admin/promo', { method: 'POST', body: JSON.stringify(data) }),
    adminToggle: (id: string) => req<PromoEntry>(`/api/admin/promo/${id}/toggle`, { method: 'PATCH' }),
    adminStats: () => req<PromoStats>('/api/admin/promo/stats'),
  },

  seoCards: {
    generate: (data: { preset: string; category: string; product_name: string }) =>
      req<{ task_id: string }>('/api/ai/generate-background', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    taskStatus: (task_id: string) =>
      req<TaskStatusFull>(`/api/ai/task/${task_id}`),
    suggestText: (product_name: string, category: string) =>
      req<SuggestTextResponse>('/api/ai/suggest-text', {
        method: 'POST',
        body: JSON.stringify({ product_name, category }),
      }),
    retrySingle: (data: { preset: string; category: string; product_name: string; slide_idx: number }) =>
      req<{ background_url: string }>('/api/ai/generate-single', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    saveProject: (data: {
      name?: string; product_name: string; marketplace?: string; preset?: string;
      category?: string; typography_preset?: string; current_price?: string; old_price?: string;
      advantages?: string[]; template_set?: string; image_urls?: string[];
    }) => req<SeoProjectItem>('/api/seo-projects', { method: 'POST', body: JSON.stringify(data) }),
    listProjects: () => req<SeoProjectItem[]>('/api/seo-projects'),
    deleteProject: (id: string) => req<{ ok: boolean }>(`/api/seo-projects/${id}`, { method: 'DELETE' }),
    duplicateProject: (id: string) =>
      req<SeoProjectItem>(`/api/seo-projects/${id}/duplicate`, { method: 'POST' }),
  },

  payments: {
    create: (tariff: 'basic' | 'pro') =>
      req<CreatePaymentResponse>('/api/payments/create', {
        method: 'POST',
        body: JSON.stringify({ tariff }),
      }),
    status: (payment_id: string) =>
      req<{ payment_id: string; status: string; tariff: string; amount: string }>(
        `/api/payments/status/${payment_id}`
      ),
    history: () => req<Payment[]>('/api/payments/history'),
  },

  actionEngine: {
    getInsights: () => req<InsightsResponse>('/api/insights'),
    updateStatus: (insightKey: string, status: string) =>
      req<{ ok: boolean; record_id: string; new_status: string }>(
        `/api/insights/${encodeURIComponent(insightKey)}/status`,
        { method: 'POST', body: JSON.stringify({ status }) },
      ),
    // ME-6: execute an insight (dry_run=true for "Проверить", false for "Выполнить")
    executeInsight: (
      insightKey: string,
      opts: { dry_run: boolean; overrides?: Record<string, unknown> } = { dry_run: false },
    ) =>
      req<InsightExecuteResult>(
        `/api/insights/${encodeURIComponent(insightKey)}/execute`,
        { method: 'POST', body: JSON.stringify({ dry_run: opts.dry_run, overrides: opts.overrides ?? {} }) },
      ),
  },

  // ME-6.1: execution history — "what PULT did for me"
  executions: {
    list: () => req<ExecutionLogItem[]>('/api/executions'),
    detail: (id: string) => req<ExecutionLogDetail>(`/api/executions/${encodeURIComponent(id)}`),
  },

  rebuildTracker: {
    track: (data: TrackRebuildPayload) =>
      req<{ ok: boolean; id: string; confidence_level: string }>('/api/rebuild/track', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    recommendation: () => req<RebuildRecommendation>('/api/rebuild/recommendation'),
    history: () => req<unknown[]>('/api/rebuild/history'),
    styleStats: () => req<{ stats: { style: string; avg_delta: number; count: number; winners: number }[]; is_demo: boolean; total_rebuilds: number }>('/api/rebuild/style-stats'),
  },

  creative: {
    score: (data: {
      product_name: string; category: string; preset: string
      marketplace: string; advantages: string[]; has_product_photo: boolean
    }) => req<CreativeScoreResponse>('/api/creative/score', {
      method: 'POST', body: JSON.stringify(data),
    }),
    optimize: (data: {
      product_name: string; category: string; marketplace: string
      advantages: string[]; has_product_photo: boolean
    }) => req<CreativeOptimizeResponse>('/api/creative/optimize', {
      method: 'POST', body: JSON.stringify(data),
    }),
    benchmarks: () => req<CreativeBenchmarksResponse>('/api/creative/benchmarks'),
    compareMarketplaces: (data: {
      product_name: string; category: string; preset: string
      advantages: string[]; has_product_photo: boolean
    }) => req<CreativeMarketplaceCompare>('/api/creative/compare-marketplaces', {
      method: 'POST', body: JSON.stringify(data),
    }),
  },

  seoIntelligence: {
    leaderboard: (params?: { marketplace?: string; category?: string; limit?: number }) => {
      const q = new URLSearchParams()
      if (params?.marketplace) q.set('marketplace', params.marketplace)
      if (params?.category)    q.set('category',    params.category)
      if (params?.limit)       q.set('limit',       String(params.limit))
      return req<StyleLeaderboardResponse>(`/api/seo-intelligence/leaderboard?${q}`)
    },
    styleDetail: (styleName: string, params?: { marketplace?: string; category?: string }) => {
      const q = new URLSearchParams()
      if (params?.marketplace) q.set('marketplace', params.marketplace)
      if (params?.category)    q.set('category',    params.category)
      return req<StyleDetailResponse>(`/api/seo-intelligence/style/${encodeURIComponent(styleName)}?${q}`)
    },
  },

  csvImport: {
    upload: (file: File, marketplace?: string, importType?: string) => {
      const form = new FormData()
      form.append('file', file)
      if (marketplace) form.append('marketplace', marketplace)
      if (importType)  form.append('import_type', importType)
      return reqForm<ImportPreviewResponse>('/api/import/upload', form)
    },
    confirm: (importId: string) =>
      req<ImportConfirmResponse>(`/api/import/${importId}/confirm`, { method: 'POST' }),
    history: () => req<ImportHistoryItem[]>('/api/import/history'),
    templateUrl: (marketplace: string, importType: string) =>
      `${API}/api/import/templates/${marketplace}/${importType}`,
    financeSummary: () => req<ImportFinanceSummary>('/api/import/finance/summary'),
    stats: () => req<ImportStatsResponse>('/api/import/stats'),
  },

  // ── Learning Surface (F1) — read-only ranked alternatives + decision evidence.
  // Contract rule: pass the SAME listing_id to both calls so the recommendation
  // and its evidence share one resolved context (see E4 consistency fix).
  learning: {
    getLearningAlternatives: (params: { insight_key: string; listing_id?: string }) => {
      const q = new URLSearchParams({ insight_key: params.insight_key })
      if (params.listing_id) q.set('listing_id', params.listing_id)
      return req<LearningAlternativesResponse>(`/api/learning/alternatives?${q}`)
    },
    getDecisionEvidence: (params: { insight_key: string; action_key: string; listing_id?: string }) => {
      const q = new URLSearchParams({ insight_key: params.insight_key, action_key: params.action_key })
      if (params.listing_id) q.set('listing_id', params.listing_id)
      return req<DecisionEvidenceResponse>(`/api/learning/evidence?${q}`)
    },
  },

  // ── SEO Engine (marketplace-agnostic; marketplace passed on every call) ──────
  seo: {
    getSeoOverview: (listingId: string, marketplace: string) =>
      req<SeoOverview>(`/api/seo/overview?${new URLSearchParams({ listing_id: listingId, marketplace })}`),
    getSeoSignals: (listingId: string, marketplace: string, status?: string) => {
      const q = new URLSearchParams({ listing_id: listingId, marketplace })
      if (status) q.set('status', status)
      return req<SeoSignalsResponse>(`/api/seo/signals?${q}`)
    },
    getSeoProblems: (listingId: string, marketplace: string) =>
      req<SeoProblemsResponse>(`/api/seo/problems?${new URLSearchParams({ listing_id: listingId, marketplace })}`),
    getSeoAudits: (listingId: string, marketplace: string) =>
      req<SeoAuditsResponse>(`/api/seo/audits?${new URLSearchParams({ listing_id: listingId, marketplace })}`),
    runSeoAudit: (payload: SeoAuditPayload) =>
      req<SeoAuditResult>('/api/seo/audit', { method: 'POST', body: JSON.stringify(payload) }),
  },

  // ── Advertising Engine (money-first; marketplace passed on every call) ───────
  advertising: {
    getAdvertisingOverview: (listingId: string, marketplace: string) =>
      req<AdvOverview>(`/api/advertising/overview?${new URLSearchParams({ listing_id: listingId, marketplace })}`),
    getAdvertisingSignals: (listingId: string, marketplace: string, status?: string) => {
      const q = new URLSearchParams({ listing_id: listingId, marketplace })
      if (status) q.set('status', status)
      return req<AdvSignalsResponse>(`/api/advertising/signals?${q}`)
    },
    getAdvertisingProblems: (listingId: string, marketplace: string) =>
      req<AdvProblemsResponse>(`/api/advertising/problems?${new URLSearchParams({ listing_id: listingId, marketplace })}`),
    getAdvertisingAudits: (listingId: string, marketplace: string) =>
      req<AdvAuditsResponse>(`/api/advertising/audits?${new URLSearchParams({ listing_id: listingId, marketplace })}`),
    runAdvertisingAudit: (payload: AdvAuditPayload) =>
      req<AdvAuditResult>('/api/advertising/audit', { method: 'POST', body: JSON.stringify(payload) }),
  },

  // ── Review Assistant (reputation contour, NOT an autoresponder) ──────────────
  // Review signals are not listing-scoped on the backend, so listingId is
  // optional and usually omitted; marketplace is provenance/context only. This
  // surface NEVER drafts, sends, or auto-publishes replies.
  reviewAssistant: {
    getReviewOverview: (listingId?: string, marketplace?: string) =>
      req<ReviewOverview>(`/api/reviews/overview${rvQuery(listingId, marketplace)}`),
    getReviewSignals: (listingId?: string, marketplace?: string, status?: string, safetyCategory?: string) => {
      const q = new URLSearchParams()
      if (listingId) q.set('listing_id', listingId)
      if (marketplace) q.set('marketplace', marketplace)
      if (status) q.set('status', status)
      if (safetyCategory) q.set('safety_category', safetyCategory)
      const s = q.toString()
      return req<ReviewSignalsResponse>(`/api/reviews/signals${s ? `?${s}` : ''}`)
    },
    getReviewProblems: (listingId?: string, marketplace?: string) =>
      req<ReviewProblemsResponse>(`/api/reviews/problems${rvQuery(listingId, marketplace)}`),
    getReviewAudits: (listingId?: string, marketplace?: string) =>
      req<ReviewAuditsResponse>(`/api/reviews/audits${rvQuery(listingId, marketplace)}`),
    runReviewAudit: (reviewId: string, marketplace?: string) =>
      req<ReviewAuditResult>('/api/reviews/audit', {
        method: 'POST',
        body: JSON.stringify({ review_id: reviewId, marketplace: marketplace ?? null }),
      }),
  },

  // ── Growth / Opportunity Engine (surfaces unrealised upside) ─────────────────
  // Growth signals ARE listing-scoped, so listingId is passed through. marketplace
  // is provenance/context. No fabricated index, no prediction, no rival data.
  growth: {
    getGrowthOverview: (listingId?: string, marketplace?: string) =>
      req<GrowthOverview>(`/api/growth/overview${rvQuery(listingId, marketplace)}`),
    getGrowthSignals: (listingId?: string, marketplace?: string, status?: string, category?: string) => {
      const q = new URLSearchParams()
      if (listingId) q.set('listing_id', listingId)
      if (marketplace) q.set('marketplace', marketplace)
      if (status) q.set('status', status)
      if (category) q.set('category', category)
      const s = q.toString()
      return req<GrowthSignalsResponse>(`/api/growth/signals${s ? `?${s}` : ''}`)
    },
    getGrowthProblems: (listingId?: string, marketplace?: string) =>
      req<GrowthProblemsResponse>(`/api/growth/problems${rvQuery(listingId, marketplace)}`),
    getGrowthAudits: (listingId?: string, marketplace?: string) =>
      req<GrowthAuditsResponse>(`/api/growth/audits${rvQuery(listingId, marketplace)}`),
    runGrowthAudit: (payload: GrowthAuditPayload) =>
      req<GrowthAuditResult>('/api/growth/audit', { method: 'POST', body: JSON.stringify(payload) }),
  },

  // ── Legal Navigator (recommendation contour; never a legal conclusion) ───────
  // Advisory only — never a verdict, never a promise, never an all-clear. Signals
  // are subject/listing-scoped. No rating, no prediction, no money.
  legalNavigator: {
    getLegalOverview: (listingId?: string, marketplace?: string) =>
      req<LegalOverview>(`/api/legal/overview${rvQuery(listingId, marketplace)}`),
    getLegalSignals: (listingId?: string, marketplace?: string, status?: string, category?: string) => {
      const q = new URLSearchParams()
      if (listingId) q.set('listing_id', listingId)
      if (marketplace) q.set('marketplace', marketplace)
      if (status) q.set('status', status)
      if (category) q.set('category', category)
      const s = q.toString()
      return req<LegalSignalsResponse>(`/api/legal/signals${s ? `?${s}` : ''}`)
    },
    getLegalAudits: (listingId?: string, marketplace?: string) =>
      req<LegalAuditsResponse>(`/api/legal/audits${rvQuery(listingId, marketplace)}`),
    getLegalFindings: (listingId?: string, marketplace?: string) =>
      req<LegalFindingsResponse>(`/api/legal/findings${rvQuery(listingId, marketplace)}`),
    runLegalAudit: (payload: LegalAuditPayload) =>
      req<LegalAuditResult>('/api/legal/audit', { method: 'POST', body: JSON.stringify(payload) }),
    acknowledge: (signalId: string) =>
      req<LegalSignalActionResult>(`/api/legal/signals/${signalId}/acknowledge`, { method: 'POST' }),
    dismiss: (signalId: string) =>
      req<LegalSignalActionResult>(`/api/legal/signals/${signalId}/dismiss`, { method: 'POST' }),
    reopen: (signalId: string) =>
      req<LegalSignalActionResult>(`/api/legal/signals/${signalId}/reopen`, { method: 'POST' }),
  },
}

function rvQuery(listingId?: string, marketplace?: string): string {
  const q = new URLSearchParams()
  if (listingId) q.set('listing_id', listingId)
  if (marketplace) q.set('marketplace', marketplace)
  const s = q.toString()
  return s ? `?${s}` : ''
}

// ── Catalog types ──────────────────────────────────────────────────────────────

export interface SupplierEntry {
  id: string
  company_name: string
  industry: string | null
  region: string | null
  country: string
  description: string | null
  website: string | null
  phone: string | null
  min_order_qty: number | null
  is_verified: boolean
  rating: number
  total_reviews: number
  total_deals: number
}

export interface TransportCompanyEntry {
  id: string
  name: string
  region: string | null
  delivery_types: string | null
  description: string | null
  phone: string | null
  rating: number
  total_reviews: number
  price_per_kg: number | null
  price_per_m3: number | null
  min_transit_days: number | null
  max_transit_days: number | null
}

export interface DeliveryResult {
  company_id: string
  company_name: string
  delivery_types: string | null
  min_transit_days: number | null
  max_transit_days: number | null
  estimated_cost: number
  rating: number
}

export interface DealEntry {
  id: string
  seller_id: string
  supplier_id: string
  product_name: string
  specification: string | null
  price_per_unit: number
  quantity: number
  total_price: number
  deadline: string | null
  status: string
  contract_text: string | null
  signed_by_seller: boolean
  signed_at: string | null
  created_at: string
  updated_at: string
}

export interface SupplierReviewEntry {
  id: string
  reviewer_id: string
  target_type: string
  target_id: string
  deal_id: string | null
  rating: number
  text: string | null
  created_at: string
}

// ── Promo types ────────────────────────────────────────────────────────────────

export interface PromoValidateResult {
  valid:           boolean
  promo_id?:       string
  code?:           string
  type?:           string
  value?:          number
  description?:    string
  original_price?: number
  final_price?:    number
  discount_amount?: number
  trial_days?:     number
  error?:          string
}

export interface PromoEntry {
  id:                  string
  code:                string
  type:                string
  value:               number
  description:         string | null
  applicable_plans:    string
  max_activations:     number | null
  current_activations: number
  is_active:           boolean
  blogger_name:        string | null
  expires_at:          string | null
  created_at:          string
}

export interface PromoStats {
  total_promos:      number
  active_promos:     number
  total_activations: number
  bloggers: {
    blogger_name:       string
    total_codes:        number
    total_activations:  number
    codes:              string[]
  }[]
}

// ── CSV Import types ───────────────────────────────────────────────────────────

export interface ImportPreviewResponse {
  import_id:           string
  marketplace:         string | null
  import_type:         string | null
  total_rows:          number
  valid_rows:          number
  skipped_rows:        number
  headers:             string[]
  mapped_columns:      Record<string, string>
  unmapped_required:   string[]
  preview_rows:        Record<string, unknown>[]
  warnings:            string[]
  errors:              string[]
  file_hash:           string
  duplicate_import_id: string | null
  duplicate_date:      string | null
}

export interface ImportConfirmResponse {
  import_id:      string
  imported_count: number
  skipped_count:  number
}

export interface ImportHistoryItem {
  id:             string
  filename:       string
  marketplace:    string
  import_type:    string
  status:         string
  total_rows:     number
  imported_count: number
  created_at:     string
  confirmed_at:   string | null
}

export interface ImportFinanceSummary {
  has_data:         boolean
  row_count:        number
  total_revenue:    number
  total_profit:     number
  total_commission: number
  total_logistics:  number
  total_ad_spend:   number
  margin_percent:   number
  by_marketplace:   Record<string, {
    revenue: number; profit: number; commission: number
    logistics: number; ad_spend: number; margin: number
  }>
  by_period: {
    period: string; period_label: string
    revenue: number; profit: number; commission: number
    logistics: number; ad_spend: number; quantity: number; margin: number
  }[]
  by_product: {
    sku: string; title: string; marketplace: string
    revenue: number; profit: number; margin: number; sales: number
  }[]
  last_import_date: string | null
}

export interface ImportStatsResponse {
  has_finance:      boolean
  has_products:     boolean
  products_count:   number
  revenue_total:    number
  last_import_date: string | null
}

// ── SEO Cards types ────────────────────────────────────────────────────────────

export interface TaskStatusFull {
  status:           string
  stage:            string | null
  progress:         number | null
  completed_count:  number | null
  slide_statuses:   string[] | null
  image_urls:       string[] | null
}

export interface SuggestTextResponse {
  title_suggestions:   string[]
  benefit_suggestions: string[]
  cta_suggestions:     string[]
}

// ── Creative Score types ───────────────────────────────────────────────────────

export interface CreativeScoreComponent {
  label:     string
  score:     number
  max_score: number
}

export interface CreativeAutoFix {
  action: 'set_preset' | 'set_marketplace'
  value:  string
  label:  string
}

export interface CreativeIssue {
  issue_type:   string
  severity:     'critical' | 'warning' | 'tip'
  description:  string
  fix_hint:     string
  score_impact: number
  auto_fix:     CreativeAutoFix | null
}

export interface CreativeScoreResponse {
  total:                 number
  grade:                 string
  predicted_ctr_uplift:  number
  improvement_potential: number
  best_preset_for_cat:   string
  components:            CreativeScoreComponent[]
  strengths:             string[]
  issues:                CreativeIssue[]
}

export interface CreativeVariantItem {
  variant_name: string
  preset:       string
  rank:         number
  score:        CreativeScoreResponse
}

export interface CreativeOptimizeResponse {
  variants:     CreativeVariantItem[]
  best_variant: string
  best_preset:  string
}

export interface CreativeMarketplaceCompare {
  wb:         CreativeScoreResponse
  ozon:       CreativeScoreResponse
  delta:      number
  better_for: 'wb' | 'ozon' | 'equal'
}

export interface CreativeBenchmarksResponse {
  has_data:       boolean
  total_rebuilds: number
  preset_stats:   { preset: string; count: number; avg_ctr_uplift: number }[]
  category_stats: { category: string; count: number; avg_ctr_uplift: number }[]
  top_preset:     string | null
  top_category:   string | null
}

export interface SeoProjectItem {
  id:                string
  user_id:           string
  name:              string
  product_name:      string
  marketplace:       string
  preset:            string
  category:          string
  typography_preset: string | null
  current_price:     string | null
  old_price:         string | null
  advantages:        string[]
  template_set:      string | null
  image_urls:        string[]
  created_at:        string
}

// ── Learning Surface types (F1) ──────────────────────────────────────────────
// Mirror the backend read-only contracts: GET /api/learning/alternatives (L6)
// and GET /api/learning/evidence (E2/E4). Counts are aggregate; no internal ids.

export interface LearningAlternative {
  action_key:     string
  rank:           number
  reason:         string
  fallback:       boolean
  confirmed:      number
  refuted:        number
  sample:         number
  confirmed_rate: number | null
  weighted_rate:  number | null
}

export interface LearningAlternativesResponse {
  insight_key:  string
  alternatives: LearningAlternative[]
  source:       string   // "decision_memory"
  degraded:     boolean  // true when the resolved context has an "unknown" segment
}

export interface DecisionEvidence {
  action_key:     string
  reason:         string
  context_group:  string
  confirmed:      number
  refuted:        number
  sample:         number
  confirmed_rate: number | null
  weighted_rate:  number | null
  fallback:       boolean
  source:         string   // "decision_memory"
}

export interface DecisionEvidenceResponse {
  insight_key: string
  evidence:    DecisionEvidence | null
}

// ── SEO Engine types ─────────────────────────────────────────────────────────

export interface SeoOverview {
  listing_id:          string | null
  active_signals:      number
  critical_signals:    number
  high_signals:        number
  unresolved_problems: number
  last_audit_at:       string | null
}

export interface SeoSignal {
  insight_key:             string | null
  signal_key:              string
  problem_type:            string
  status:                  string   // active | dismissed | resolved | reopened | promoted_to_decision
  priority_level:          string | null   // critical | high | medium | low
  recommended_action:      string | null   // what_to_do (human text)
  recommended_action_key:  string | null
  alternative_action_keys: string[]
  what:                    string | null
  why:                     string | null
  meaning:                 string | null
  expected_effect:         string | null
  effect_band:             string | null
  confidence:              number | null
}
export interface SeoSignalsResponse { items: SeoSignal[]; total: number }

export interface SeoProblem {
  problem_type:          string
  severity:              string   // critical | high | medium | low
  category:              string | null
  estimated_effect_type: string | null
  evidence:              Record<string, unknown> | null
  detected_at:           string | null
}
export interface SeoProblemsResponse { items: SeoProblem[]; total: number }

export interface SeoAuditItem {
  audit_id:            string
  listing_id:          string | null
  marketplace:         string | null
  sku:                 string | null
  status:              string
  total_problems:      number
  total_not_evaluated: number
  top_severity:        string | null
  triggered_by:        string | null
  created_at:          string | null
  completed_at:        string | null
}
export interface SeoAuditsResponse { items: SeoAuditItem[]; total: number }

export interface SeoManualConstraints {
  title_min_len:                 number
  title_max_len:                 number
  description_min_len:           number
  media_min_images:              number
  attribute_fill_rate_threshold: number
  content_completeness_threshold: number
}

export interface SeoManualSnapshot {
  sku?:         string
  title?:       string
  description?: string
  brand?:       string
  media?:       { image_count: number; video_present?: boolean }
  constraints?: SeoManualConstraints
}

export interface SeoAuditPayload {
  listing_id:  string
  marketplace: string
  snapshot?:   SeoManualSnapshot   // omitted → adapter mode
}

export interface SeoAuditResult {
  ok:                  boolean
  status:              string   // completed | snapshot_unavailable | unknown_marketplace
  listing_id:          string
  marketplace:         string
  audit_id:            string | null
  total_problems:      number | null
  total_not_evaluated: number | null
  top_severity:        string | null
  reconciliation:      { created: number; updated: number; resolved: number; reopened: number; unchanged: number } | null
  reason:              string | null
}

// ── Review Assistant types (reputation; no score, safety_mode always present) ─

export interface ReviewOverview {
  listing_id:          string | null
  active_signals:      number
  risk_signals:        number
  attention_signals:   number
  safe_signals:        number
  unresolved_problems: number
  total_not_evaluated: number
  last_audit_at:       string | null
}

export interface ReviewSignal {
  insight_key:             string | null
  signal_key:              string
  problem_type:            string
  review_id:               string | null
  status:                  string   // active | dismissed | resolved | reopened | promoted_to_decision
  safety_category:         string | null   // SAFE | ATTENTION | RISK
  safety_mode:             string | null   // off | manual_only | manual_approval | auto (never auto for RISK/ATTENTION)
  priority_level:          string | null
  recommended_action:      string | null   // human text (what_to_do)
  recommended_action_key:  string | null
  alternative_action_keys: string[]
  what:                    string | null
  why:                     string | null
  meaning:                 string | null
  expected_effect:         string | null
  effect_band:             string | null
  confidence:              number | null
}
export interface ReviewSignalsResponse { items: ReviewSignal[]; total: number }

export interface ReviewProblem {
  review_id:             string | null
  problem_type:          string
  severity:              string
  category:              string | null   // SAFE | ATTENTION | RISK
  estimated_effect_type: string | null
  evidence:              Record<string, unknown> | null
  detected_at:           string | null
}
export interface ReviewProblemsResponse { items: ReviewProblem[]; total: number }

export interface ReviewAuditItem {
  audit_id:            string
  status:              string
  total_problems:      number
  total_not_evaluated: number
  top_severity:        string | null
  triggered_by:        string | null
  created_at:          string | null
}
export interface ReviewAuditsResponse { items: ReviewAuditItem[]; total: number }

export interface ReviewAuditResult {
  ok:                  boolean
  status:              string   // completed | review_unavailable | <reason>
  review_id:           string
  audit_id:            string | null
  total_problems:      number | null
  total_not_evaluated: number | null
  top_severity:        string | null
  reconciliation:      { created: number; updated: number; resolved: number; reopened: number; unchanged: number } | null
  reason:              string | null
}

// ── Legal Navigator types (advisory only; no score, no verdict, no rubles) ───

export interface LegalOverview {
  listing_id:          string | null
  active_signals:      number
  high_signals:        number
  medium_signals:      number
  unresolved_signals:  number
  total_not_evaluated: number
  last_audit_at:       string | null
}

export interface LegalSignal {
  signal_id:               string
  insight_key:             string | null
  signal_key:              string
  requirement_type:        string
  category:                string | null
  status:                  string   // active|acknowledged|dismissed|resolved|reopened|promoted_to_decision
  lifecycle_reason:        string | null
  priority_level:          string | null
  risk_level:              string | null
  effect_type:             string | null   // qualitative *_risk_reduction
  effect_band:             string | null
  recommended_action_key:  string | null
  alternative_action_keys: string[]
  // 5-part doctrine
  what_happened:           string | null
  why_it_matters:          string | null
  meaning:                 string | null
  recommended_action:      string | null
  expected_effect:         string | null
  subject_type:            string | null
  subject_ref:             string | null
  sku:                     string | null
  marketplace:             string | null
  created_at:              string | null
  updated_at:              string | null
}
export interface LegalSignalsResponse { items: LegalSignal[]; total: number }

export interface LegalAuditItem {
  audit_id:            string
  status:              string
  total_findings:      number
  total_not_evaluated: number
  top_severity:        string | null
  triggered_by:        string | null
  created_at:          string | null
}
export interface LegalAuditsResponse { items: LegalAuditItem[]; total: number }

export interface LegalFinding {
  requirement_type:      string
  category:              string | null
  severity:              string
  risk_level:            string | null
  estimated_effect_type: string | null
  evidence:              Record<string, unknown> | null
  detected_at:           string | null
}
export interface LegalFindingsResponse { items: LegalFinding[]; total: number }

export interface LegalAuditPayload {
  marketplace:   string
  subject_type?: string
  subject_ref?:  string
  sku?:          string
  listing_id?:   string
}

export interface LegalAuditResult {
  ok:                  boolean
  status:              string   // completed | legal_unavailable | <reason>
  marketplace:         string
  subject_ref:         string | null
  sku:                 string | null
  audit_id:            string | null
  total_findings:      number | null
  total_not_evaluated: number | null
  top_severity:        string | null
  reconciliation:      { created: number; updated: number; reopened: number; resolved: number; unchanged: number } | null
  reason:              string | null
}

export interface LegalSignalActionResult { ok: boolean; signal: LegalSignal }

// ── Growth / Opportunity Engine types (no fabricated index, no prediction) ───

export interface GrowthOverview {
  listing_id:               string | null
  active_signals:           number
  high_signals:             number
  medium_signals:           number
  unresolved_opportunities: number
  total_not_evaluated:      number
  last_audit_at:            string | null
}

export interface GrowthSignal {
  insight_key:             string | null
  signal_key:              string
  problem_type:            string
  status:                  string
  category:                string | null   // pricing|advertising|seo|inventory|reputation
  priority_level:          string | null   // critical|high|medium|low
  recommended_action:      string | null   // what_to_do (human text)
  recommended_action_key:  string | null
  alternative_action_keys: string[]
  what:                    string | null
  why:                     string | null
  meaning:                 string | null
  expected_effect:         string | null
  effect_band:             string | null
  confidence:              number | null
}
export interface GrowthSignalsResponse { items: GrowthSignal[]; total: number }

export interface GrowthProblem {
  problem_type:          string
  severity:              string
  category:              string | null
  estimated_effect_type: string | null
  evidence:              Record<string, unknown> | null
  detected_at:           string | null
}
export interface GrowthProblemsResponse { items: GrowthProblem[]; total: number }

export interface GrowthAuditItem {
  audit_id:            string
  status:              string
  total_problems:      number
  total_not_evaluated: number
  top_severity:        string | null
  triggered_by:        string | null
  created_at:          string | null
}
export interface GrowthAuditsResponse { items: GrowthAuditItem[]; total: number }

export interface GrowthThresholdsIn {
  low_stock_units?:                 number | null
  min_revenue_for_growth_signal?:    number | null
  min_net_profit_for_growth_signal?: number | null
}

export interface GrowthAuditPayload {
  listing_id?: string
  marketplace: string
  sku:         string
  thresholds?: GrowthThresholdsIn
}

export interface GrowthAuditResult {
  ok:                  boolean
  status:              string   // completed | growth_unavailable | <reason>
  listing_id:          string | null
  marketplace:         string
  sku:                 string
  audit_id:            string | null
  total_problems:      number | null
  total_not_evaluated: number | null
  top_severity:        string | null
  reconciliation:      { created: number; updated: number; resolved: number; reopened: number; unchanged: number } | null
  reason:              string | null
}

// ── Advertising Engine types ─────────────────────────────────────────────────

export interface AdvOverview {
  listing_id:          string | null
  active_signals:      number
  critical_signals:    number
  high_signals:        number
  unresolved_problems: number
  total_not_evaluated: number
  last_audit_at:       string | null
}

export interface AdvSignal {
  insight_key:             string | null
  signal_key:              string
  problem_type:            string
  status:                  string
  priority_level:          string | null
  recommended_action:      string | null
  recommended_action_key:  string | null
  alternative_action_keys: string[]
  what:                    string | null
  why:                     string | null
  meaning:                 string | null
  expected_effect:         string | null
  effect_band:             string | null
  confidence:              number | null
}
export interface AdvSignalsResponse { items: AdvSignal[]; total: number }

export interface AdvProblem {
  problem_type:          string
  severity:              string
  category:              string | null
  estimated_effect_type: string | null
  evidence:              Record<string, unknown> | null
  detected_at:           string | null
}
export interface AdvProblemsResponse { items: AdvProblem[]; total: number }

export interface AdvAuditItem {
  audit_id:            string
  status:              string
  total_problems:      number
  total_not_evaluated: number
  top_severity:        string | null
  triggered_by:        string | null
  created_at:          string | null
}
export interface AdvAuditsResponse { items: AdvAuditItem[]; total: number }

export interface AdvThresholds {
  max_drr:                        number
  min_revenue_for_signal:         number
  min_ad_spend_for_signal:        number
  low_margin_threshold:           number
  low_stock_units:                number
  oos_risk_days:                  number
}

export interface AdvAuditPayload {
  listing_id?: string
  marketplace: string
  sku:         string
  thresholds?: AdvThresholds   // omitted → threshold rules not_evaluated
}

export interface AdvAuditResult {
  ok:                  boolean
  status:              string   // completed | finance_unavailable | <reason>
  listing_id:          string | null
  marketplace:         string
  sku:                 string
  audit_id:            string | null
  total_problems:      number | null
  total_not_evaluated: number | null
  top_severity:        string | null
  reconciliation:      { created: number; updated: number; resolved: number; reopened: number; unchanged: number } | null
  reason:              string | null
}