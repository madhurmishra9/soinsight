import type { SseEvent } from '../types/api'

/**
 * Opens an EventSource SSE connection.
 * Returns a cleanup function — call it to close the stream.
 *
 * `onDone` means the run finished successfully: it fires only on a server
 * `type:"done"` event. A failure calls `onError` INSTEAD of `onDone`, never as
 * well as it — callers append both to the same progress log, so calling both
 * would end a failed run with a success line ("✓ Fetch complete.") written
 * after the error. Each callback already clears its own running flag.
 */
export function connectSSE(
  url: string,
  onEvent: (event: SseEvent) => void,
  onDone: () => void,
  onError?: (msg: string) => void,
): () => void {
  const es = new EventSource(url)

  // Report a failure exactly once. Falls back to onDone only when the caller
  // supplied no error handler, so a run can never be left stuck "running".
  const fail = (msg: string) => {
    if (onError) onError(msg)
    else onDone()
  }

  es.onmessage = (e: MessageEvent<string>) => {
    let data: SseEvent
    try {
      data = JSON.parse(e.data) as SseEvent
    } catch {
      return
    }

    if (data.type === 'done') {
      // Close before the callback: if onDone throws, an open EventSource would
      // otherwise keep auto-reconnecting to a stream the server has torn down.
      es.close()
      onDone()
      return
    }

    if (data.type === 'error') {
      es.close()
      fail(String(data.message ?? 'Stream error'))
      return
    }

    onEvent(data)
  }

  es.onerror = () => {
    es.close()
    fail('SSE connection lost')
  }

  return () => es.close()
}
