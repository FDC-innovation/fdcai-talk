import "./globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Toaster } from "react-hot-toast";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";

const inter = Inter({ subsets: ["latin"] });

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
const SITE_TITLE =
  "FDCAI — Real-Time AI Avatar with Lip-Sync & Voice Cloning";
const SITE_DESCRIPTION =
  "Open-source AI talking avatar platform: upload a photo, clone a voice, and have " +
  "real-time conversations with photorealistic lip-sync video. Powered by Claude, " +
  "Whisper, Chatterbox TTS, and MuseTalk. Self-host everything.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: SITE_TITLE,
    template: "%s · FDCAI",
  },
  description: SITE_DESCRIPTION,
  applicationName: "FDCAI",
  keywords: [
    "AI avatar",
    "talking avatar",
    "talking head",
    "digital human",
    "lip sync",
    "voice cloning",
    "text to speech",
    "speech to text",
    "real-time avatar",
    "AI companion",
    "Claude",
    "Whisper",
    "MuseTalk",
    "Chatterbox TTS",
    "open source avatar",
    "self-hosted AI",
  ],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "FDCAI",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
};

// Structured data (schema.org SoftwareApplication) — lets search engines show
// a rich result card instead of a bare blue link.
const JSON_LD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "FDCAI",
  applicationCategory: "MultimediaApplication",
  operatingSystem: "Web, Linux, Docker",
  description: SITE_DESCRIPTION,
  url: SITE_URL,
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
  softwareHelp: "https://github.com/PunithVT/ai-avatar-system",
  sameAs: ["https://github.com/PunithVT/ai-avatar-system"],
  featureList: [
    "Real-time lip-sync avatar video (MuseTalk)",
    "Zero-shot voice cloning in 23 languages (Chatterbox)",
    "Streaming LLM conversations (Claude / GPT)",
    "Whisper speech-to-text",
    "Self-hostable with Docker",
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }}
        />
      </head>
      <body className={inter.className}>
        {/* Skip link — appears only on keyboard focus, lets users bypass the nav */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[100]
                     focus:px-4 focus:py-2 focus:rounded-lg focus:bg-primary-600 focus:text-white
                     focus:shadow-glow focus:outline-none focus:ring-2 focus:ring-primary-300"
        >
          Skip to main content
        </a>
        <QueryProvider>
          <ErrorBoundary>
            <div id="main-content">{children}</div>
          </ErrorBoundary>
          <Toaster
            position="top-right"
            toastOptions={{
              className: "dark:bg-gray-800 dark:text-gray-100",
            }}
          />
        </QueryProvider>
      </body>
    </html>
  );
}
