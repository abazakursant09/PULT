export interface RouteFlags {
  growthContour: boolean
  billing: boolean
}

// These routes were literal ComingSoon placeholders. Keep the deny-list after deleting the
// pages so an accidental reintroduction cannot silently make an unfinished surface reachable.
export const UNAVAILABLE_ROUTE_PREFIXES = [
  '/ad-strategy',
  '/auto-promotions',
  '/community',
  '/dashboard/finance',
  '/dashboard/leaks',
  '/dashboard/opportunities',
  '/dashboard/products',
  '/dashboard/risks',
  '/dashboard/seo',
  '/dashboard/sklad',
  '/dashboard/zakazy',
] as const

export const GROWTH_ROUTE_PREFIXES = [
  '/academy',
  '/ideas',
  '/market-overview',
  '/dashboard/deals',
  '/dashboard/referrals',
] as const

export const BILLING_ROUTE_PREFIXES = [
  '/checkout',
  '/dashboard/billing',
  '/payment/result',
  '/startup',
] as const

export const AUTHENTICATED_TOOL_PREFIXES = [
  '/academy',
  '/ai-agents',
  '/ideas',
  '/logistics',
  '/market-overview',
  '/profit-calculator',
  '/suppliers',
  '/admin',
] as const

export function matchesRoutePrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`)
}

export function isRouteUnavailable(pathname: string, flags: RouteFlags): boolean {
  if (UNAVAILABLE_ROUTE_PREFIXES.some(prefix => matchesRoutePrefix(pathname, prefix))) return true
  if (!flags.growthContour && GROWTH_ROUTE_PREFIXES.some(prefix => matchesRoutePrefix(pathname, prefix))) return true
  if (!flags.billing && BILLING_ROUTE_PREFIXES.some(prefix => matchesRoutePrefix(pathname, prefix))) return true
  return false
}
