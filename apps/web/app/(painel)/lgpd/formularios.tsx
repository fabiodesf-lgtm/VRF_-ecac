"use client";

import { useState, useTransition } from "react";

import {
  AreaTexto,
  Aviso,
  Botao,
  Campo,
  Dialogo,
  Entrada,
  RodapeDialogo,
  Selecao,
  useAvisos,
} from "@/components/ui";
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

type Empresa = { id: string; nome: string };

/** Registro de um pedido de titular, com prazo. */
export function NovaSolicitacao({ empresas }: { empresas: Empresa[] }) {
  const [pendente, iniciar] = useTransition();
  const [aberto, setAberto] = useState(false);
  const avisos = useAvisos();
  const vazio = { tipo: "acesso", solicitante: "", empresaId: "", canal: "", detalhe: "" };
  const [campos, setCampos] = useState(vazio);

  if (!aberto) {
    return (
      <Botao tamanho="pequeno" onClick={() => setAberto(true)}>
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
          if (!r.ok) avisos.falha(r.erro);
          else {
            avisos.sucesso(r.mensagem ?? "Pedido registrado.");
            setAberto(false);
            setCampos(vazio);
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
            required
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
        <AreaTexto
          rows={2}
          value={campos.detalhe}
          onChange={(e) => setCampos({ ...campos, detalhe: e.target.value })}
        />
      </Campo>
      <div className="flex items-center gap-2">
        <Botao type="submit" tamanho="pequeno" disabled={pendente}>
          {pendente ? "salvando…" : "Registrar"}
        </Botao>
        <Botao
          type="button"
          variante="secundario"
          tamanho="pequeno"
          onClick={() => setAberto(false)}
        >
          Cancelar
        </Botao>
      </div>
    </form>
  );
}

/** Fecha um pedido guardando a resposta dada ao titular. */
export function AtenderSolicitacao({ solicitacaoId }: { solicitacaoId: string }) {
  const [pendente, iniciar] = useTransition();
  const [aberto, setAberto] = useState(false);
  const [resposta, setResposta] = useState("");
  const avisos = useAvisos();

  const enviar = (recusar: boolean) =>
    iniciar(async () => {
      const r = await atenderSolicitacao(solicitacaoId, resposta, recusar);
      if (!r.ok) avisos.falha(r.erro);
      else {
        avisos.sucesso(r.mensagem ?? "Pedido fechado.");
        setAberto(false);
        setResposta("");
      }
    });

  if (!aberto) {
    return (
      <Botao variante="secundario" tamanho="pequeno" onClick={() => setAberto(true)}>
        Responder
      </Botao>
    );
  }

  return (
    <div className="flex w-full flex-col gap-1.5 sm:w-80">
      <Campo label="O que foi respondido ao titular" obrigatorio>
        <AreaTexto
          rows={3}
          value={resposta}
          onChange={(e) => setResposta(e.target.value)}
          placeholder="É a prova de que o pedido foi atendido no prazo"
        />
      </Campo>
      <div className="flex flex-wrap gap-1.5">
        <Botao
          tamanho="pequeno"
          disabled={pendente || !resposta.trim()}
          onClick={() => enviar(false)}
        >
          {pendente ? "…" : "Atendido"}
        </Botao>
        <Botao
          variante="secundario"
          tamanho="pequeno"
          disabled={pendente || !resposta.trim()}
          onClick={() => enviar(true)}
        >
          Recusar com justificativa
        </Botao>
        <Botao
          variante="secundario"
          tamanho="pequeno"
          onClick={() => setAberto(false)}
        >
          Cancelar
        </Botao>
      </div>
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
export function ExportarDados({ empresas }: { empresas: Empresa[] }) {
  const [pendente, iniciar] = useTransition();
  const [empresaId, setEmpresaId] = useState("");
  const avisos = useAvisos();

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
              avisos.falha(r.erro);
              return;
            }
            const url = URL.createObjectURL(new Blob([r.json], { type: "application/json" }));
            const link = document.createElement("a");
            link.href = url;
            link.download = r.nome;
            link.click();
            URL.revokeObjectURL(url);
            avisos.sucesso(`Arquivo ${r.nome} gerado.`);
          })
        }
      >
        {pendente ? "montando…" : "Baixar JSON"}
      </Botao>
    </div>
  );
}

/** Anonimização de um cliente. Irreversível, e por isso pede o nome de volta. */
export function Anonimizar({
  empresas,
  ehAdmin,
}: {
  empresas: Empresa[];
  ehAdmin: boolean;
}) {
  const [pendente, iniciar] = useTransition();
  const [empresaId, setEmpresaId] = useState("");
  const [motivo, setMotivo] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [dialogo, setDialogo] = useState(false);
  const avisos = useAvisos();

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

      <Botao
        variante="perigo"
        disabled={pendente || !confirmado || !motivo.trim()}
        onClick={() => setDialogo(true)}
      >
        Anonimizar
      </Botao>

      <Dialogo
        aberto={dialogo}
        titulo="Anonimizar este cliente?"
        aoFechar={() => !pendente && setDialogo(false)}
        rodape={
          <RodapeDialogo
            aoCancelar={() => setDialogo(false)}
            aoConfirmar={() =>
              iniciar(async () => {
                const r = await anonimizar(empresaId, motivo);
                if (r.ok) {
                  avisos.sucesso(r.mensagem ?? "Cliente anonimizado.");
                  setEmpresaId("");
                  setMotivo("");
                  setConfirmacao("");
                  setDialogo(false);
                } else {
                  avisos.falha(r.erro);
                }
              })
            }
            rotuloConfirmar="Anonimizar — não tem volta"
            variante="perigo"
            pendente={pendente}
          />
        }
      >
        <p>
          Os dados de contato de <strong>{escolhida?.nome}</strong> e o conteúdo das conversas
          serão apagados, sem possibilidade de recuperação. O registro fiscal permanece.
        </p>
      </Dialogo>
    </div>
  );
}

/** Simulação e execução do expurgo por retenção. */
export function Retencao({ ehAdmin }: { ehAdmin: boolean }) {
  const [pendente, iniciar] = useTransition();
  const [resumo, setResumo] = useState<string | null>(null);
  const [dialogo, setDialogo] = useState(false);
  const avisos = useAvisos();

  function executar(simular: boolean) {
    iniciar(async () => {
      const r = await rodarRetencao(simular);
      if (r.ok) {
        setResumo(r.mensagem ?? null);
        if (!simular) avisos.sucesso(r.mensagem ?? "Expurgo executado.");
        setDialogo(false);
      } else {
        avisos.falha(r.erro);
      }
    });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Botao
          variante="secundario"
          tamanho="pequeno"
          disabled={pendente}
          onClick={() => executar(true)}
        >
          {pendente ? "…" : "Simular expurgo"}
        </Botao>

        {ehAdmin && (
          <Botao
            variante="perigo"
            tamanho="pequeno"
            disabled={pendente}
            onClick={() => setDialogo(true)}
          >
            Executar expurgo
          </Botao>
        )}
      </div>

      {resumo && <p className="text-xs text-tinta-fraca">{resumo}</p>}

      <Dialogo
        aberto={dialogo}
        titulo="Executar o expurgo agora?"
        aoFechar={() => !pendente && setDialogo(false)}
        rodape={
          <RodapeDialogo
            aoCancelar={() => setDialogo(false)}
            aoConfirmar={() => executar(false)}
            rotuloConfirmar="Executar — não tem volta"
            variante="perigo"
            pendente={pendente}
          />
        }
      >
        <p>
          O conteúdo que passou dos prazos é apagado de forma irreversível: corpo das mensagens
          e PDFs guardados. O registro de que existiram é preservado. Simule antes, se ainda não
          simulou.
        </p>
      </Dialogo>
    </div>
  );
}
