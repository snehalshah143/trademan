import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { format, formatDistanceToNow } from 'date-fns'

// ─── Tailwind ─────────────────────────────────────────────────────────────────

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

// ─── Number Formatting ────────────────────────────────────────────────────────

const INR_FORMATTER = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const INR_NO_FRACTION = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

export function fmtINR(value: number): string {
  return INR_FORMATTER.format(value)
}

export function fmtINRCompact(value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1_00_00_000) return `${sign}₹${(abs / 1_00_00_000).toFixed(2)}Cr`
  if (abs >= 1_00_000)    return `${sign}₹${(abs / 1_00_000).toFixed(2)}L`
  if (abs >= 1_000)       return `${sign}₹${(abs / 1_000).toFixed(1)}K`
  return INR_NO_FRACTION.format(value)
}

export function fmtPct(value: number, fractionDigits = 2): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(fractionDigits)}%`
}

export function fmtPrice(value: number, decimals = 2): string {
  return value.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function fmtPnl(value: number): string {
  const prefix = value >= 0 ? '+' : ''
  return `${prefix}${fmtINR(value)}`
}

// ─── Profit / Loss CSS Classes ────────────────────────────────────────────────

export function profitLossClass(value: number): string {
  if (value > 0) return 'text-profit'
  if (value < 0) return 'text-loss'
  return 'text-text-secondary'
}

export function profitLossBgClass(value: number): string {
  if (value > 0) return 'bg-green-500/10 text-profit'
  if (value < 0) return 'bg-red-500/10 text-loss'
  return 'bg-surface-3 text-text-secondary'
}

// ─── Date / Time Formatting ───────────────────────────────────────────────────

export function fmtTime(dateStr: string | number): string {
  return format(new Date(dateStr), 'HH:mm:ss')
}

export function fmtDate(dateStr: string | number): string {
  return format(new Date(dateStr), 'dd MMM yyyy')
}

export function fmtDateTime(dateStr: string | number): string {
  return format(new Date(dateStr), 'dd MMM HH:mm:ss')
}

export function fmtRelative(dateStr: string | number): string {
  return formatDistanceToNow(new Date(dateStr), { addSuffix: true })
}

/** Format expiry date like "28 Mar" */
export function fmtExpiry(dateStr: string): string {
  try {
    return format(new Date(dateStr), 'dd MMM')
  } catch {
    return dateStr
  }
}

// ─── Symbol Display ───────────────────────────────────────────────────────────

/** NIFTY24MAR22000CE → NIFTY 28 Mar 22000 CE */
export function fmtSymbolDisplay(symbol: string): string {
  // Try to parse option symbols — fallback to raw
  const match = symbol.match(/^([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE|FUT)?$/)
  if (!match) return symbol
  const [, underlying, _yy, mon, strike, optType] = match
  return `${underlying} ${_yy} ${mon} ${strike}${optType ? ' ' + optType : ''}`
}

// ─── Staleness ────────────────────────────────────────────────────────────────

export function isDataStale(lastUpdatedMs: number, thresholdMs = 5000): boolean {
  return Date.now() - lastUpdatedMs > thresholdMs
}

// ─── ID Generation ────────────────────────────────────────────────────────────

export function generateId(prefix = 'id'): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}
