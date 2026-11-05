import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "OrgTwin",
  description: "A live 3D operations twin of your company, staffed by AI agents running on Vultr.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
