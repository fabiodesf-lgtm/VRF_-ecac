import Link from "next/link";

import {
  Aviso,
  Cabecalho,
  Card,
  Etiqueta,
  Indicador,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { formatarCnpj, formatarData } from "@/lib/validacao";
import { Anonimizar, AtenderSolicitacao, ExportarDados, NovaSolicitacao, Retencao } from "./formularios";

export const dynamic = "force-dynamic";

const TIPO_ROTULO: Record<string, string> = {
  acesso: "acesso",
  portabilidade: "portabilidade",
  correcao: "correção",
  eliminacao: "eliminação",
  revogacao_consentimento: "revogação de consentimento",
};

export default async function Lgpd() {
  const supabase = await criarClienteServidor();
  const atual = await usuarioAtual();
  const ehAdmin = atual?.perfil?.papel === "admin";

  const [{ data: solicitacoes }, { data: empresas }, { data: config }] = await Promise.all([
    supabase
      .from("solicitacoes_lgpd")
      .select(
        "id, tipo, status, solicitante, canal, detalhe, resposta, prazo_em, atendido_em, created_at, empresas(id, razao_social)",
      )
      .order("created_at", { ascending: false })
      .limit(100),
    supabase
      .from("empresas")
      .select("id, cnpj, razao_social, anonimizado_em, encerrado_em, opt_out_em")
      .order("razao_social"),
    supabase.from("configuracoes_publicas").select("chave, valor").like("chave", "lgpd.%"),
  ]);

  const pedidos = solicitacoes ?? [];
  const abertos = pedidos.filter((s) => s.status === "aberta" || s.status === "em_andamento");
  const hoje = new Date().toISOString().slice(0, 10);
  const atrasados = abertos.filter((s) => s.prazo_em && s.prazo_em < hoje);

  const todas = empresas ?? [];
  const paraEscolher = todas
    .filter((e) => !e.anonimizado_em)
    .map((e) => ({ id: e.id, nome: e.razao_social }));
  const anonimizadas = todas.filter((e) => e.anonimizado_em);

  const valores = new Map((config ?? []).map((c) => [c.chave ?? "", c.valor]));
  const retencaoAtiva = valores.get("lgpd.retencao_ativa") === true;

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="LGPD"
        descricao="Pedidos de titular, exportação de dados e política de retenção."
        acao={
          <Link href="/configuracoes?aba=lgpd" className="text-sm text-marca underline">
            configurar os prazos
          </Link>
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <Indicador
          rotulo="Pedidos em aberto"
          valor={String(abertos.length)}
          detalhe={`prazo de ${abertos.length === 1 ? "resposta" : "resposta"}: 15 dias`}
        />
        <Indicador
          rotulo="Fora do prazo"
          valor={String(atrasados.length)}
          detalhe={atrasados.length > 0 ? "exposição direta do escritório" : "nenhum"}
          tom={atrasados.length > 0 ? "destaque" : "neutro"}
        />
        <Indicador
          rotulo="Clientes anonimizados"
          valor={String(anonimizadas.length)}
          detalhe="contato apagado, registro fiscal preservado"
        />
      </div>

      {atrasados.length > 0 && (
        <Aviso tom="alerta">
          <strong>
            {atrasados.length} pedido(s) fora do prazo.
          </strong>{" "}
          Pedido de titular com prazo vencido é exposição direta do escritório.
        </Aviso>
      )}

      <Card titulo={`Pedidos de titular (${abertos.length} em aberto)`}>
        <div className="mb-3">
          <NovaSolicitacao empresas={paraEscolher} />
        </div>

        {pedidos.length === 0 ? (
          <Vazio titulo="Nenhum pedido registrado">
            Pedidos de acesso, portabilidade ou eliminação chegam por WhatsApp, e-mail ou
            telefone. Registrá-los aqui é o que permite ao escritório mostrar que respondeu
            dentro do prazo.
          </Vazio>
        ) : (
          <ul className="divide-y divide-linha">
            {pedidos.map((s) => {
              const atrasado =
                (s.status === "aberta" || s.status === "em_andamento") &&
                !!s.prazo_em &&
                s.prazo_em < hoje;
              return (
                <li
                  key={s.id}
                  className="flex flex-wrap items-start gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Etiqueta tom={atrasado ? "alerta" : "info"}>
                        {TIPO_ROTULO[s.tipo] ?? s.tipo}
                      </Etiqueta>
                      {s.status === "atendida" && <Etiqueta tom="sucesso">atendida</Etiqueta>}
                      {s.status === "recusada" && <Etiqueta tom="neutro">recusada</Etiqueta>}
                      {atrasado && <Etiqueta tom="alerta">fora do prazo</Etiqueta>}
                    </div>
                    <p className="mt-1 text-sm font-medium text-tinta">{s.solicitante}</p>
                    {s.detalhe && <p className="text-sm text-tinta-fraca">{s.detalhe}</p>}
                    {s.resposta && (
                      <p className="mt-0.5 text-sm text-tinta-fraca">
                        <strong>Resposta:</strong> {s.resposta}
                      </p>
                    )}
                    <p className="mt-1 text-xs text-tinta-fraca">
                      {[
                        s.empresas?.razao_social,
                        s.canal,
                        `recebido em ${formatarData(s.created_at)}`,
                        s.prazo_em && `prazo ${formatarData(s.prazo_em)}`,
                        s.atendido_em && `atendido em ${formatarData(s.atendido_em)}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  {(s.status === "aberta" || s.status === "em_andamento") && (
                    <AtenderSolicitacao solicitacaoId={s.id} />
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card titulo="Exportar dados de um cliente">
        <p className="mb-3 text-sm text-tinta-fraca">
          Atende os direitos de acesso e portabilidade. O arquivo é gerado na hora e baixado
          pelo navegador — nada fica guardado no servidor. A exportação é registrada em
          auditoria.
        </p>
        <ExportarDados empresas={paraEscolher} />
      </Card>

      <Card titulo="Retenção">
        <p className="mb-3 text-sm text-tinta-fraca">
          O expurgo apaga o <strong>conteúdo</strong> do que passou do prazo — corpo das
          mensagens, PDFs guardados — e preserva o <strong>registro</strong> de que existiu.
          Simular conta o que sairia, sem apagar nada.
        </p>

        {!retencaoAtiva && (
          <div className="mb-3">
            <Aviso tom="atencao">
              A retenção automática está <strong>desligada</strong>. Ela nasce assim de
              propósito: apagar é irreversível, e os prazos devem ser conferidos com o
              jurídico antes do primeiro expurgo. Quando estiverem definidos, ligue em{" "}
              <Link href="/configuracoes?aba=lgpd" className="font-medium underline">
                Configurações → LGPD
              </Link>
              .
            </Aviso>
          </div>
        )}

        <Retencao ehAdmin={ehAdmin} />

        <div className="mt-4">
          <Tabela>
            <thead>
              <tr>
                <Th>Prazo</Th>
                <Th alinhar="direita">Dias</Th>
              </tr>
            </thead>
            <tbody>
              {(config ?? [])
                .filter((c) => c.chave && c.chave !== "lgpd.retencao_ativa")
                .map((c) => (
                  <tr key={c.chave}>
                    <Td>
                      {(c.chave ?? "").replace("lgpd.retencao_", "").replace(/_/g, " ")}
                    </Td>
                    <Td alinhar="direita">{String(c.valor)}</Td>
                  </tr>
                ))}
            </tbody>
          </Tabela>
        </div>
      </Card>

      <Card titulo="Anonimizar um cliente">
        <Anonimizar empresas={paraEscolher} ehAdmin={ehAdmin} />
      </Card>

      {anonimizadas.length > 0 && (
        <Card titulo={`Já anonimizadas (${anonimizadas.length})`}>
          <Tabela>
            <thead>
              <tr>
                <Th>Razão social</Th>
                <Th>CNPJ</Th>
                <Th>Anonimizada em</Th>
              </tr>
            </thead>
            <tbody>
              {anonimizadas.map((e) => (
                <tr key={e.id}>
                  <Td>{e.razao_social}</Td>
                  <Td className="tabular">{formatarCnpj(e.cnpj)}</Td>
                  <Td className="tabular">{formatarData(e.anonimizado_em)}</Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        </Card>
      )}
    </div>
  );
}
