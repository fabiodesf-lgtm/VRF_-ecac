"use client";

import { useEffect } from "react";

import { Aviso, Botao, Card } from "@/components/ui";

/**
 * Erro não tratado numa tela do painel.
 *
 * A mensagem original **não** vai para a tela: erro de banco costuma carregar
 * nome de tabela, fragmento de consulta e por vezes dado de cliente, e esta tela
 * mostra situação fiscal de terceiros. Ela vai para o console do servidor, que é
 * onde o diagnóstico acontece — o mesmo critério que as server actions já usam.
 */
export default function Erro({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("erro na tela do painel:", error);
  }, [error]);

  return (
    <Card titulo="Algo falhou nesta tela">
      <div className="space-y-4">
        <Aviso tom="alerta">
          A página não conseguiu carregar. Se o problema persistir, confira em{" "}
          <strong>Operação</strong> se o worker e o banco estão de pé.
        </Aviso>
        {error.digest && (
          <p className="text-xs text-tinta-fraca">
            Código para o log do servidor: <code className="tabular">{error.digest}</code>
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Botao onClick={() => reset()}>Tentar de novo</Botao>
        </div>
      </div>
    </Card>
  );
}
