export interface InstrumentInfo {
  symbol: string
  displayName: string
  fullName: string
  category: 'INDEX' | 'STOCK' | 'CURRENCY' | 'COMMODITY'
  exchange: string
  lotSize: number
  strikeInterval: number
  iconBg: string
  iconText: string
  iconColor: string
  hasOptions: boolean
  hasFutures: boolean
}

// ─── Index Instruments ────────────────────────────────────────────────────────

const INDEX_INSTRUMENTS: InstrumentInfo[] = [
  {
    symbol: 'NIFTY', displayName: 'NIFTY 50', fullName: 'NIFTY 50',
    category: 'INDEX', exchange: 'NFO', lotSize: 75, strikeInterval: 50,
    iconBg: '#1565C0', iconText: '50', iconColor: '#FFFFFF',
    hasOptions: true, hasFutures: true,
  },
  {
    symbol: 'BANKNIFTY', displayName: 'NIFTY BANK', fullName: 'NIFTY BANK',
    category: 'INDEX', exchange: 'NFO', lotSize: 15, strikeInterval: 100,
    iconBg: '#E65100', iconText: 'B', iconColor: '#FFFFFF',
    hasOptions: true, hasFutures: true,
  },
  {
    symbol: 'FINNIFTY', displayName: 'NIFTY FIN SERVICE', fullName: 'NIFTY FINANCIAL SERVICES',
    category: 'INDEX', exchange: 'NFO', lotSize: 40, strikeInterval: 50,
    iconBg: '#558B2F', iconText: 'FN', iconColor: '#FFFFFF',
    hasOptions: true, hasFutures: true,
  },
  {
    symbol: 'SENSEX', displayName: 'SENSEX', fullName: 'S&P BSE SENSEX',
    category: 'INDEX', exchange: 'BFO', lotSize: 10, strikeInterval: 100,
    iconBg: '#6A1B9A', iconText: 'S', iconColor: '#FFFFFF',
    hasOptions: true, hasFutures: true,
  },
  {
    symbol: 'MIDCPNIFTY', displayName: 'NIFTY MIDCAP SELECT', fullName: 'NIFTY MIDCAP SELECT',
    category: 'INDEX', exchange: 'NFO', lotSize: 50, strikeInterval: 25,
    iconBg: '#37474F', iconText: 'MC', iconColor: '#FFFFFF',
    hasOptions: true, hasFutures: true,
  },
]

// ─── Stock Instruments ────────────────────────────────────────────────────────

function stockIcon(symbol: string): Pick<InstrumentInfo, 'iconBg' | 'iconText' | 'iconColor'> {
  return {
    iconBg: '#455A64',
    iconText: symbol.slice(0, 2).toUpperCase(),
    iconColor: '#FFFFFF',
  }
}

function stock(
  symbol: string,
  fullName: string,
  lotSize: number,
  strikeInterval: number
): InstrumentInfo {
  return {
    symbol,
    displayName: symbol,
    fullName,
    category: 'STOCK',
    exchange: 'NFO',
    lotSize,
    strikeInterval,
    ...stockIcon(symbol),
    hasOptions: true,
    hasFutures: true,
  }
}

const STOCK_INSTRUMENTS: InstrumentInfo[] = [
  stock('RELIANCE',   'Reliance Industries',     500,  50),
  stock('TCS',        'Tata Consultancy',         175,  50),
  stock('INFY',       'Infosys',                  400,  25),
  stock('HDFCBANK',   'HDFC Bank',                550,  10),
  stock('ICICIBANK',  'ICICI Bank',               700,  10),
  stock('SBIN',       'State Bank of India',      750,   5),
  stock('WIPRO',      'Wipro',                   3000,   5),
  stock('AXISBANK',   'Axis Bank',                625,  10),
  stock('KOTAKBANK',  'Kotak Mahindra Bank',     2000,  10),
  stock('LT',         'Larsen & Toubro',          175,  25),
  stock('BHARTIARTL', 'Bharti Airtel',            475,   5),
  stock('ITC',        'ITC Limited',             1600,   2),
  stock('BAJFINANCE', 'Bajaj Finance',            750,  50),
  stock('ASIANPAINT', 'Asian Paints',             250,  25),
  stock('MARUTI',     'Maruti Suzuki',             50, 100),
  stock('TATASTEEL',  'Tata Steel',              5500,   2),
  stock('ONGC',       'ONGC',                    2250,   2),
  stock('POWERGRID',  'Power Grid',              1900,   2),
  stock('NTPC',       'NTPC',                    1500,   2),
  stock('ADANIENT',   'Adani Enterprises',        309,  25),
  stock('HINDUNILVR', 'Hindustan Unilever',       300,  25),
  stock('SUNPHARMA',  'Sun Pharmaceutical',       350,  10),
  stock('DRREDDY',    "Dr. Reddy's Lab",          625,  50),
  stock('TECHM',      'Tech Mahindra',            600,  10),
  stock('HCLTECH',    'HCL Technologies',         350,  10),
  stock('ULTRACEMCO', 'UltraTech Cement',          50, 200),
  stock('TITAN',      'Titan Company',            175,  25),
  stock('BAJAJFINSV', 'Bajaj Finserv',            250,   5),
  stock('NESTLEIND',  'Nestle India',             500, 100),
  stock('DIVISLAB',   "Divi's Laboratories",      100,  50),
]

// ─── Full catalogue ───────────────────────────────────────────────────────────

export const STATIC_INSTRUMENTS: InstrumentInfo[] = [
  ...INDEX_INSTRUMENTS,
  ...STOCK_INSTRUMENTS,
]

export function getStaticInstrument(symbol: string): InstrumentInfo | undefined {
  return STATIC_INSTRUMENTS.find((i) => i.symbol === symbol)
}
