import '../../../styles/ledger.css'
import { ledgerFontVars } from '@/components/stores/ledgerFonts'

// /dashboard/import is now the first step of the import — choosing a store — so it shares the
// ledger language with the screens it leads into.

export default function ImportEntryLayout({ children }: { children: React.ReactNode }) {
  return <div className={ledgerFontVars}>{children}</div>
}
