"use client";

import { useState, useTransition, type ReactNode } from "react";

import { useAvisos } from "./avisos";
import { Dialogo, RodapeDialogo } from "./dialogo";
import { Botao } from "./primitivos";

/**
 * O formato de retorno comum a todas as server actions do painel.
 *
 * Cada módulo declara o seu (`ResultadoAcao`, `Resultado`), e todos concordam no
 * discriminante `ok` e no campo de erro — é o suficiente para um componente
 * genérico tratar o retorno sem conhecer o módulo.
 */
export type ResultadoSimples =
  | { ok: true; mensagem?: string }
  | { ok: false; erro: string };

/**
 * Botão que dispara uma server action.
 *
 * Concentra o que estava reescrito em seis componentes: o `useTransition`, o
 * rótulo de pendência, o bloqueio do duplo clique e a exibição do retorno. A
 * confirmação é opcional e deliberadamente explícita em cada uso: resolver uma
 * tarefa não pede confirmação (a tarefa fica no histórico e pode ser reaberta),
 * mas emitir um DARF pede — o clique manda um documento de arrecadação para o
 * WhatsApp de um cliente e isso não se desfaz.
 */
export function BotaoAcao({
  acao,
  children,
  rotuloPendente = "…",
  confirmacao,
  mensagemSucesso,
  aoConcluir,
  variante = "secundario",
  tamanho = "normal",
  className = "",
  disabled = false,
  title,
}: {
  acao: () => Promise<ResultadoSimples>;
  children: ReactNode;
  rotuloPendente?: string;
  confirmacao?: {
    titulo: string;
    descricao: ReactNode;
    rotuloConfirmar?: string;
    variante?: "primario" | "perigo";
  };
  mensagemSucesso?: string;
  aoConcluir?: (resultado: ResultadoSimples) => void;
  variante?: "primario" | "secundario" | "perigo" | "sutil";
  tamanho?: "normal" | "pequeno";
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [pendente, iniciar] = useTransition();
  const [confirmando, setConfirmando] = useState(false);
  const avisos = useAvisos();

  function executar() {
    iniciar(async () => {
      const resultado = await acao();
      if (resultado.ok) {
        const texto = resultado.mensagem ?? mensagemSucesso;
        if (texto) avisos.sucesso(texto);
      } else {
        avisos.falha(resultado.erro);
      }
      setConfirmando(false);
      aoConcluir?.(resultado);
    });
  }

  return (
    <>
      <Botao
        type="button"
        variante={variante}
        tamanho={tamanho}
        className={className}
        title={title}
        disabled={disabled || pendente}
        onClick={() => (confirmacao ? setConfirmando(true) : executar())}
      >
        {pendente ? rotuloPendente : children}
      </Botao>

      {confirmacao && (
        <Dialogo
          aberto={confirmando}
          titulo={confirmacao.titulo}
          aoFechar={() => !pendente && setConfirmando(false)}
          rodape={
            <RodapeDialogo
              aoCancelar={() => setConfirmando(false)}
              aoConfirmar={executar}
              rotuloConfirmar={confirmacao.rotuloConfirmar}
              variante={confirmacao.variante}
              pendente={pendente}
            />
          }
        >
          {confirmacao.descricao}
        </Dialogo>
      )}
    </>
  );
}

/**
 * Interruptor para um estado booleano guardado no banco.
 *
 * Usa `aria-pressed` em vez de uma caixa de seleção porque não é campo de
 * formulário: o clique já grava. O rótulo diz o estado atual, não o que vai
 * acontecer — "avisos ativos" é informação; "desativar avisos" obrigaria a
 * pessoa a inferir o estado a partir do verbo.
 */
export function Alternador({
  ligado,
  acao,
  rotuloLigado,
  rotuloDesligado,
  confirmacaoParaDesligar,
  title,
  disabled = false,
}: {
  ligado: boolean;
  acao: (ligar: boolean) => Promise<ResultadoSimples>;
  rotuloLigado: string;
  rotuloDesligado: string;
  /** Texto do diálogo quando desligar é a ação de consequência. */
  confirmacaoParaDesligar?: { titulo: string; descricao: ReactNode };
  title?: string;
  disabled?: boolean;
}) {
  const [pendente, iniciar] = useTransition();
  const [confirmando, setConfirmando] = useState(false);
  const avisos = useAvisos();

  function executar() {
    iniciar(async () => {
      const resultado = await acao(!ligado);
      if (!resultado.ok) avisos.falha(resultado.erro);
      else if (resultado.mensagem) avisos.sucesso(resultado.mensagem);
      setConfirmando(false);
    });
  }

  const pedirConfirmacao = ligado && confirmacaoParaDesligar !== undefined;

  return (
    <>
      <button
        type="button"
        aria-pressed={ligado}
        disabled={disabled || pendente}
        title={title}
        onClick={() => (pedirConfirmacao ? setConfirmando(true) : executar())}
        className={`inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
          ligado
            ? "border-marca/20 bg-marca-clara text-marca hover:bg-marca-clara/70"
            : "border-atencao/20 bg-atencao-clara text-atencao hover:bg-atencao-clara/70"
        }`}
      >
        <span
          className={`size-1.5 rounded-full ${ligado ? "bg-marca" : "bg-atencao"}`}
          aria-hidden
        />
        {pendente ? "…" : ligado ? rotuloLigado : rotuloDesligado}
      </button>

      {confirmacaoParaDesligar && (
        <Dialogo
          aberto={confirmando}
          titulo={confirmacaoParaDesligar.titulo}
          aoFechar={() => !pendente && setConfirmando(false)}
          rodape={
            <RodapeDialogo
              aoCancelar={() => setConfirmando(false)}
              aoConfirmar={executar}
              rotuloConfirmar="Desativar"
              variante="perigo"
              pendente={pendente}
            />
          }
        >
          {confirmacaoParaDesligar.descricao}
        </Dialogo>
      )}
    </>
  );
}

/** Copia um texto curto (código de barras, protocolo) para a área de transferência. */
export function Copiar({ valor, rotulo = "copiar" }: { valor: string; rotulo?: string }) {
  const [copiado, setCopiado] = useState(false);
  const avisos = useAvisos();

  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(valor);
          setCopiado(true);
          setTimeout(() => setCopiado(false), 2000);
        } catch {
          // Área de transferência bloqueada (contexto não seguro, permissão
          // negada): dizer isso é melhor que um botão que não faz nada.
          avisos.falha("O navegador não permitiu copiar. Selecione o texto à mão.");
        }
      }}
      className="text-xs text-marca underline hover:text-marca-forte"
    >
      {copiado ? "copiado" : rotulo}
    </button>
  );
}
