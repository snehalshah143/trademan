import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { STATIC_INSTRUMENTS, type InstrumentInfo } from '@/data/instruments'

async function fetchInstruments(): Promise<InstrumentInfo[]> {
  const res = await axios.get<{ instruments: InstrumentInfo[] }>('/api/instruments/list')
  return res.data.instruments ?? []
}

interface UseInstrumentsResult {
  instruments: InstrumentInfo[]
  indexInstruments: InstrumentInfo[]
  stockInstruments: InstrumentInfo[]
  loading: boolean
  getInstrument: (symbol: string) => InstrumentInfo | undefined
}

export function useInstruments(): UseInstrumentsResult {
  const query = useQuery({
    queryKey: ['instruments'],
    queryFn: fetchInstruments,
    staleTime: 24 * 60 * 60 * 1000, // 24 hours
    retry: 1,
  })

  // Fall back to static list if API fails or is loading
  const instruments = useMemo(
    () => (query.data && query.data.length > 0 ? query.data : STATIC_INSTRUMENTS),
    [query.data]
  )

  const indexInstruments = useMemo(
    () => instruments.filter((i) => i.category === 'INDEX'),
    [instruments]
  )

  const stockInstruments = useMemo(
    () => instruments.filter((i) => i.category === 'STOCK'),
    [instruments]
  )

  const getInstrument = useMemo(
    () => (symbol: string) => instruments.find((i) => i.symbol === symbol),
    [instruments]
  )

  return {
    instruments,
    indexInstruments,
    stockInstruments,
    loading: query.isFetching && !query.data,
    getInstrument,
  }
}
