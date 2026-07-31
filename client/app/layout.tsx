import type React from "react"
import type { Metadata } from "next"
import { Inter } from "next/font/google"
import "./globals.css"
import dynamic from "next/dynamic"
//lazy load AuthProvider only when needed (disableAuth is false)
const AuthProvider = dynamic(
  () =>
    import("@/context/AuthProvider").then((mod) => mod.AuthProvider)
);
const inter = Inter({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Agentics",
  description: "A multi-agent orchestration system for exploratory data analysis",
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const disableAuth = process.env.NEXT_PUBLIC_DISABLE_AUTH === "true";

  const providers = <>{children}</>;

  return (
    <html lang="en">
      <body className={inter.className}>
        {disableAuth ? providers : <AuthProvider>{providers}</AuthProvider>}
      </body>
    </html>
  );
}
