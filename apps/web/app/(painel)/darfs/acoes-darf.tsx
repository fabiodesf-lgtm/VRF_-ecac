"use client";

import { useState, useTransition } from "react";

import {
  AreaTexto,
  Botao,
  BotaoAcao,
  Campo,
  Dialogo,
  RodapeDialogo,
  useAvisos,
} from "@/components/ui";
import { aprovar, descartar } from "./acoes";

/**
 * Botões de um DARF na fila de aprovação.
 *
 * Aprovar pede confirmação, diferente de resolver uma tarefa: o clique manda um
 * documento de arrecadação para o WhatsApp de um cliente, e isso não se desfaz.
 * Descartar pede o motivo, porque o registro de por que um pedido do cliente não
 * virou DARF é o que responde a pergunta dele depois.
 */
export function AcoesDarf({ darfId, resumo }: { darfId: string; resumo: string }) {
  return (
    <div className="flex shrink-0 flex-wrap items-start justify-end gap-1.5">
      <Descartar darfId={darfId} />
      <BotaoAcao
        acao={() => aprovar(darfId)}
        variante="primario"
        tamanho="pequeno"
        rotuloPendente="emitindo…"
        confirmacao={{
          titulo: "Emitir e enviar este DARF?",
          descricao: (
            <p>
              O documento de <strong>{resumo}</strong> será emitido no SICALC e enviado ao
              cliente pelo WhatsApp. Não há como desfazer o envio.
            </p>
          ),
          rotuloConfirmar: "Emitir e enviar",
        }}
      >
        Aprovar e emitir
      </BotaoAcao>
    </div>
  );
}

function Descartar({ darfId }: { darfId: string }) {
  const [aberto, setAberto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [pendente, iniciar] = useTransition();
  const avisos = useAvisos();

  return (
    <>
      <Botao variante="secundario" tamanho="pequeno" onClick={() => setAberto(true)}>
        Descartar
      </Botao>

      <Dialogo
        aberto={aberto}
        titulo="Descartar este pedido de DARF?"
        aoFechar={() => !pendente && setAberto(false)}
        rodape={
          <RodapeDialogo
            aoCancelar={() => setAberto(false)}
            aoConfirmar={() =>
              iniciar(async () => {
                const r = await descartar(darfId, motivo.trim());
                if (r.ok) {
                  avisos.sucesso(r.mensagem ?? "Pedido descartado.");
                  setAberto(false);
                  setMotivo("");
                } else {
                  avisos.falha(r.erro);
                }
              })
            }
            rotuloConfirmar="Descartar"
            variante="perigo"
            pendente={pendente}
            confirmarDesabilitado={motivo.trim().length === 0}
          />
        }
      >
        <div className="space-y-3">
          <p className="text-tinta-fraca">
            O pedido fica registrado como recusado, com o motivo — o cliente pediu alguma coisa,
            e o registro é o que responde a ele depois.
          </p>
          <Campo label="Por que não emitir?" obrigatorio>
            <AreaTexto
              value={motivo}
              onChange={(e) => setMotivo(e.target.value)}
              rows={3}
              placeholder="Ex.: débito já pago, valor a apurar, cliente desistiu"
            />
          </Campo>
        </div>
      </Dialogo>
    </>
  );
}
