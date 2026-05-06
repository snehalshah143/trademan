import { useQuery } from '@tanstack/react-query'
import { positionService } from '@/services/positionService'

export function useBrokerPositions() {
  return useQuery({
    queryKey: ['broker-positions'],
    queryFn:  positionService.getPositions,
    refetchInterval: 5000,
    retry: false,
    placeholderData: (prev) => prev,
  })
}

export function useFunds() {
  return useQuery({
    queryKey: ['broker-funds'],
    queryFn:  positionService.getFunds,
    refetchInterval: 10000,
    retry: false,
  })
}

export function useConnectionStatus() {
  return useQuery({
    queryKey: ['health'],
    queryFn:  () => fetch('/health').then(r => r.json()) as Promise<{ status: string; broker_connected: boolean; adapter: string }>,
    refetchInterval: 8000,
    retry: false,
  })
}
