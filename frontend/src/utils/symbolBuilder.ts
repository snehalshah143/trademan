/**
 * Symbol builder utilities for F&O instrument names.
 * Format: UNDERLYING + DDMONYY + TYPE + STRIKE
 * e.g. NIFTY27MAR26CE24700
 */

const MONTH_ABBR = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']

/** Normalize expiry to DD-MON-YY format (e.g. "27-MAR-26") */
export function normalizeExpiry(raw: string): string {
  const s = raw.trim().toUpperCase()

  // Already DD-MON-YY
  if (/^\d{2}-[A-Z]{3}-\d{2}$/.test(s)) return s

  // DD-MON-YYYY
  if (/^\d{2}-[A-Z]{3}-\d{4}$/.test(s)) {
    const parts = s.split('-')
    return `${parts[0]}-${parts[1]}-${parts[2].slice(2)}`
  }

  // DDMONYY or DDMONYYYY
  const m1 = s.match(/^(\d{2})([A-Z]{3})(\d{2,4})$/)
  if (m1) {
    const yr = m1[3].length === 4 ? m1[3].slice(2) : m1[3]
    return `${m1[1]}-${m1[2]}-${yr}`
  }

  // DD/MM/YY or DD/MM/YYYY
  const m2 = s.match(/^(\d{2})\/(\d{2})\/(\d{2,4})$/)
  if (m2) {
    const month = parseInt(m2[2], 10)
    if (month >= 1 && month <= 12) {
      const yr = m2[3].length === 4 ? m2[3].slice(2) : m2[3]
      return `${m2[1]}-${MONTH_ABBR[month - 1]}-${yr}`
    }
  }

  // DD-MM-YY or DD-MM-YYYY
  const m3 = s.match(/^(\d{2})-(\d{2})-(\d{2,4})$/)
  if (m3) {
    const month = parseInt(m3[2], 10)
    if (month >= 1 && month <= 12) {
      const yr = m3[3].length === 4 ? m3[3].slice(2) : m3[3]
      return `${m3[1]}-${MONTH_ABBR[month - 1]}-${yr}`
    }
  }

  return s  // return as-is if we can't parse
}

/** Build a full instrument symbol from parts. */
export function buildSymbol(
  underlying: string,
  expiry: string,   // expects DD-MON-YY format
  optionType: 'CE' | 'PE' | 'FUT',
  strike?: number,
): string {
  const u = underlying.toUpperCase()
  // Convert expiry DD-MON-YY → DDMONYY
  const expiryParts = expiry.split('-')
  const expStr = expiryParts.join('')  // e.g. 27MAR26
  if (optionType === 'FUT') {
    return `${u}${expStr}FUT`
  }
  const strikeStr = strike !== undefined ? String(Math.round(strike)) : '0'
  return `${u}${expStr}${optionType}${strikeStr}`
}

/** Parse a full symbol string back into parts. */
export function parseSymbol(symbol: string): {
  underlying: string
  expiry: string  // DD-MON-YY
  optionType: 'CE' | 'PE' | 'FUT'
  strike: number | null
} | null {
  const s = symbol.toUpperCase()

  // Match: UNDERLYING + DDMONYY + (CE|PE) + STRIKE
  const m1 = s.match(/^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(CE|PE)(\d+(?:\.\d+)?)$/)
  if (m1) {
    return {
      underlying: m1[1],
      expiry: normalizeExpiry(m1[2]),
      optionType: m1[3] as 'CE' | 'PE',
      strike: parseFloat(m1[4]),
    }
  }

  // Match: UNDERLYING + DDMONYY + FUT
  const m2 = s.match(/^([A-Z]+)(\d{2}[A-Z]{3}\d{2})FUT$/)
  if (m2) {
    return {
      underlying: m2[1],
      expiry: normalizeExpiry(m2[2]),
      optionType: 'FUT',
      strike: null,
    }
  }

  return null
}
