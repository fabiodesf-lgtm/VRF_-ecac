"use client";

import { useEffect } from "react";

/**
 * Erro que escapa até a raiz — inclusive falha no próprio layout.
 *
 * Precisa trazer `<html>` e `<body>` porque substitui o layout raiz, e não pode
 * depender do kit de UI nem de CSS carregado: é o último recurso.
 */
export default function ErroGlobal({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("erro global no painel:", error);
  }, [error]);

  return (
    <html lang="pt-BR">
      <body
        style={{
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#f7f8fa",
          color: "#14181f",
        }}
      >
        <main style={{ maxWidth: "28rem", padding: "1.5rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "1.125rem", fontWeight: 600 }}>O painel não carregou</h1>
          <p style={{ marginTop: "0.5rem", fontSize: "0.875rem", color: "#5b6472" }}>
            Recarregue a página. Se continuar assim, verifique se o banco e o worker
            estão no ar.
          </p>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              marginTop: "1.5rem",
              padding: "0.5rem 0.75rem",
              borderRadius: "0.375rem",
              border: 0,
              background: "#14532d",
              color: "#fff",
              fontSize: "0.875rem",
              fontWeight: 500,
            }}
          >
            Tentar de novo
          </button>
        </main>
      </body>
    </html>
  );
}
