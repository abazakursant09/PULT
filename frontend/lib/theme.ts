export const THEME_STORAGE_KEY = 'pult_seller_theme_v1'

export const THEME_IDS = ['champagne', 'obsidian', 'titanium', 'pearl', 'system'] as const

export type ThemeId = (typeof THEME_IDS)[number]

export interface ThemeOption {
  id: ThemeId
  name: string
  description: string
  swatches: readonly [string, string, string]
  mode: 'dark' | 'light' | 'adaptive'
}

// The approved option 4: calm graphite with a restrained champagne accent.
export const DEFAULT_THEME: ThemeId = 'champagne'

export const THEME_OPTIONS: readonly ThemeOption[] = [
  {
    id: 'champagne',
    name: 'Шампань + графит',
    description: 'Основная спокойная тема для ежедневной работы',
    swatches: ['#171817', '#272824', '#C9A96D'],
    mode: 'dark',
  },
  {
    id: 'obsidian',
    name: 'Обсидиан + сапфир',
    description: 'Глубокий холодный контраст для вечерней работы',
    swatches: ['#090D18', '#172139', '#839CFF'],
    mode: 'dark',
  },
  {
    id: 'titanium',
    name: 'Титановый дым',
    description: 'Нейтральный графит без цветового давления',
    swatches: ['#202326', '#30353A', '#BBC4CC'],
    mode: 'dark',
  },
  {
    id: 'pearl',
    name: 'Жемчужное стекло',
    description: 'Мягкая светлая тема без ослепляющего белого',
    swatches: ['#ECECE7', '#FAF9F5', '#7660B9'],
    mode: 'light',
  },
  {
    id: 'system',
    name: 'Как в системе',
    description: 'Следует светлой или тёмной теме устройства',
    swatches: ['#ECECE7', '#202326', '#9A86D6'],
    mode: 'adaptive',
  },
] as const

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value)
}

export function themeForKey(current: ThemeId, key: string): ThemeId | undefined {
  const index = THEME_IDS.indexOf(current)
  if (key === 'Home') return THEME_IDS[0]
  if (key === 'End') return THEME_IDS[THEME_IDS.length - 1]
  if (key === 'ArrowRight' || key === 'ArrowDown') return THEME_IDS[(index + 1) % THEME_IDS.length]
  if (key === 'ArrowLeft' || key === 'ArrowUp') return THEME_IDS[(index + THEME_IDS.length - 1) % THEME_IDS.length]
  return undefined
}

export function readStoredTheme(): ThemeId {
  if (typeof window === 'undefined') return DEFAULT_THEME
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isThemeId(stored) ? stored : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function applyTheme(theme: ThemeId): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.sellerTheme = theme
}

export const THEME_BOOTSTRAP_SCRIPT = `
(function () {
  try {
    var allowed = ${JSON.stringify(THEME_IDS)};
    var value = localStorage.getItem('${THEME_STORAGE_KEY}');
    document.documentElement.dataset.sellerTheme = allowed.indexOf(value) >= 0 ? value : '${DEFAULT_THEME}';
  } catch (_) {
    document.documentElement.dataset.sellerTheme = '${DEFAULT_THEME}';
  }
})();`
