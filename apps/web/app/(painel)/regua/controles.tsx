"use client";

import Link from "next/link";

import { Aviso, BotaoAcao } from "@/components/ui";
import { enviarAgora, ligarKillSwitch, recalcularRegua } from "./acoes";

/**
 * Controles da régua.
 *
 * O kill switch pede confirmação ao **desligar**, não ao ligar: ligar é sempre
 * seguro (para tudo), desligar é o que volta a mandar mensagem para clientes.
 * Recalcular não pede nada — não envia.
 */
export function Controles({
  killSwitchLigado,
  ehAdmin,
  janelaAberta,
  motivoJanela,
}: {
  killSwitchLigado: boolean;
  ehAdmin: boolean;
  janelaAberta: boolean;
  motivoJanela: string | null;
}) {
  return (
    <div className="space-y-4">
      {killSwitchLigado ? (
        <Aviso tom="alerta">
          <strong>Kill switch LIGADO.</strong> Nenhuma mensagem é criada nem enviada, por
          nenhum caminho — nem pelo agendador, nem pelos botões abaixo.
        </Aviso>
      ) : !janelaAberta ? (
        <Aviso tom="atencao">
          Fora da janela de envio ({motivoJanela}). Os avisos ficam pendentes e saem quando a
          janela abrir.{" "}
          <Link href="/configuracoes?aba=envio" className="font-medium underline">
            ajustar a janela
          </Link>
        </Aviso>
      ) : (
        <Aviso tom="sucesso">Janela de envio aberta.</Aviso>
      )}

      <div className="flex flex-wrap gap-2">
        <BotaoAcao
          acao={recalcularRegua}
          disabled={killSwitchLigado}
          title="Recalcula os avisos do dia. Não envia nada."
        >
          Recalcular avisos
        </BotaoAcao>

        <BotaoAcao
          acao={() => enviarAgora(false)}
          variante="primario"
          disabled={killSwitchLigado}
          title="Envia os avisos liberados, respeitando a janela."
          rotuloPendente="enviando…"
        >
          Enviar agora
        </BotaoAcao>

        {!janelaAberta && !killSwitchLigado && (
          <BotaoAcao
            acao={() => enviarAgora(true)}
            rotuloPendente="enviando…"
            confirmacao={{
              titulo: "Enviar fora da janela de horário?",
              descricao: (
                <p>
                  Cobrança fora do horário comercial aumenta o risco de restrição da conta do
                  WhatsApp. O kill switch e as demais travas continuam valendo.
                </p>
              ),
              rotuloConfirmar: "Enviar mesmo assim",
              variante: "perigo",
            }}
          >
            Enviar ignorando a janela
          </BotaoAcao>
        )}

        {ehAdmin && (
          <div className="ml-auto">
            <BotaoAcao
              acao={() => ligarKillSwitch(!killSwitchLigado)}
              variante={killSwitchLigado ? "primario" : "perigo"}
              confirmacao={
                killSwitchLigado
                  ? {
                      titulo: "Desligar o kill switch?",
                      descricao: (
                        <p>
                          Os envios automáticos voltam a acontecer dentro da janela, para todos
                          os clientes com avisos ativos.
                        </p>
                      ),
                      rotuloConfirmar: "Desligar",
                    }
                  : undefined
              }
            >
              {killSwitchLigado ? "Desligar kill switch" : "Parar tudo (kill switch)"}
            </BotaoAcao>
          </div>
        )}
      </div>

      {!ehAdmin && (
        <p className="text-xs text-tinta-fraca">
          O kill switch só pode ser acionado por um administrador.
        </p>
      )}
    </div>
  );
}
