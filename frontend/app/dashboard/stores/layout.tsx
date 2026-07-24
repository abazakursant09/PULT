import '../../../styles/ledger.css'
import { ledgerFontVars } from '@/components/stores/ledgerFonts'

// The ledger theme and its three fonts are mounted HERE, on the store routes only. Loading them
// in app/layout.tsx would restyle every other page in PULT — which this slice must not do.

export default function StoresLayout({ children }: { children: React.ReactNode }) {
  return <div className={ledgerFontVars}>{children}</div>
}
