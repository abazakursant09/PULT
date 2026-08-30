'use client'

import { useId, type SVGProps } from 'react'

/**
 * Canonical PULT mark.
 *
 * The geometry is intentionally shared by every product surface: a Cyrillic
 * «П», two upper facets converging on the hub, and the routed flow continuing
 * into the right leg. Consumers may change only the surrounding size and the
 * frame contrast through `color`; the brand geometry remains here.
 */
export function PultMark(props: SVGProps<SVGSVGElement>) {
  const gradientId = useId().replace(/:/g, '')
  const bodyGradient = `${gradientId}-body`
  const facetGradient = `${gradientId}-facet`

  return (
    <svg viewBox="0 0 28 32" fill="none" aria-hidden="true" {...props}>
      <defs>
        <linearGradient id={bodyGradient} x1="5" y1="3" x2="23" y2="29" gradientUnits="userSpaceOnUse">
          <stop stopColor="#F8F6FF" stopOpacity=".72" />
          <stop offset=".43" stopColor="#8E7AF0" stopOpacity=".25" />
          <stop offset="1" stopColor="#4E59C8" stopOpacity=".34" />
        </linearGradient>
        <linearGradient id={facetGradient} x1="7" y1="4" x2="22" y2="24" gradientUnits="userSpaceOnUse">
          <stop stopColor="#FFFFFF" stopOpacity=".52" />
          <stop offset=".48" stopColor="#947EF1" stopOpacity=".2" />
          <stop offset="1" stopColor="#4EA5C0" stopOpacity=".25" />
        </linearGradient>
      </defs>

      <path
        className="pult-mark-body"
        fill={`url(#${bodyGradient})`}
        fillRule="evenodd"
        d="M7.25 2.75h13.5a4 4 0 0 1 4 4V29.25h-7.2V16.1c0-2.88-1.5-4.7-3.55-4.7s-3.55 1.82-3.55 4.7v13.15h-7.2V6.75a4 4 0 0 1 4-4Z"
      />
      <path className="pult-mark-facet" fill={`url(#${facetGradient})`} d="M5.05 5.35c.52-1.03 1.33-1.65 2.48-1.82h12.92L12.15 11 5.05 5.35Z" />
      <path className="pult-mark-facet" fill={`url(#${facetGradient})`} opacity=".78" d="m20.45 3.53 2.5 1.82c.22.44.33.9.33 1.4v9.7c-2.72-2.65-6.39-4.72-11.13-5.45l8.3-7.47Z" />
      <path className="pult-mark-facet" fill={`url(#${facetGradient})`} opacity=".72" d="M12.15 11c4.74.73 8.41 2.8 11.13 5.45v10.9h-4.2V16.1c0-2.82-1.3-4.89-3.34-5.83L12.15 11Z" />

      <path
        className="pult-mark-frame"
        stroke="currentColor"
        strokeWidth="1.45"
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3.25 29.25V6.75a4 4 0 0 1 4-4h13.5a4 4 0 0 1 4 4v22.5M10.45 29.25V16.1c0-2.88 1.5-4.7 3.55-4.7s3.55 1.82 3.55 4.7v13.15"
      />
      <path
        className="pult-mark-flow"
        stroke="#8E7BEA"
        strokeWidth="1.15"
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M5.05 5.35 12.15 11M22.95 5.35 12.15 11M12.15 11c4.74.73 8.41 2.8 11.13 5.45"
      />
      <circle className="pult-mark-hub" cx="12.15" cy="11" r="2.15" fill="#F9F7FF" stroke="#7561DD" strokeWidth=".9" />
      <circle className="pult-mark-glint" cx="11.55" cy="10.35" r=".55" fill="#FFFFFF" opacity=".9" />
    </svg>
  )
}
