import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VRF e-CAC — Gestão de Débitos",
  description: "Acompanhamento de débitos vencidos e cobrança automática por WhatsApp.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
