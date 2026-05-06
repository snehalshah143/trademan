/**
 * Parse OpenAlgo F&O symbols.
 * Format: {UNDERLYING}{DD}{MON}{YY}{STRIKE}{CE|PE|FUT}
 * Examples:
 *   BHEL26MAY26335PE   → BHEL 26 MAY 2026 335 PE
 *   NATIONALUM26MAY26FUT → NATIONALUM 26 MAY 2026 FUT
 *   AFCONS              → AFCONS (equity)
 */

const MONTH_NAMES: Record<string, string> = {
  JAN: 'Jan', FEB: 'Feb', MAR: 'Mar', APR: 'Apr',
  MAY: 'May', JUN: 'Jun', JUL: 'Jul', AUG: 'Aug',
  SEP: 'Sep', OCT: 'Oct', NOV: 'Nov', DEC: 'Dec',
}

const MONTH_NUM: Record<string, string> = {
  JAN: '01', FEB: '02', MAR: '03', APR: '04',
  MAY: '05', JUN: '06', JUL: '07', AUG: '08',
  SEP: '09', OCT: '10', NOV: '11', DEC: '12',
}

// Matches: BHEL26MAY26335PE or NATIONALUM26MAY26FUT
const FO_REGEX = /^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d*)(CE|PE|FUT)$/

export interface ParsedSymbol {
  underlying:  string
  expiry:        string | null   // ISO date "2026-05-26"
  expiryDisplay: string | null   // "26 May 2026"
  expiryShort:   string | null   // "26MAY26"
  strike:        number | null
  optType:       'CE' | 'PE' | 'FUT' | null
  isEquity:      boolean
  displayName:   string          // "BHEL 26 MAY 2026 335 PE (NFO)"
}

export function parseSymbol(symbol: string, exchange = 'NFO'): ParsedSymbol {
  const m = symbol.match(FO_REGEX)
  if (!m) {
    return {
      underlying:    symbol,
      expiry:        null,
      expiryDisplay: null,
      expiryShort:   null,
      strike:        null,
      optType:       null,
      isEquity:      true,
      displayName:   `${symbol} (${exchange})`,
    }
  }

  const [, underlying, dd, mon, yy, strikeRaw, optType] = m
  const strike   = strikeRaw ? Number(strikeRaw) : null
  const monName  = MONTH_NAMES[mon] ?? mon
  const monNum   = MONTH_NUM[mon] ?? '01'
  const fullYear = `20${yy}`
  const expiry        = `${fullYear}-${monNum}-${dd}`
  const expiryDisplay = `${dd} ${monName} ${fullYear}`
  const expiryShort   = `${dd}${mon}${yy}`

  const parts = [underlying, expiryDisplay]
  if (strike) parts.push(String(strike))
  if (optType !== 'FUT') parts.push(optType)
  else parts.push('FUT')

  return {
    underlying,
    expiry,
    expiryDisplay,
    expiryShort,
    strike,
    optType: optType as 'CE' | 'PE' | 'FUT',
    isEquity: false,
    displayName: `${parts.join(' ')} (${exchange})`,
  }
}

/** Extract underlying name from a symbol (for strategy grouping). */
export function extractUnderlying(symbol: string): string {
  return parseSymbol(symbol).underlying
}

/** Format expiry for section sub-header: "26 May 2026" */
export function fmtExpiryFull(expiry: string | null): string {
  if (!expiry) return 'No Expiry'
  try {
    const d = new Date(expiry)
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch {
    return expiry
  }
}
