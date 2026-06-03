'use client'

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { type Lang, getT, LANGS } from './i18n'

interface LangCtx {
  lang:    Lang
  setLang: (l: Lang) => void
  t:       ReturnType<typeof getT>
}

const Ctx = createContext<LangCtx>({
  lang:    'ru',
  setLang: () => {},
  t:       getT('ru'),
})

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>('ru')

  useEffect(() => {
    const stored = localStorage.getItem('lang') as Lang | null
    if (stored && LANGS.some(l => l.code === stored)) {
      setLangState(stored)
    }
  }, [])

  function setLang(l: Lang) {
    setLangState(l)
    localStorage.setItem('lang', l)
    document.documentElement.lang = l
  }

  return (
    <Ctx.Provider value={{ lang, setLang, t: getT(lang) }}>
      {children}
    </Ctx.Provider>
  )
}

export function useLang() {
  return useContext(Ctx)
}
