/** Shared WebSocket to /api/v1/ws/events with automatic reconnect. Components subscribe by event-type prefix. */
import { getToken } from "./api";

export interface AppEvent { type: string; data: any }
type Listener = (e: AppEvent) => void;

const listeners = new Set<Listener>();
let socket: WebSocket | null = null;
let retry = 0;
let wanted = 0;
const statusListeners = new Set<(connected: boolean) => void>();
export let connected = false;

function setConnected(v: boolean) { connected = v; statusListeners.forEach((l) => l(v)); }

function open() {
  if (socket || wanted === 0) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const token = getToken();
  socket = new WebSocket(`${proto}://${location.host}/api/v1/ws/events${token ? `?token=${encodeURIComponent(token)}` : ""}`);
  socket.onopen = () => { retry = 0; setConnected(true); };
  socket.onmessage = (m) => {
    try { const ev = JSON.parse(m.data) as AppEvent; if (ev.type !== "ping") listeners.forEach((l) => l(ev)); } catch { /* ignore malformed frames */ }
  };
  socket.onclose = () => {
    socket = null; setConnected(false);
    if (wanted > 0) setTimeout(open, Math.min(10000, 500 * 2 ** retry++));
  };
  socket.onerror = () => socket?.close();
}

export function subscribe(l: Listener): () => void {
  listeners.add(l); wanted++; open();
  return () => { listeners.delete(l); wanted--; if (wanted === 0) { socket?.close(); socket = null; } };
}
export function onConnection(l: (c: boolean) => void): () => void { statusListeners.add(l); l(connected); return () => { statusListeners.delete(l); }; }
