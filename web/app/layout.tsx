import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Virector — AI Video Director",
  description: "Direct consistent AI video from character and world references.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
