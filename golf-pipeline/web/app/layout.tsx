import "./globals.css";
import type { Metadata, Viewport } from "next";
import { PwaInit } from "@/components/PwaInit";

export const metadata: Metadata = {
  title: "Swing Pipeline",
  description: "Personal golf swing capture and analysis",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Swing",
  },
  icons: {
    apple: "/apple-touch-icon.png",
    icon: [
      { url: "/icon-192.png", sizes: "192x192" },
      { url: "/icon-512.png", sizes: "512x512" },
    ],
  },
};

export const viewport: Viewport = {
  themeColor: "#0a0d0f",
  viewportFit: "cover",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="bg-ink-950">
      <body className="font-sans antialiased min-h-screen">
        <PwaInit />
        {/* The capture page is `position: fixed inset-0 z-50` so it overlays
            this chrome on phones. On desktop the chrome stays visible above
            the (unused) capture viewport. */}
        <header className="border-b border-ink-800/80">
          <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
            <a href="/" className="flex items-center gap-3 group">
              <span className="w-2 h-2 rounded-full bg-accent shadow-[0_0_12px_2px_theme(colors.accent)]" />
              <span className="font-display text-lg tracking-tight">Swing Pipeline</span>
              <span className="font-mono text-[10px] tracking-wider2 text-ink-400 uppercase mt-0.5">
                v0.1
              </span>
            </a>
            <nav className="font-mono text-xs uppercase tracking-wider2 text-ink-300 flex gap-5">
              <a href="/" className="hover:text-accent">Sessions</a>
              <a href="/swings" className="hover:text-accent">Swings</a>
              <a href="/capture" className="text-accent hover:opacity-80">● Capture</a>
            </nav>
          </div>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
