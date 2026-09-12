import Link from "next/link";
import { Suspense } from "react";

import {
  Aviso,
  Cabecalho,
  Card,
  Descricao,
  Esqueleto,
  Etiqueta,
  Indicador,
  Item,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { FAIXAS, FAIXAS_ORDENADAS, type FaixaAtraso } from "@/lib/faixas";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarData, formatarMoeda } from "@/lib/validacao";
import { saudeWorker } from "@/lib/worker";

export const dynamic = "force-dynamic";

export default async function Inicio() {
  const supabase = await criarClienteServidor();

  const [faixas, resumos, tarefas, consultas] = await Promise.all([
    supabase.from("resumo_faixas").select("*"),
    supabase
      .from("empresas_resumo")
      .select("*")
      .eq("status", "ativo")
      .order("total_cobravel", { ascending: false }),
    supabase
      .from("tarefas")
      .select("id, tipo, titulo, created_at, empresas(razao_social)")
      .in("status", ["aberta", "em_andamento"])
      .order("created_at", { ascending: false })
      .limit(6),
    supabase
      .from("sitfis_consultas")
      .select("id, status, parse_status, iniciado_em")
      .order("iniciado_em", { ascending: false })
      .limit(50),
  ]);

  const porFaixa = new Map<FaixaAtraso, { qtd: number; total: number; cobravel: number }>();
  for (const linha of faixas.data ?? []) {
    if (!linha.faixa_atraso) continue;
    porFaixa.set(linha.faixa_atraso, {
      qtd: Number(linha.qtd_debitos ?? 0),
      total: Number(linha.total ?? 0),
      cobravel: Number(linha.total_cobravel ?? 0),
    });
  }

  const empresas = resumos.data ?? [];
  const comDebito = empresas.filter((e) => Number(e.qtd_debitos ?? 0) > 0);
  const totalAberto = empresas.reduce((s, e) => s + Number(e.total_aberto ?? 0), 0);
  const totalCobravel = empresas.reduce((s, e) => s + Number(e.total_cobravel ?? 0), 0);
  const totalConferir = empresas.reduce((s, e) => s + Number(e.qtd_conferir ?? 0), 0);

  // Empresas nunca sincronizadas ou com a última sincronização com problema:
  // são as que o sistema não está de fato acompanhando.
  const semSincronizacao = empresas.filter((e) => !e.ultima_sincronizacao_em);
  const comProblema = empresas.filter(
    (e) =>
      e.ultima_sincronizacao_status === "erro" ||
      (e.ultima_sincronizacao_status === "concluido" &&
        e.ultima_sincronizacao_parse !== "ok"),
  );

  const maiorTotal = Math.max(1, ...FAIXAS_ORDENADAS.map((f) => porFaixa.get(f)?.total ?? 0));

  return (
    <div className="space-y-6">
      <Cabecalho
        titulo="Visão geral"
        descricao="Situação dos débitos e da operação de cobrança."
      />

      <Aviso tom="atencao">
        <strong>Sistema completo, travas fechadas.</strong> O leitor do relatório do e-CAC só foi
        conferido contra a seção de débito comum (SIEF), o envio pelo WhatsApp começa em modo de
        teste e toda emissão de DARF está em aprovação manual. O que falta conferir está em{" "}
        <Link href="/operacao" className="font-medium underline">
          Operação
        </Link>
        .
      </Aviso>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Indicador
          rotulo="Total em aberto"
          valor={formatarMoeda(totalAberto)}
          detalhe={`${formatarMoeda(totalCobravel)} cobrável`}
        />
        <Indicador
          rotulo="Empresas com débito"
          valor={String(comDebito.length)}
          detalhe={`de ${empresas.length} ativa(s)`}
        />
        <Indicador
          rotulo="Débitos a conferir"
          valor={String(totalConferir)}
          detalhe={totalConferir > 0 ? "fora da cobrança automática" : "nenhum"}
          tom={totalConferir > 0 ? "destaque" : "neutro"}
        />
        <Indicador
          rotulo="Consultas ao e-CAC"
          valor={String((consultas.data ?? []).length)}
          detalhe="últimas 50"
        />
      </div>

      {(semSincronizacao.length > 0 || comProblema.length > 0) && (
        <Aviso tom="alerta">
          {semSincronizacao.length > 0 && (
            <span className="block">
              {semSincronizacao.length} empresa(s) ativa(s) nunca foram sincronizadas — o
              sistema não conhece os débitos delas.{" "}
              <Link href="/empresas?situacao=sem_sincronizacao" className="font-medium underline">
                ver quais
              </Link>
            </span>
          )}
          {comProblema.length > 0 && (
            <span className="block">
              {comProblema.length} empresa(s) com a última consulta em erro ou relatório
              parcialmente lido.
            </span>
          )}
        </Aviso>
      )}

      <Card titulo="Débitos por faixa de atraso">
        {totalAberto === 0 ? (
          <Vazio titulo="Nenhum débito em aberto">
            Sincronize as empresas com o e-CAC para o sistema conhecer os débitos.
          </Vazio>
        ) : (
          <div className="space-y-2">
            {FAIXAS_ORDENADAS.map((faixa) => {
              const dados = porFaixa.get(faixa) ?? { qtd: 0, total: 0, cobravel: 0 };
              const largura = Math.round((dados.total / maiorTotal) * 100);
              return (
                <div key={faixa} className="flex items-center gap-3 text-sm">
                  <span className="w-16 shrink-0 text-xs font-medium text-tinta-fraca">
                    {FAIXAS[faixa].curto}
                  </span>
                  {/* Barra proporcional: comparar faixas é mais rápido visualmente
                      do que ler sete números. A leitura por voz vem do texto ao
                      lado, então a barra é decoração. */}
                  <span
                    className="h-5 shrink-0 rounded-sm"
                    style={{
                      width: `${Math.max(largura, dados.total > 0 ? 2 : 0)}%`,
                      minWidth: dados.total > 0 ? "4px" : 0,
                      backgroundColor:
                        FAIXAS[faixa].tom === "alerta"
                          ? "var(--color-alerta)"
                          : FAIXAS[faixa].tom === "atencao"
                            ? "var(--color-atencao)"
                            : "var(--color-linha)",
                    }}
                    aria-hidden
                  />
                  {dados.total > 0 ? (
                    <Link
                      href={`/debitos?faixa=${faixa}`}
                      className="tabular text-tinta underline decoration-linha hover:decoration-tinta"
                    >
                      {formatarMoeda(dados.total)}
                    </Link>
                  ) : (
                    <span className="tabular text-tinta-fraca">—</span>
                  )}
                  <span className="text-xs text-tinta-fraca">
                    {dados.qtd} débito{dados.qtd === 1 ? "" : "s"}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          titulo="Maiores devedores"
          acao={
            <Link href="/debitos" className="text-xs text-marca underline">
              ver todos
            </Link>
          }
        >
          {comDebito.length === 0 ? (
            <Vazio titulo="Nenhuma empresa com débito" />
          ) : (
            <Tabela>
              <thead>
                <tr>
                  <Th>Empresa</Th>
                  <Th alinhar="direita">Atraso</Th>
                  <Th alinhar="direita">Cobrável</Th>
                </tr>
              </thead>
              <tbody>
                {comDebito.slice(0, 8).map((e) => (
                  <tr key={e.empresa_id}>
                    <Td>
                      <Link
                        href={`/empresas/${e.empresa_id}`}
                        className="text-marca underline"
                      >
                        {e.razao_social}
                      </Link>
                      {!e.avisos_ativos && (
                        <Etiqueta tom="atencao">avisos off</Etiqueta>
                      )}
                    </Td>
                    <Td alinhar="direita">
                      {e.maior_atraso_dias === null ? "—" : `${e.maior_atraso_dias} d`}
                    </Td>
                    <Td alinhar="direita">{formatarMoeda(e.total_cobravel)}</Td>
                  </tr>
                ))}
              </tbody>
            </Tabela>
          )}
        </Card>

        <div className="space-y-4">
          <Card
            titulo="Fila de tarefas"
            acao={
              <Link href="/atendimento" className="text-xs text-marca underline">
                ver fila
              </Link>
            }
          >
            {(tarefas.data ?? []).length === 0 ? (
              <Vazio titulo="Nenhuma tarefa aberta" />
            ) : (
              <ul className="divide-y divide-linha">
                {(tarefas.data ?? []).map((t) => (
                  <li key={t.id} className="py-2 first:pt-0 last:pb-0">
                    <div className="flex items-start gap-2">
                      <Etiqueta tom="atencao">{t.tipo.replace(/_/g, " ")}</Etiqueta>
                      <span className="text-sm text-tinta">{t.titulo}</span>
                    </div>
                    {t.empresas?.razao_social && (
                      <span className="mt-0.5 block text-xs text-tinta-fraca">
                        {t.empresas.razao_social} · {formatarData(t.created_at)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* O worker fica fora do Promise.all acima: ele tem timeout de cinco
              segundos e, estando fora do ar, prenderia a tela inteira esperando
              por um cartão. Aqui o banco pinta a página e só este bloco espera. */}
          <Suspense fallback={<CartaoEstadoCarregando />}>
            <EstadoDoSistema />
          </Suspense>
        </div>
      </div>
    </div>
  );
}

async function EstadoDoSistema() {
  const saude = await saudeWorker();

  return (
    <Card titulo="Estado do sistema">
      <Descricao>
        <Item rotulo="Worker">
          {saude ? (
            <Etiqueta tom={saude.ok ? "sucesso" : "alerta"}>
              {saude.ok ? "no ar" : "banco inacessível"}
            </Etiqueta>
          ) : (
            <Etiqueta tom="alerta">inacessível</Etiqueta>
          )}
        </Item>
        <Item rotulo="Integra Contador">
          {saude?.integra_provider === "serpro" ? (
            <Etiqueta tom="sucesso">API SERPRO</Etiqueta>
          ) : (
            <Etiqueta tom="atencao">mock (API não contratada)</Etiqueta>
          )}
        </Item>
        <Item rotulo="Armazenamento">
          <Etiqueta>{saude?.storage_backend ?? "—"}</Etiqueta>
        </Item>
      </Descricao>
      {!saude && (
        <p className="mt-3 text-xs text-tinta-fraca">
          O painel não conseguiu falar com o worker. Envio de certificado e consultas ao
          e-CAC ficam indisponíveis até ele voltar.
        </p>
      )}
    </Card>
  );
}

function CartaoEstadoCarregando() {
  return (
    <Card titulo="Estado do sistema">
      <div className="space-y-3">
        <Esqueleto className="h-3 w-full" />
        <Esqueleto className="h-3 w-4/5" />
        <Esqueleto className="h-3 w-3/5" />
      </div>
    </Card>
  );
}
