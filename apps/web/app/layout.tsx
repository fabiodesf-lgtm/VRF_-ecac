import type { Metadata } from "next";

import { ProvedorAvisos } from "@/components/ui";
import "./globals.css";

export const metadata: Metadata = {
  title: "VRF e-CAC — Gestão de Débitos",
  description: "Acompanhamento de débitos vencidos e cobrança automática por WhatsApp.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        {/* A região de avisos mora na raiz para valer também no login, onde não
            há a casca do painel. */}
        <ProvedorAvisos>{children}</ProvedorAvisos>
      </body>
    </html>
  );
}
