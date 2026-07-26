import '../../../styles/ledger.css'
import { ledgerFontVars } from '@/components/stores/ledgerFonts'

export default function ImportsLayout({ children }: { children: React.ReactNode }) {
  return <div className={ledgerFontVars}>{children}</div>
}
