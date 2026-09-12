"use client";

import { useState, useTransition } from "react";

import { AreaTexto, Aviso, Botao, Card, Etiqueta, useAvisos } from "@/components/ui";
import {
  GRUPOS_TEMPLATE,
  previaDoTemplate,
  variaveisInvalidas,
  variaveisPermitidas,
} from "@/lib/templates";
import { salvarTemplate } from "./acoes";

export type Template = {
  chave: string;
  titulo: string;
  corpo: string;
  descricao: string | null;
};

/**
 * Editor dos textos que vão para o cliente.
 *
 * A prévia fica ao lado, com dados de exemplo, porque estes textos não são
 * rótulos de interface: são mensagens de cobrança que chegam no WhatsApp de um
 * cliente, com negrito, emoji e quebras de linha que importam. Editar sem ver o
 * resultado é como mandar a carta sem ler.
 */
export function EditorTemplates({ templates }: { templates: Template[] }) {
  const porChave = new Map(templates.map((t) => [t.chave, t]));

  return (
    <div className="space-y-5">
      <Aviso tom="info">
        A substituição é simples, sem lógica: o que está entre <code>{"{{ }}"}</code> é trocado
        pelo dado do cliente. Uma variável que o sistema não fornece naquele texto{" "}
        <strong>faz o envio falhar</strong>, então o salvamento recusa antes de gravar.
      </Aviso>

      {GRUPOS_TEMPLATE.map((grupo) => {
        const doGrupo = grupo.chaves
          .map((c) => porChave.get(c))
          .filter((t): t is Template => t !== undefined);
        if (doGrupo.length === 0) return null;

        return (
          <Card key={grupo.titulo} titulo={grupo.titulo}>
            <ul className="divide-y divide-linha">
              {doGrupo.map((template) => (
                <li key={template.chave} className="py-3 first:pt-0 last:pb-0">
                  <UmTemplate template={template} />
                </li>
              ))}
            </ul>
          </Card>
        );
      })}

      {/* Texto cadastrado no banco que a tela não conhece: aparece para poder ser
          editado, em vez de ficar invisível. */}
      {(() => {
        const conhecidas = new Set(GRUPOS_TEMPLATE.flatMap((g) => g.chaves));
        const sobras = templates.filter((t) => !conhecidas.has(t.chave));
        if (sobras.length === 0) return null;
        return (
          <Card titulo="Outros textos">
            <ul className="divide-y divide-linha">
              {sobras.map((template) => (
                <li key={template.chave} className="py-3 first:pt-0 last:pb-0">
                  <UmTemplate template={template} />
                </li>
              ))}
            </ul>
          </Card>
        );
      })()}
    </div>
  );
}

function UmTemplate({ template }: { template: Template }) {
  const [aberto, setAberto] = useState(false);
  const [corpo, setCorpo] = useState(template.corpo);
  const [pendente, iniciar] = useTransition();
  const avisos = useAvisos();

  const permitidas = variaveisPermitidas(template.chave);
  const invalidas = variaveisInvalidas(template.chave, corpo);
  const mudou = corpo !== template.corpo;

  if (!aberto) {
    return (
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-tinta">{template.titulo}</p>
          {template.descricao && (
            <p className="mt-0.5 text-xs text-tinta-fraca">{template.descricao}</p>
          )}
          <p className="truncar-2 mt-1 whitespace-pre-wrap text-xs text-tinta-fraca/80">
            {template.corpo}
          </p>
        </div>
        <Botao variante="secundario" tamanho="pequeno" onClick={() => setAberto(true)}>
          Editar
        </Botao>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium text-tinta">{template.titulo}</p>
        <code className="text-xs text-tinta-fraca">{template.chave}</code>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-2">
          <AreaTexto
            value={corpo}
            onChange={(e) => setCorpo(e.target.value)}
            rows={14}
            className="font-mono text-xs"
            aria-label={`Texto de ${template.titulo}`}
          />
          <div className="flex flex-wrap gap-1">
            {permitidas.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setCorpo((atual) => `${atual}{{${v}}}`)}
                className="rounded border border-linha bg-fundo px-1.5 py-0.5 font-mono text-[0.7rem] text-tinta-fraca hover:bg-papel hover:text-tinta"
                title="Clique para inserir no fim do texto"
              >
                {`{{${v}}}`}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-tinta-fraca">
            Como o cliente vê
          </p>
          <div className="max-h-80 overflow-y-auto rounded-md border border-linha bg-fundo p-3">
            <p className="whitespace-pre-wrap text-sm text-tinta">
              {previaDoTemplate(corpo) || "—"}
            </p>
          </div>
          <p className="text-xs text-tinta-fraca">
            {corpo.length} caracteres. O negrito do WhatsApp usa *asterisco* e aparece como
            texto na prévia.
          </p>
        </div>
      </div>

      {invalidas.length > 0 && (
        <Aviso tom="alerta">
          Variável não disponível neste texto:{" "}
          {invalidas.map((v) => (
            <Etiqueta key={v} tom="alerta">{`{{${v}}}`}</Etiqueta>
          ))}{" "}
          — o envio falharia. Use uma das listadas abaixo do campo.
        </Aviso>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Botao
          tamanho="pequeno"
          disabled={pendente || !mudou || invalidas.length > 0}
          onClick={() =>
            iniciar(async () => {
              const r = await salvarTemplate(template.chave, corpo);
              if (r.ok) {
                avisos.sucesso(r.mensagem ?? "Texto salvo.");
                setAberto(false);
              } else {
                avisos.falha(r.erro);
              }
            })
          }
        >
          {pendente ? "salvando…" : "Salvar texto"}
        </Botao>
        <Botao
          variante="secundario"
          tamanho="pequeno"
          disabled={pendente}
          onClick={() => {
            setCorpo(template.corpo);
            setAberto(false);
          }}
        >
          Cancelar
        </Botao>
        {mudou && <span className="text-xs text-atencao">há alterações não salvas</span>}
      </div>
    </div>
  );
}
