import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { site } from "@/site";

export const metadata: Metadata = {
  title: `${site.brand}: your personal AI Vedic astrologer on WhatsApp`,
  description:
    "Chat with an AI Vedic astrologer who remembers your kundli. Voice notes in Hindi, English and Hinglish. No app to install.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site">
          <div className="wrap">
            <Link href="/" className="brand">
              {site.brand}
            </Link>
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer className="site">
          <div className="wrap">
            <nav aria-label="Legal">
              <Link href="/privacy/">Privacy</Link>
              <Link href="/terms/">Terms</Link>
              <Link href="/refunds/">Refunds &amp; cancellation</Link>
              <Link href="/contact/">Contact &amp; grievance officer</Link>
              <Link href="/delete-my-data/">Delete my data</Link>
            </nav>
            <div>
              © {new Date().getFullYear()} {site.company.legalName}. For guidance and
              entertainment; not a substitute for medical, legal or financial advice.
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
