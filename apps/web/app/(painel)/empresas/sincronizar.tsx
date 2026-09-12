"use client";

import { useActionState } from "react";

import { Aviso, Botao } from "@/components/ui";
import type { ResultadoSincronizacao } from "./acoes";

/**
 * Botão de consulta ao e-CAC.
 *
 * Deixa explícito na interface que a consulta é cobrada: o botão normal respeita
 * a cota diária, e forçar é uma ação separada, com aviso. Esconder o custo atrás
 * de um botão só levaria alguém a clicar dez vezes sem saber o que está gastando.
 */
export function BotaoSincronizar({
  acao,
  cotaDisponivel,
}: {
  acao: (anterior: unknown, dados: FormData) => Promise<ResultadoSincronizacao>;
  cotaDisponivel: boolean;
}) {
  const [estado, enviar, enviando] = useActionState(
    acao,
    null as ResultadoSincronizacao | null,
  );

  return (
    <div className="space-y-3">
      <form action={enviar} className="flex flex-wrap items-center gap-2">
        <input type="hidden" name="forcar" value="" />
        <Botao type="submit" disabled={enviando}>
          {enviando ? "Consultando o e-CAC…" : "Sincronizar agora"}
        </Botao>
        {!cotaDisponivel && (
          <BotaoForcar acao={acao} />
        )}
      </form>

      {estado?.ok === false && <Aviso tom="alerta">{estado.erro}</Aviso>}

      {estado?.ok && estado.status === "pulado" && (
        <Aviso tom="atencao">{estado.mensagem}</Aviso>
      )}

      {estado?.ok && (estado.status === "aguardando" || estado.status === "expirado") && (
        <Aviso tom="atencao">
          {estado.mensagem} O protocolo ficou guardado; a próxima execução continua de onde
          parou.
        </Aviso>
      )}

      {estado?.ok && estado.status === "concluido" && (
        <Aviso tom={estado.secoesDesconhecidas.length > 0 ? "atencao" : "sucesso"}>
          <span className="block font-medium">{estado.mensagem}</span>
          <span className="block text-xs">
            {estado.debitosNovos} novo(s) · {estado.debitosAtualizados} atualizado(s) ·{" "}
            {estado.debitosResolvidos} resolvido(s)
            {estado.baixaConfianca > 0 && ` · ${estado.baixaConfianca} para conferência`}
          </span>
          {estado.secoesDesconhecidas.length > 0 && (
            <span className="mt-1 block text-xs">
              Seções não reconhecidas no relatório:{" "}
              {estado.secoesDesconhecidas.join("; ")}. Elas podem conter débitos que o
              sistema não está cobrando — veja a fila de atendimento.
            </span>
          )}
        </Aviso>
      )}
    </div>
  );
}

function BotaoForcar({
  acao,
}: {
  acao: (anterior: unknown, dados: FormData) => Promise<ResultadoSincronizacao>;
}) {
  const [, enviar, enviando] = useActionState(acao, null as ResultadoSincronizacao | null);

  return (
    <form action={enviar}>
      <input type="hidden" name="forcar" value="true" />
      <Botao
        type="submit"
        variante="secundario"
        tamanho="pequeno"
        disabled={enviando}
        title="Cada consulta ao Integra Contador é cobrada"
      >
        {enviando ? "Consultando…" : "Forçar (cota usada)"}
      </Botao>
    </form>
  );
}
