'use client'

import Link from 'next/link'
import React from 'react'

// The frame every ledger screen sits in. It does two things and no more: it opens the `.ledger`
// scope (which is what swaps the palette and typography for the shared UI components), and it
// draws the page head — an eyebrow, a title, and at most one primary action.
//
// No animation on entry: these screens are opened many times a day, and a page that fades in
// feels slower than one that is simply there.

interface Crumb { label: string; href?: string }

export function LedgerShell({
  crumbs, title, action, children,
}: {
  crumbs?: Crumb[]
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="ledger" style={{ minHeight: '100vh' }}>
      <div className="l-page">
        {crumbs && crumbs.length > 0 && (
          <nav className="l-caps l-muted" style={{ paddingTop: 28 }}>
            {crumbs.map((c, i) => (
              <React.Fragment key={`${c.label}-${i}`}>
                {i > 0 && <span aria-hidden> → </span>}
                {c.href
                  ? <Link href={c.href} style={{ color: 'inherit', textDecoration: 'none' }}>{c.label}</Link>
                  : <span>{c.label}</span>}
              </React.Fragment>
            ))}
          </nav>
        )}
        <div className="l-head" style={crumbs?.length ? { paddingTop: 10 } : undefined}>
          <h1 className="l-serif l-h1">{title}</h1>
          {action}
        </div>
        {children}
      </div>
    </div>
  )
}

/** A labelled figure line — the ledger's way of stating a number without inventing a card. */
export function LedgerFigure({
  label, value, tone,
}: { label: string; value: React.ReactNode; tone?: 'green' | 'oxide' }) {
  return (
    <div
      style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'baseline',
        padding: '13px 0', borderBottom: '1px solid var(--line)', gap: 16,
      }}
    >
      <span className={tone === 'oxide' ? 'l-oxide' : 'l-dim'}>{label}</span>
      <span className={`l-num ${tone === 'green' ? 'l-green' : tone === 'oxide' ? 'l-oxide' : ''}`}
            style={{ fontSize: 17, whiteSpace: 'nowrap' }}>
        {value}
      </span>
    </div>
  )
}
