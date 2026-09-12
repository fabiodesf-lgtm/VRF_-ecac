import {
  Busca,
  Cabecalho,
  Card,
  Etiqueta,
  Filtro,
  LimparFiltros,
  Paginacao,
  Vazio,
} from "@/components/ui";
import { termoParaBusca } from "@/lib/consulta";
import { criarClienteServidor } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const POR_PAGINA = 50;
const CAMPOS_FILTRO = ["q", "entidade", "ator", "dias"];

const ENTIDADES = [
  "empresas",
  "procuradores",
  "debitos",
  "conversas",
  "tarefas",
  "darfs",
  "receitas_darf",
  "configuracoes",
  "regua",
  "solicitacoes_lgpd",
  "mensagens",
];

type Filtros = {
  q?: string;
  entidade?: string;
  ator?: string;
  dias?: string;
  pagina?: string;
};

/**
 * O registro do que foi feito no sistema, e por quem.
 *
 * O painel e o worker já escrevem em `audit_log` desde o início — aprovação de
 * DARF, liberação de código de receita, kill switch, anonimização, confirmação de
 * procuração. Sem esta tela, nada disso podia ser lido sem acesso ao banco, o que
 * torna a auditoria inútil justamente para quem ela protege.
 *
 * A tabela é append-only por RLS: não há nada a editar aqui, só a ler.
 */
export default async function Auditoria({
  searchParams,
}: {
  searchParams: Promise<Filtros>;
}) {
  const filtros = await searchParams;
  const pagina = Math.max(1, Number(filtros.pagina ?? 1) || 1);
  const supabase = await criarClienteServidor();

  let consulta = supabase
    .from("audit_log")
    .select(
      "id, acao, entidade, entidade_id, actor_tipo, actor_id, antes, depois, created_at, profiles(nome, email)",
      { count: "exact" },
    );

  const termo = termoParaBusca(filtros.q);
  if (termo) consulta = consulta.ilike("acao", `%${termo}%`);
  if (filtros.entidade && ENTIDADES.includes(filtros.entidade)) {
    consulta = consulta.eq("entidade", filtros.entidade);
  }
  if (filtros.ator === "usuario" || filtros.ator === "worker" || filtros.ator === "sistema") {
    consulta = consulta.eq("actor_tipo", filtros.ator);
  }

  const dias = Number(filtros.dias ?? 0);
  if (dias > 0) {
    consulta = consulta.gte(
      "created_at",
      new Date(Date.now() - dias * 86_400_000).toISOString(),
    );
  }

  const inicio = (pagina - 1) * POR_PAGINA;
  const { data, count, error } = await consulta
    .order("created_at", { ascending: false })
    .range(inicio, inicio + POR_PAGINA - 1);

  const linhas = data ?? [];
  const total = count ?? linhas.length;
  const temFiltro = CAMPOS_FILTRO.some((c) => filtros[c as keyof Filtros]);

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="Auditoria"
        descricao="Quem fez o quê, quando. O registro não pode ser alterado nem apagado pelo painel."
      />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Busca rotulo="Buscar na ação" placeholder="darf, kill_switch, procuracao…" />
          <Filtro
            campo="entidade"
            rotulo="Entidade"
            rotuloVazio="todas"
            opcoes={ENTIDADES.map((e) => ({ valor: e, rotulo: e.replace(/_/g, " ") }))}
          />
          <Filtro
            campo="ator"
            rotulo="Quem agiu"
            opcoes={[
              { valor: "usuario", rotulo: "pessoa do escritório" },
              { valor: "worker", rotulo: "worker (automação)" },
              { valor: "sistema", rotulo: "sistema" },
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
              { valor: "90", rotulo: "últimos 90 dias" },
            ]}
          />
        </div>
        <div className="mt-3">
          <LimparFiltros campos={CAMPOS_FILTRO} />
        </div>
      </Card>

      <Card descricao={`${total} registro(s)`} titulo="Registros">
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && linhas.length === 0 ? (
          <Vazio titulo={temFiltro ? "Nada com esses filtros" : "Nenhum registro ainda"}>
            As ações do painel e do worker passam a aparecer aqui conforme acontecem.
          </Vazio>
        ) : (
          <>
            <ul className="divide-y divide-linha">
              {linhas.map((l) => (
                <li key={l.id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Etiqueta tom={l.actor_tipo === "usuario" ? "info" : "neutro"}>
                      {l.actor_tipo}
                    </Etiqueta>
                    <code className="text-sm font-medium text-tinta">{l.acao}</code>
                    <span className="text-xs text-tinta-fraca">{l.entidade}</span>
                  </div>
                  <p className="mt-1 text-xs text-tinta-fraca">
                    {[
                      l.profiles?.nome || l.profiles?.email || null,
                      l.entidade_id && `id ${l.entidade_id}`,
                      new Date(l.created_at).toLocaleString("pt-BR", {
                        timeZone: "America/Sao_Paulo",
                      }),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  {(l.antes || l.depois) && (
                    // `<details>` em vez de um painel aberto: a maioria das linhas
                    // se lê pela ação, e mostrar dois JSONs por linha afogaria a
                    // lista.
                    <details className="mt-1">
                      <summary className="cursor-pointer text-xs text-marca underline">
                        ver o que mudou
                      </summary>
                      <div className="mt-2 grid gap-2 lg:grid-cols-2">
                        {l.antes && <Json titulo="antes" valor={l.antes} />}
                        {l.depois && <Json titulo="depois" valor={l.depois} />}
                      </div>
                    </details>
                  )}
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

function Json({ titulo, valor }: { titulo: string; valor: unknown }) {
  return (
    <div className="overflow-x-auto rounded-md border border-linha bg-fundo p-2">
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-tinta-fraca">
        {titulo}
      </p>
      <pre className="whitespace-pre-wrap break-all text-xs text-tinta">
        {JSON.stringify(valor, null, 2)}
      </pre>
    </div>
  );
}
