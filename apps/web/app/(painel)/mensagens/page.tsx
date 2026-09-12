import Link from "next/link";

import {
  Busca,
  Cabecalho,
  Card,
  Etiqueta,
  Filtro,
  Indicador,
  LimparFiltros,
  Paginacao,
  Vazio,
} from "@/components/ui";
import { termoParaBusca } from "@/lib/consulta";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarData, formatarWhatsapp } from "@/lib/validacao";

export const dynamic = "force-dynamic";

const POR_PAGINA = 50;
const CAMPOS_FILTRO = ["q", "direcao", "status", "dias"];

const TONS: Record<string, "sucesso" | "atencao" | "alerta" | "info" | "neutro"> = {
  entregue: "sucesso",
  lida: "sucesso",
  enviada: "info",
  fila: "atencao",
  falhou: "alerta",
};

type Filtros = {
  q?: string;
  direcao?: string;
  status?: string;
  dias?: string;
  pagina?: string;
};

/**
 * Log das mensagens trocadas com os clientes.
 *
 * Era a tela que faltava para responder "o cliente recebeu?" sem abrir o
 * WhatsApp: o cartão de últimas mensagens da régua mostrava trinta linhas, sem
 * filtro e sem como achar a conversa de um cliente específico.
 *
 * É deliberadamente somente leitura. Reenviar uma mensagem exige rota nova no
 * worker, e o caminho que existe — "Enviar agora", na régua — passa por todas as
 * travas de janela, teto e kill switch. Um botão de reenvio aqui seria um segundo
 * caminho de envio, sem elas.
 */
export default async function Mensagens({
  searchParams,
}: {
  searchParams: Promise<Filtros>;
}) {
  const filtros = await searchParams;
  const pagina = Math.max(1, Number(filtros.pagina ?? 1) || 1);
  const supabase = await criarClienteServidor();

  let consulta = supabase
    .from("mensagens")
    .select(
      "id, direcao, whatsapp, corpo, status, erro, enviado_em, created_at, empresas(id, razao_social)",
      { count: "exact" },
    );

  const termo = termoParaBusca(filtros.q);
  if (termo) consulta = consulta.ilike("corpo", `%${termo}%`);
  if (filtros.direcao === "entrada" || filtros.direcao === "saida") {
    consulta = consulta.eq("direcao", filtros.direcao);
  }
  if (filtros.status && filtros.status in TONS) {
    consulta = consulta.eq("status", filtros.status as "fila");
  }

  const dias = Number(filtros.dias ?? 0);
  if (dias > 0) {
    const desde = new Date(Date.now() - dias * 86_400_000).toISOString();
    consulta = consulta.gte("created_at", desde);
  }

  const inicio = (pagina - 1) * POR_PAGINA;
  const { data, count, error } = await consulta
    .order("created_at", { ascending: false })
    .range(inicio, inicio + POR_PAGINA - 1);

  const mensagens = data ?? [];
  const total = count ?? mensagens.length;
  const falhadas = mensagens.filter((m) => m.status === "falhou").length;
  const naFila = mensagens.filter((m) => m.status === "fila").length;
  const temFiltro = CAMPOS_FILTRO.some((c) => filtros[c as keyof Filtros]);

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="Mensagens"
        descricao="Tudo que saiu e entrou pelo WhatsApp, do mais recente para o mais antigo."
        acao={
          <Link href="/regua" className="text-sm text-marca underline">
            ir para a régua
          </Link>
        }
      />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Busca rotulo="Buscar no texto" placeholder="trecho da mensagem" />
          <Filtro
            campo="direcao"
            rotulo="Direção"
            opcoes={[
              { valor: "saida", rotulo: "enviadas pelo sistema" },
              { valor: "entrada", rotulo: "recebidas do cliente" },
            ]}
          />
          <Filtro
            campo="status"
            rotulo="Situação"
            opcoes={[
              { valor: "fila", rotulo: "na fila" },
              { valor: "enviada", rotulo: "enviada" },
              { valor: "entregue", rotulo: "entregue" },
              { valor: "lida", rotulo: "lida" },
              { valor: "falhou", rotulo: "falhou" },
            ]}
          />
          <Filtro
            campo="dias"
            rotulo="Período"
            rotuloVazio="tudo"
            opcoes={[
              { valor: "1", rotulo: "últimas 24 h" },
              { valor: "7", rotulo: "últimos 7 dias" },
              { valor: "30", rotulo: "últimos 30 dias" },
            ]}
          />
        </div>
        <div className="mt-3">
          <LimparFiltros campos={CAMPOS_FILTRO} />
        </div>
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        <Indicador rotulo="Mensagens no filtro" valor={String(total)} />
        <Indicador
          rotulo="Falhadas nesta página"
          valor={String(falhadas)}
          tom={falhadas > 0 ? "destaque" : "neutro"}
        />
        <Indicador
          rotulo="Ainda na fila"
          valor={String(naFila)}
          detalhe={naFila > 0 ? "saem no próximo despacho" : undefined}
        />
      </div>

      <Card>
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && mensagens.length === 0 ? (
          <Vazio
            titulo={temFiltro ? "Nenhuma mensagem com esses filtros" : "Nenhuma mensagem trocada"}
          >
            {temFiltro ? (
              <Link href="/mensagens" className="text-marca underline">
                limpar filtros
              </Link>
            ) : (
              "As mensagens aparecem aqui conforme a régua despacha e os clientes respondem."
            )}
          </Vazio>
        ) : (
          <>
            <ul className="divide-y divide-linha">
              {mensagens.map((m) => (
                <li key={m.id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-tinta-fraca">
                    <Etiqueta tom={m.direcao === "entrada" ? "info" : "neutro"}>
                      {m.direcao === "entrada" ? "recebida" : "enviada"}
                    </Etiqueta>
                    {m.empresas?.id ? (
                      <Link
                        href={`/empresas/${m.empresas.id}`}
                        className="text-marca underline"
                      >
                        {m.empresas.razao_social}
                      </Link>
                    ) : (
                      <span className="text-atencao">número não cadastrado</span>
                    )}
                    <span className="tabular">{formatarWhatsapp(m.whatsapp)}</span>
                    <span className="tabular">{formatarData(m.enviado_em ?? m.created_at)}</span>
                    <Etiqueta tom={TONS[m.status] ?? "neutro"}>{m.status}</Etiqueta>
                  </div>
                  {/* whitespace-pre-wrap preserva as quebras de linha da mensagem,
                      que é como o cliente a vê no WhatsApp. */}
                  <p className="mt-1 whitespace-pre-wrap text-sm text-tinta">
                    {m.corpo.length > 600 ? `${m.corpo.slice(0, 600)}…` : m.corpo}
                  </p>
                  {m.erro && <p className="mt-1 text-xs text-alerta">{m.erro}</p>}
                </li>
              ))}
            </ul>
            <Paginacao pagina={pagina} tamanho={POR_PAGINA} total={total} />
          </>
        )}
      </Card>
    </div>
  );
}
