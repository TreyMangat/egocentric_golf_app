// PWA capture utilities — getUserMedia + MediaRecorder + wake lock.
//
// iOS Safari (14.3+) supports MediaRecorder but with constraints:
//  - Prefers MP4 (H.264 + AAC) over WebM
//  - 4K @ 60fps is unreliable; 1080p @ 60fps works on recent iPhones
//  - Camera stream must be acquired in response to a user gesture
//  - Permission can be re-prompted on route changes — keep the stream alive

export interface CaptureCapabilities {
  width: number;
  height: number;
  frameRate: number;
  mimeType: string;
}

const PREFERRED_MIMES = [
  // iOS Safari prefers these. Order matters — first supported wins.
  "video/mp4;codecs=h264,aac",
  "video/mp4;codecs=avc1.42E01E,mp4a.40.2",
  "video/mp4",
  // Other browsers
  "video/webm;codecs=h264,opus",
  "video/webm;codecs=vp9,opus",
  "video/webm;codecs=vp8,opus",
  "video/webm",
];

export function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "video/mp4";
  for (const m of PREFERRED_MIMES) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return ""; // browser default
}

export async function acquireStream(opts: {
  width?: number;
  height?: number;
  frameRate?: number;
} = {}): Promise<MediaStream> {
  const constraints: MediaStreamConstraints = {
    audio: {
      // Audio is critical — backend uses impact transient detection (3–5kHz).
      // Disable processing that might filter out the impact spike.
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
    video: {
      facingMode: { ideal: "environment" },
      width: { ideal: opts.width ?? 1920 },
      height: { ideal: opts.height ?? 1080 },
      frameRate: { ideal: opts.frameRate ?? 60, min: 30 },
    },
  };
  return await navigator.mediaDevices.getUserMedia(constraints);
}

export function streamCapabilities(stream: MediaStream): CaptureCapabilities {
  const vt = stream.getVideoTracks()[0];
  const settings = vt?.getSettings() ?? {};
  return {
    width: settings.width ?? 0,
    height: settings.height ?? 0,
    frameRate: settings.frameRate ?? 0,
    mimeType: pickMimeType(),
  };
}

// ─── recording ────────────────────────────────────────────────────────────────

export class Recorder {
  private chunks: Blob[] = [];
  private recorder: MediaRecorder | null = null;
  private startedAt: number = 0;
  readonly mimeType: string;

  constructor(private stream: MediaStream) {
    this.mimeType = pickMimeType();
  }

  start(bitsPerSecond = 8_000_000) {
    this.chunks = [];
    const opts: MediaRecorderOptions = { videoBitsPerSecond: bitsPerSecond };
    if (this.mimeType) opts.mimeType = this.mimeType;
    this.recorder = new MediaRecorder(this.stream, opts);
    this.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };
    this.startedAt = performance.now();
    // Pull data every second so a crash doesn't cost the whole session.
    this.recorder.start(1000);
  }

  elapsedMs(): number {
    return this.recorder ? Math.round(performance.now() - this.startedAt) : 0;
  }

  async stop(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      if (!this.recorder) return reject(new Error("not recording"));
      this.recorder.onstop = () => {
        const blob = new Blob(this.chunks, {
          type: this.recorder?.mimeType || this.mimeType || "video/mp4",
        });
        resolve(blob);
      };
      try {
        this.recorder.stop();
      } catch (e) {
        reject(e);
      }
    });
  }
}

// ─── wake lock ────────────────────────────────────────────────────────────────

interface WakeLockSentinel {
  release(): Promise<void>;
}

export async function requestWakeLock(): Promise<WakeLockSentinel | null> {
  const wl = (navigator as unknown as { wakeLock?: { request(t: string): Promise<WakeLockSentinel> } }).wakeLock;
  if (!wl) return null;
  try {
    return await wl.request("screen");
  } catch {
    return null;
  }
}

// ─── ids ──────────────────────────────────────────────────────────────────────

export function newSessionId(): string {
  // Sortable, human-readable, filesystem-safe.
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return [
    "session",
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
    pad(d.getHours()) + pad(d.getMinutes()),
  ].join("_");
}

export function detectPhoneModel(): string {
  const ua = navigator.userAgent || "";
  const m = ua.match(/iPhone|iPad|iPod/);
  if (!m) return "browser-unknown";
  return ua.match(/OS (\d+_\d+)/) ? `iPhone-iOS${RegExp.$1.replace("_", ".")}` : "iPhone";
}
