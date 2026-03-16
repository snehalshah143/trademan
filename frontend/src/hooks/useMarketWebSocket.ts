import { useEffect, useRef, useCallback } from 'react'
import { useLTPStore } from '@store/ltpStore'
import { useAlertStore } from '@store/alertStore'
import type { WSMessage, WSLTPTickPayload, WSLTPBatchPayload, AlertEvent, WSConnectionStatusPayload } from '@/types/domain'

// ─── Config ───────────────────────────────────────────────────────────────────

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/market`
const MAX_RECONNECT_ATTEMPTS = 10
const BASE_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 30_000

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useMarketWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const attemptsRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  const { updateTick, updateBatch, setConnectionStatus, setHeartbeat } = useLTPStore()
  const { addEvent } = useAlertStore()

  const getBackoffMs = (attempt: number): number => {
    const backoff = Math.min(BASE_BACKOFF_MS * Math.pow(2, attempt), MAX_BACKOFF_MS)
    // Add ±10% jitter
    return backoff * (0.9 + Math.random() * 0.2)
  }

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return

    setConnectionStatus('CONNECTING')

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return }
      attemptsRef.current = 0
      setConnectionStatus('CONNECTED')
      setHeartbeat()
    }

    ws.onmessage = (event: MessageEvent) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(event.data) as WSMessage
        handleMessage(msg)
      } catch {
        // ignore malformed frames
      }
    }

    ws.onerror = () => {
      // onerror is always followed by onclose — handle reconnect there
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      wsRef.current = null

      if (attemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setConnectionStatus('FAILED')
        return
      }

      setConnectionStatus('RECONNECTING')
      const delay = getBackoffMs(attemptsRef.current)
      attemptsRef.current += 1

      reconnectTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connect()
      }, delay)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleMessage = (msg: WSMessage) => {
    switch (msg.type) {
      case 'LTP_TICK': {
        const payload = msg.payload as WSLTPTickPayload
        updateTick({
          symbol: payload.symbol,
          ltp: payload.ltp,
          change: payload.change,
          changePct: payload.changePct,
          timestamp: payload.timestamp,
        })
        setHeartbeat()
        break
      }

      case 'LTP_BATCH': {
        const payload = msg.payload as WSLTPBatchPayload
        updateBatch(
          payload.ticks.map((t) => ({
            symbol: t.symbol,
            ltp: t.ltp,
            change: t.change,
            changePct: t.changePct,
            timestamp: t.timestamp,
          }))
        )
        setHeartbeat()
        break
      }

      case 'ALERT_FIRED': {
        const event = msg.payload as AlertEvent
        addEvent(event)
        break
      }

      case 'CONNECTION_STATUS': {
        const payload = msg.payload as WSConnectionStatusPayload
        setConnectionStatus(payload.status)
        break
      }

      case 'STALE_WARNING': {
        setConnectionStatus('CONNECTED') // still connected but data is stale
        break
      }

      case 'PONG': {
        setHeartbeat()
        break
      }

      default:
        break
    }
  }

  // Periodic ping to keep connection alive
  useEffect(() => {
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'PING', payload: null, timestamp: Date.now() }))
      }
    }, 30_000)

    return () => clearInterval(pingInterval)
  }, [])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null // prevent reconnect on unmount
        wsRef.current.close()
        wsRef.current = null
      }
      setConnectionStatus('DISCONNECTED')
    }
  }, [connect]) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    connectionStatus: useLTPStore((s) => s.connectionStatus),
    reconnect: () => {
      attemptsRef.current = 0
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
        wsRef.current = null
      }
      connect()
    },
  }
}
