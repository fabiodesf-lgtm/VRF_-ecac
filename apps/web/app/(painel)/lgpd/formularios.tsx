"use client";

import { useState, useTransition } from "react";

import { Aviso, Botao, Campo, Entrada, Selecao } from "@/components/ui";
import {
  anonimizar,
  atenderSolicitacao,
  exportarDados,
  registrarSolicitacao,
  rodarRetencao,
} from "./acoes";

const TIPOS: [string, string][] = [
  ["acesso", "Acesso — quer saber o que guardamos"],
  ["portabilidade", "Portabilidade — quer levar os dados"],
  ["correcao", "Correção — dado errado"],
  ["eliminacao", "Eliminação — quer que apaguemos"],
  ["revogacao_consentimento", "Revogação de consentimento"],
];

/** Registro de um pedido de titular, com prazo. */
export function NovaSolicitacao({ empresas }: { empresas: { id: string; nome: string }[] }) {
  const [pendente, iniciar] = useTransition();
  const [aberto, setAberto] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [campos, setCampos] = useState({
    tipo: "acesso",
    solicitante: "",
    empresaId: "",
    canal: "",
    detalhe: "",
  });

  if (!aberto) {
    return (
      <Botao onClick={() => setAberto(true)} className="px-3 py-1.5 text-xs">
        Registrar pedido
      </Botao>
    );
  }

  return (
    <form
      className="space-y-3 rounded-md border border-linha bg-fundo p-3"
      onSubmit={(e) => {
        e.preventDefault();
        iniciar(async () => {
          const r = await registrarSolicitacao(campos);
          if (!r.ok) setErro(r.erro);
          else {
            setErro(null);
            setAberto(false);
            setCampos({ tipo: "acesso", solicitante: "", empresaId: "", canal: "", detalhe: "" });
          }
        });
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Campo label="O que o titular pediu" obrigatorio>
          <Selecao
            value={campos.tipo}
            onChange={(e) => setCampos({ ...campos, tipo: e.target.value })}
          >
            {TIPOS.map(([valor, rotulo]) => (
              <option key={valor} value={valor}>
                {rotulo}
              </option>
            ))}
          </Selecao>
        </Campo>
        <Campo label="Quem pediu" obrigatorio dica="Nome, telefone ou e-mail de quem procurou">
          <Entrada
            value={campos.solicitante}
            onChange={(e) => setCampos({ ...campos, solicitante: e.target.value })}
          />
        </Campo>
        <Campo label="Empresa" dica="Se o pedido é de um cliente cadastrado">
          <Selecao
            value={campos.empresaId}
            onChange={(e) => setCampos({ ...campos, empresaId: e.target.value })}
          >
            <option value="">— não vinculado —</option>
            {empresas.map((e) => (
              <option key={e.id} value={e.id}>
                {e.nome}
              </option>
            ))}
          </Selecao>
        </Campo>
        <Campo label="Por onde chegou" dica="WhatsApp, e-mail, telefone, presencial">
          <Entrada
            value={campos.canal}
            onChange={(e) => setCampos({ ...campos, canal: e.target.value })}
          />
        </Campo>
      </div>
      <Campo label="O que exatamente foi pedido">
        <Entrada
          value={campos.detalhe}
          onChange={(e) => setCampos({ ...campos, detalhe: e.target.value })}
        />
      </Campo>
      <div className="flex items-center gap-2">
        <Botao type="submit" disabled={pendente} className="px-3 py-1.5 text-xs">
          {pendente ? "salvando…" : "Registrar"}
        </Botao>
        <Botao
          type="button"
          variante="secundario"
          onClick={() => setAberto(false)}
          className="px-3 py-1.5 text-xs"
        >
          Cancelar
        </Botao>
        {erro && <span className="text-xs text-alerta">{erro}</span>}
      </div>
    </form>
  );
}

/** Fecha um pedido guardando a resposta dada ao titular. */
export function AtenderSolicitacao({ solicitacaoId }: { solicitacaoId: string }) {
  const [pendente, iniciar] = useTransition();
  const [aberto, setAberto] = useState(false);
  const [resposta, setResposta] = useState("");
  const [erro, setErro] = useState<string | null>(null);

  if (!aberto) {
    return (
      <Botao variante="secundario" onClick={() => setAberto(true)} className="px-2 py-1 text-xs">
        Responder
      </Botao>
    );
  }

  const enviar = (recusar: boolean) =>
    iniciar(async () => {
      const r = await atenderSolicitacao(solicitacaoId, resposta, recusar);
      if (!r.ok) setErro(r.erro);
      else {
        setErro(null);
        setAberto(false);
        setResposta("");
      }
    });

  return (
    <div className="flex w-full flex-col gap-1.5 sm:w-80">
      <Entrada
        value={resposta}
        onChange={(e) => setResposta(e.target.value)}
        placeholder="O que foi respondido ao titular"
        className="py-1 text-xs"
      />
      <div className="flex flex-wrap gap-1.5">
        <Botao disabled={pendente} onClick={() => enviar(false)} className="px-2 py-1 text-xs">
          {pendente ? "…" : "Atendido"}
        </Botao>
        <Botao
          variante="secundario"
          disabled={pendente}
          onClick={() => enviar(true)}
          className="px-2 py-1 text-xs"
        >
          Recusar
        </Botao>
        <Botao
          variante="secundario"
          onClick={() => setAberto(false)}
          className="px-2 py-1 text-xs"
        >
          Cancelar
        </Botao>
      </div>
      {erro && <span className="text-xs text-alerta">{erro}</span>}
    </div>
  );
}

/**
 * Baixa o pacote de dados de uma empresa.
 *
 * O JSON vem do servidor e é salvo pelo navegador. Nada é gravado em disco no
 * servidor: um pacote com a situação fiscal inteira de um cliente não deve ficar
 * esquecido num diretório.
 */
export function ExportarDados({ empresas }: { empresas: { id: string; nome: string }[] }) {
  const [pendente, iniciar] = useTransition();
  const [empresaId, setEmpresaId] = useState("");
  const [erro, setErro] = useState<string | null>(null);

  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="min-w-56 flex-1">
        <Campo label="Empresa">
          <Selecao value={empresaId} onChange={(e) => setEmpresaId(e.target.value)}>
            <option value="">— escolha —</option>
            {empresas.map((e) => (
              <option key={e.id} value={e.id}>
                {e.nome}
              </option>
            ))}
          </Selecao>
        </Campo>
      </div>
      <Botao
        disabled={pendente || !empresaId}
        onClick={() =>
          iniciar(async () => {
            const r = await exportarDados(empresaId);
            if (!r.ok) {
              setErro(r.erro);
              return;
            }
            setErro(null);
            const url = URL.createObjectURL(new Blob([r.json], { type: "application/json" }));
            const link = document.createElement("a");
            link.href = url;
            link.download = r.nome;
            link.click();
            URL.revokeObjectURL(url);
          })
        }
        className="px-3 py-2 text-xs"
      >
        {pendente ? "montando…" : "Baixar JSON"}
      </Botao>
      {erro && <span className="text-xs text-alerta">{erro}</span>}
    </div>
  );
}

/** Anonimização de um cliente. Irreversível, e por isso pede o nome de volta. */
export function Anonimizar({
  empresas,
  ehAdmin,
}: {
  empresas: { id: string; nome: string }[];
  ehAdmin: boolean;
}) {
  const [pendente, iniciar] = useTransition();
  const [empresaId, setEmpresaId] = useState("");
  const [motivo, setMotivo] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [resposta, setResposta] = useState<{ ok: boolean; texto: string } | null>(null);

  if (!ehAdmin) {
    return (
      <p className="text-sm text-tinta-fraca">
        Anonimizar um cliente é ação de administrador — é irreversível.
      </p>
    );
  }

  const escolhida = empresas.find((e) => e.id === empresaId);
  const confirmado = escolhida !== undefined && confirmacao.trim() === escolhida.nome;

  return (
    <div className="space-y-3">
      <Aviso tom="alerta">
        <strong>Não tem volta.</strong> WhatsApp, e-mail e o conteúdo das conversas são apagados.
        O CNPJ, a razão social e os débitos permanecem — apagá-los poria o escritório em falta
        com a Receita, e o art. 16 da LGPD preserva o que a lei obriga a guardar.
      </Aviso>

      <div className="grid gap-3 sm:grid-cols-2">
        <Campo label="Empresa">
          <Selecao
            value={empresaId}
            onChange={(e) => {
              setEmpresaId(e.target.value);
              setConfirmacao("");
            }}
          >
            <option value="">— escolha —</option>
            {empresas.map((e) => (
              <option key={e.id} value={e.id}>
                {e.nome}
              </option>
            ))}
          </Selecao>
        </Campo>
        <Campo label="Motivo" obrigatorio dica="Fica registrado na auditoria">
          <Entrada value={motivo} onChange={(e) => setMotivo(e.target.value)} />
        </Campo>
      </div>

      {escolhida && (
        <Campo
          label="Confirme digitando a razão social"
          dica={`Digite exatamente: ${escolhida.nome}`}
        >
          <Entrada value={confirmacao} onChange={(e) => setConfirmacao(e.target.value)} />
        </Campo>
      )}

      <div className="flex items-center gap-2">
        <Botao
          variante="perigo"
          disabled={pendente || !confirmado || !motivo.trim()}
          onClick={() =>
            iniciar(async () => {
              const r = await anonimizar(empresaId, motivo);
              setResposta({ ok: r.ok, texto: r.ok ? (r.mensagem ?? "Feito.") : r.erro });
              if (r.ok) {
                setEmpresaId("");
                setMotivo("");
                setConfirmacao("");
              }
            })
          }
          className="px-3 py-2 text-xs"
        >
          {pendente ? "anonimizando…" : "Anonimizar"}
        </Botao>
        {resposta && (
          <span className={`text-xs ${resposta.ok ? "text-marca" : "text-alerta"}`}>
            {resposta.texto}
          </span>
        )}
      </div>
    </div>
  );
}

/** Simulação e execução do expurgo por retenção. */
export function Retencao({ ehAdmin }: { ehAdmin: boolean }) {
  const [pendente, iniciar] = useTransition();
  const [resposta, setResposta] = useState<{ ok: boolean; texto: string } | null>(null);
  const [confirmando, setConfirmando] = useState(false);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Botao
          variante="secundario"
          disabled={pendente}
          onClick={() =>
            iniciar(async () => {
              const r = await rodarRetencao(true);
              setResposta({ ok: r.ok, texto: r.ok ? (r.mensagem ?? "") : r.erro });
            })
          }
          className="px-3 py-1.5 text-xs"
        >
          {pendente ? "…" : "Simular expurgo"}
        </Botao>

        {ehAdmin &&
          (confirmando ? (
            <>
              <Botao
                variante="perigo"
                disabled={pendente}
                onClick={() =>
                  iniciar(async () => {
                    const r = await rodarRetencao(false);
                    setResposta({ ok: r.ok, texto: r.ok ? (r.mensagem ?? "") : r.erro });
                    setConfirmando(false);
                  })
                }
                className="px-3 py-1.5 text-xs"
              >
                Confirmar — não tem volta
              </Botao>
              <Botao
                variante="secundario"
                onClick={() => setConfirmando(false)}
                className="px-3 py-1.5 text-xs"
              >
                Cancelar
              </Botao>
            </>
          ) : (
            <Botao
              variante="secundario"
              onClick={() => setConfirmando(true)}
              className="px-3 py-1.5 text-xs"
            >
              Executar expurgo
            </Botao>
          ))}
      </div>
      {resposta && (
        <p className={`text-xs ${resposta.ok ? "text-tinta-fraca" : "text-alerta"}`}>
          {resposta.texto}
        </p>
      )}
    </div>
  );
}
