/**
 * Bulk leg parser — supports 3 formats:
 * Format 1: SIDE STRIKE TYPE EXPIRY QTY [PRICE]
 * Format 2: SIDE FULL_SYMBOL QTY [PRICE]
 * Format 3: CSV with optional header
 */

import { normalizeExpiry, parseSymbol, buildSymbol } from './symbolBuilder'

export interface ParsedLeg {
  leg_number: number
  side: 'BUY' | 'SELL'
  instrument: string
  underlying: string
  strike: number | null
  option_type: 'CE' | 'PE' | 'FUT'
  expiry: string          // DD-MON-YY
  quantity: number
  lot_size: number
  entry_price: number | null  // null = needs LTP fetch
}

export interface ParseResult {
  success: boolean
  legs: ParsedLeg[]
  errors: Array<{ line: number; message: string }>
}

const SIDE_MAP: Record<string, 'BUY' | 'SELL'> = {
  B: 'BUY', BUY: 'BUY', S: 'SELL', SELL: 'SELL',
}

const TYPE_MAP: Record<string, 'CE' | 'PE' | 'FUT'> = {
  CE: 'CE', C: 'CE', CALL: 'CE',
  PE: 'PE', P: 'PE', PUT: 'PE',
  FUT: 'FUT', F: 'FUT', FUTURES: 'FUT',
}

const CSV_HEADER_FIELDS = new Set(['side', 'strike', 'type', 'expiry', 'qty', 'price', 'quantity'])

function isCsvHeaderLine(line: string): boolean {
  const lower = line.toLowerCase()
  let matches = 0
  for (const field of CSV_HEADER_FIELDS) {
    if (lower.includes(field)) matches++
  }
  return matches >= 2
}

function parseSide(raw: string): 'BUY' | 'SELL' | null {
  return SIDE_MAP[raw.toUpperCase()] ?? null
}

function parseOptionType(raw: string): 'CE' | 'PE' | 'FUT' | null {
  return TYPE_MAP[raw.toUpperCase()] ?? null
}

function parseQuantity(raw: string): number | null {
  const n = parseInt(raw.replace(/,/g, ''), 10)
  return isNaN(n) || n <= 0 ? null : n
}

function parsePrice(raw: string): number | null {
  if (!raw || raw === '-' || raw === '') return null
  const n = parseFloat(raw.replace(/,/g, ''))
  return isNaN(n) || n <= 0 ? null : n
}

function parseStrike(raw: string): number | null {
  const n = parseFloat(raw.replace(/,/g, ''))
  return isNaN(n) ? null : n
}

/** Detect format: CSV if commas present, else space/tab */
function detectFormat(lines: string[]): 'csv' | 'space' {
  const nonEmpty = lines.filter(l => l.trim() && !l.startsWith('#'))
  for (const line of nonEmpty.slice(0, 3)) {
    if (line.includes(',')) return 'csv'
  }
  return 'space'
}

/** Split a line by commas or whitespace */
function splitLine(line: string, format: 'csv' | 'space'): string[] {
  if (format === 'csv') {
    return line.split(',').map(s => s.trim())
  }
  return line.trim().split(/\s+/)
}

export function parseBulkLegs(
  text: string,
  underlying: string,
  lotSize: number,
): ParseResult {
  const rawLines = text.split('\n')
  const errors: Array<{ line: number; message: string }> = []
  const legs: ParsedLeg[] = []
  const format = detectFormat(rawLines)

  let lineNum = 0
  let legIndex = 0

  for (const rawLine of rawLines) {
    lineNum++
    const trimmed = rawLine.trim()

    // Skip blanks and comments
    if (!trimmed || trimmed.startsWith('#')) continue

    // Skip header lines in CSV
    if (isCsvHeaderLine(trimmed)) continue

    if (legIndex >= 10) {
      errors.push({ line: lineNum, message: 'Maximum 10 legs exceeded — remaining lines ignored' })
      break
    }

    const parts = splitLine(trimmed, format)
    if (parts.length < 3) {
      errors.push({ line: lineNum, message: `Too few columns (expected at least SIDE, STRIKE/SYMBOL, TYPE) — got "${trimmed}"` })
      continue
    }

    const sideRaw  = parts[0]
    const side     = parseSide(sideRaw)
    if (!side) {
      errors.push({ line: lineNum, message: `Invalid side "${sideRaw}" — expected BUY or SELL (or B/S)` })
      continue
    }

    // Detect Format 2: second token looks like a full symbol (contains digits mid-string)
    const secondToken = parts[1]
    const parsedSymbol = parseSymbol(secondToken)

    if (parsedSymbol) {
      // Format 2: SIDE FULL_SYMBOL QTY [PRICE]
      const qty   = parseQuantity(parts[2] ?? '')
      const price = parts[3] ? parsePrice(parts[3]) : null

      if (!qty) {
        errors.push({ line: lineNum, message: `Invalid quantity "${parts[2]}"` })
        continue
      }

      legIndex++
      legs.push({
        leg_number:  legIndex,
        side,
        instrument:  secondToken.toUpperCase(),
        underlying:  parsedSymbol.underlying || underlying.toUpperCase(),
        strike:      parsedSymbol.strike,
        option_type: parsedSymbol.optionType,
        expiry:      parsedSymbol.expiry,
        quantity:    qty,
        lot_size:    lotSize,
        entry_price: price,
      })
    } else {
      // Format 1: SIDE STRIKE TYPE EXPIRY QTY [PRICE]
      // Format 3 CSV: same column order
      if (parts.length < 5) {
        errors.push({ line: lineNum, message: `Line ${lineNum}: expected SIDE STRIKE TYPE EXPIRY QTY [PRICE], got ${parts.length} columns` })
        continue
      }

      const strikeRaw = parts[1]
      const typeRaw   = parts[2]
      const expiryRaw = parts[3]
      const qtyRaw    = parts[4]
      const priceRaw  = parts[5] ?? ''

      const strike = parseStrike(strikeRaw)
      if (strike === null) {
        errors.push({ line: lineNum, message: `Invalid strike "${strikeRaw}"` })
        continue
      }

      const optionType = parseOptionType(typeRaw)
      if (!optionType) {
        errors.push({ line: lineNum, message: `Invalid option type "${typeRaw}" — expected CE, PE, or FUT` })
        continue
      }

      const expiry = normalizeExpiry(expiryRaw)
      if (!expiry || expiry === expiryRaw.toUpperCase() && !expiry.match(/^\d{2}-[A-Z]{3}-\d{2}$/)) {
        errors.push({ line: lineNum, message: `Could not parse expiry "${expiryRaw}"` })
        continue
      }

      const qty = parseQuantity(qtyRaw)
      if (!qty) {
        errors.push({ line: lineNum, message: `Invalid quantity "${qtyRaw}"` })
        continue
      }

      const price = priceRaw ? parsePrice(priceRaw) : null

      const instrument = buildSymbol(underlying, expiry, optionType, optionType === 'FUT' ? undefined : strike)

      legIndex++
      legs.push({
        leg_number:  legIndex,
        side,
        instrument,
        underlying:  underlying.toUpperCase(),
        strike:      optionType === 'FUT' ? null : strike,
        option_type: optionType,
        expiry,
        quantity:    qty,
        lot_size:    lotSize,
        entry_price: price,
      })
    }
  }

  return {
    success: errors.length === 0 && legs.length > 0,
    legs,
    errors,
  }
}
