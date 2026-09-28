import "./globals.css";

import type { Metadata } from "next";

import { AuthProvider } from "@/lib/auth";

export const metadata: Metadata = {
  title: "Guruji Console",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
