import "./globals.css";
import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "OrgTwin",
  description: "A live 3D operations twin of your company, staffed by AI agents running on Vultr.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "OrgTwin", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#7cc8ff",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
