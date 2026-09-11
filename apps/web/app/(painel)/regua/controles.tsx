"use client";

import { useState, useTransition } from "react";

import { Aviso, Botao } from "@/components/ui";
import { enviarAgora, ligarKillSwitch, recalcularRegua } from "./acoes";
import type { Resultado } from "./acoes";

/**
 * Controles da régua.
 *
 * O kill switch pede confirmação ao **desligar**, não ao ligar: ligar é sempre
 * seguro (para tudo), desligar é o que volta a mandar mensagem para clientes.
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
  const [pendente, iniciar] = useTransition();
  const [resultado, setResultado] = useState<Resultado | null>(null);

  function executar(acao: () => Promise<Resultado>) {
    iniciar(async () => setResultado(await acao()));
  }

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
          janela abrir.
        </Aviso>
      ) : (
        <Aviso tom="sucesso">Janela de envio aberta.</Aviso>
      )}

      {resultado && (
        <Aviso tom={resultado.ok ? "sucesso" : "alerta"}>
          {resultado.ok ? resultado.mensagem : resultado.erro}
        </Aviso>
      )}

      <div className="flex flex-wrap gap-2">
        <Botao
          variante="secundario"
          disabled={pendente || killSwitchLigado}
          onClick={() => executar(recalcularRegua)}
          title="Recalcula os avisos do dia. Não envia nada."
        >
          {pendente ? "…" : "Recalcular avisos"}
        </Botao>

        <Botao
          disabled={pendente || killSwitchLigado}
          onClick={() => executar(() => enviarAgora(false))}
          title="Envia os avisos liberados, respeitando a janela."
        >
          {pendente ? "…" : "Enviar agora"}
        </Botao>

        {!janelaAberta && !killSwitchLigado && (
          <Botao
            variante="secundario"
            disabled={pendente}
            onClick={() => {
              if (
                confirm(
                  "Enviar fora da janela de horário? Cobrança fora do horário comercial " +
                    "aumenta o risco de restrição da conta do WhatsApp.",
                )
              ) {
                executar(() => enviarAgora(true));
              }
            }}
          >
            Enviar ignorando a janela
          </Botao>
        )}

        {ehAdmin && (
          <Botao
            variante={killSwitchLigado ? "primario" : "perigo"}
            disabled={pendente}
            onClick={() => {
              // Confirmação ao DESLIGAR: ligar é sempre seguro, desligar volta a
              // mandar mensagem para clientes.
              if (
                killSwitchLigado &&
                !confirm(
                  "Desligar o kill switch? Os envios automáticos voltam a acontecer " +
                    "dentro da janela.",
                )
              ) {
                return;
              }
              executar(() => ligarKillSwitch(!killSwitchLigado));
            }}
            className="ml-auto"
          >
            {killSwitchLigado ? "Desligar kill switch" : "Parar tudo (kill switch)"}
          </Botao>
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
