"use client";

import { useState, useTransition } from "react";

import { Botao, Campo, Entrada } from "@/components/ui";
import { salvarReceita } from "./acoes";

/**
 * Formulário de conferência de um código de receita.
 *
 * Liberar um código é autorizar o sistema a emitir DARF daquela receita sem
 * revisão. O campo de conferência é obrigatório porque é ele que sustenta a
 * decisão: uma lista de códigos sem procedência não se explica meses depois.
 *
 * "Emite sozinho" vem **desmarcado**: o caminho de menor esforço tem de ser o
 * mais seguro.
 */
export function FormularioReceita() {
  const [pendente, iniciar] = useTransition();
  const [aberto, setAberto] = useState(false);
  const [resposta, setResposta] = useState<{ ok: boolean; texto: string } | null>(null);
  const [campos, setCampos] = useState({
    codigo: "",
    descricao: "",
    conferencia: "",
    tetoValor: "",
    ativo: false,
  });

  if (!aberto) {
    return (
      <Botao variante="secundario" onClick={() => setAberto(true)} className="px-2 py-1 text-xs">
        Conferir um código
      </Botao>
    );
  }

  return (
    <form
      className="mt-3 space-y-3 rounded-md border border-linha bg-fundo p-3"
      onSubmit={(e) => {
        e.preventDefault();
        iniciar(async () => {
          const r = await salvarReceita(campos);
          setResposta({ ok: r.ok, texto: r.ok ? (r.mensagem ?? "Salvo.") : r.erro });
          if (r.ok) {
            setCampos({
              codigo: "",
              descricao: "",
              conferencia: "",
              tetoValor: "",
              ativo: false,
            });
            setAberto(false);
          }
        });
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Campo label="Código de receita" obrigatorio>
          <Entrada
            value={campos.codigo}
            onChange={(e) => setCampos({ ...campos, codigo: e.target.value })}
            inputMode="numeric"
            placeholder="2089"
          />
        </Campo>
        <Campo label="Descrição" obrigatorio>
          <Entrada
            value={campos.descricao}
            onChange={(e) => setCampos({ ...campos, descricao: e.target.value })}
            placeholder="IRPJ — lucro presumido"
          />
        </Campo>
      </div>

      <Campo
        label="Como foi conferido"
        obrigatorio
        dica="Quem conferiu, contra o quê e quando. É o que sustenta emitir sem revisão."
      >
        <Entrada
          value={campos.conferencia}
          onChange={(e) => setCampos({ ...campos, conferencia: e.target.value })}
          placeholder="conferido contra DARF emitido no e-CAC por Fulano em 11/09/2026"
        />
      </Campo>

      <Campo
        label="Teto próprio (opcional)"
        dica="Vazio usa o teto geral. Preenchido, permite soltar só esta receita."
      >
        <Entrada
          value={campos.tetoValor}
          onChange={(e) => setCampos({ ...campos, tetoValor: e.target.value })}
          inputMode="decimal"
          placeholder="5000,00"
        />
      </Campo>

      <label className="flex items-start gap-2 text-sm text-tinta">
        <input
          type="checkbox"
          checked={campos.ativo}
          onChange={(e) => setCampos({ ...campos, ativo: e.target.checked })}
          className="mt-0.5"
        />
        <span>
          Emitir DARF desta receita <strong>sem aprovação</strong>, respeitando o teto.
        </span>
      </label>

      <div className="flex items-center gap-2">
        <Botao type="submit" disabled={pendente} className="px-3 py-1.5 text-xs">
          {pendente ? "salvando…" : "Salvar"}
        </Botao>
        <Botao
          type="button"
          variante="secundario"
          disabled={pendente}
          onClick={() => setAberto(false)}
          className="px-3 py-1.5 text-xs"
        >
          Cancelar
        </Botao>
        {resposta && !resposta.ok && <span className="text-xs text-alerta">{resposta.texto}</span>}
      </div>
    </form>
  );
}
