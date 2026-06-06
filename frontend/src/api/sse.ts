import type { SseEvent } from '../types/api'

/**
 * Opens an EventSource SSE connection.
 * Returns a cleanup function — call it to close the stream.
 * The `onDone` callback fires when the server sends a `type:"done"` event or the stream closes.
 */
export function connectSSE(
  url: string,
  onEvent: (event: SseEvent) => void,
  onDone: () => void,
  onError?: (msg: string) => void,
): () => void {
  const es = new EventSource(url)

  es.onmessage = (e: MessageEvent<string>) => {
    let data: SseEvent
    try {
      data = JSON.parse(e.data) as SseEvent
    } catch {
      return
    }

    if (data.type === 'done') {
      onDone()
      es.close()
      return
    }

    if (data.type === 'error') {
      onError?.(String(data.message ?? 'Stream error'))
      onDone()
      es.close()
      return
    }

    onEvent(data)
  }

  es.onerror = () => {
    onError?.('SSE connection lost')
    onDone()
    es.close()
  }

  return () => es.close()
}
