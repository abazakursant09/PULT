/**
 * Landing social-proof figures — SINGLE SOURCE OF TRUTH.
 *
 * Fill ONLY with real, verifiable data. Empty string ('') means "not provided
 * yet" — the landing renders the bracketed placeholder ([SELLERS_COUNT] etc.)
 * until a real value is set here. Never put invented numbers in this file.
 */
export interface LandingProof {
  /** Total sellers using ПУЛЬТ, formatted. e.g. "2 400" */
  sellersCount: string
  /** Total losses ПУЛЬТ surfaced for clients, formatted. e.g. "180 млн ₽" */
  lossesFound: string
  /** Case-study headline amount, formatted. e.g. "210 000 ₽" */
  caseAmount: string
  /** Case-study quote (without surrounding quotes). */
  caseQuote: string
  /** Case-study attribution: "Имя, ниша, площадка". */
  caseAuthor: string
}

export const LANDING_PROOF: LandingProof = {
  sellersCount: '', // TODO: real data
  lossesFound:  '', // TODO: real data
  caseAmount:   '', // TODO: real data
  caseQuote:    '', // TODO: real data
  caseAuthor:   '', // TODO: real data
}
