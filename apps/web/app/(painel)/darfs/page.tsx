import Link from "next/link";

import {
  Aviso,
  Cabecalho,
  Card,
  Copiar,
  Etiqueta,
  Indicador,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { formatarCnpj, formatarData, formatarMoeda } from "@/lib/validacao";
import { AcoesDarf } from "./acoes-darf";
import { FormularioReceita } from "./receita";

export const dynamic = "force-dynamic";

const TONS: Record<string, "sucesso" | "atencao" | "alerta" | "info"> = {
  enviado: "sucesso",
  gerado: "info",
  aguardando_aprovacao: "atencao",
  falhou: "alerta",
};

export default async function Darfs() {
  const supabase = await criarClienteServidor();
  const atual = await usuarioAtual();
  const ehAdmin = atual?.perfil?.papel === "admin";

  const [{ data: darfs }, { data: receitas }, { data: config }] = await Promise.all([
    supabase
      .from("darfs")
      .select(
        "id, status, data_consolidacao, valor_total, valor_principal, motivo_aprovacao, erro, created_at, enviado_em, codigo_barras, pdf_storage_path, empresas(id, cnpj, razao_social), debitos(descricao, codigo_receita, saldo_devedor)",
      )
      .order("created_at", { ascending: false })
      .limit(100),
    supabase.from("receitas_darf").select("codigo, descricao, ativo, teto_valor").order("codigo"),
    supabase
      .from("configuracoes_publicas")
      .select("chave, valor")
      .in("chave", ["darf.auto_emitir", "darf.teto_valor", "regua.kill_switch"]),
  ]);

  const todos = darfs ?? [];
  const fila = todos.filter((d) => d.status === "aguardando_aprovacao");
  const historico = todos.filter((d) => d.status !== "aguardando_aprovacao");
  const liberadas = (receitas ?? []).filter((r) => r.ativo);
  const enviados = historico.filter((d) => d.status === "enviado");
  const falhados = historico.filter((d) => d.status === "falhou");

  const valores = new Map((config ?? []).map((c) => [c.chave ?? "", c.valor]));
  const teto = Number(valores.get("darf.teto_valor") ?? 0);
  const autoEmitir = valores.get("darf.auto_emitir") !== false;
  const killSwitch = valores.get("regua.kill_switch") === true;

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="DARFs"
        descricao="Recálculos pedidos pelos clientes e documentos emitidos pelo SICALC."
        acao={
          <Link href="/configuracoes?aba=darf" className="text-sm text-marca underline">
            configurar as travas
          </Link>
        }
      />

      {(liberadas.length === 0 || teto === 0 || !autoEmitir || killSwitch) && (
        <Aviso tom="atencao">
          <strong>Nada é emitido sem aprovação no momento.</strong>{" "}
          {[
            liberadas.length === 0 && "nenhum código de receita foi conferido",
            teto === 0 && "o teto de emissão automática está em zero",
            !autoEmitir && "a emissão automática está desligada",
            killSwitch && "o kill switch está ligado",
          ]
            .filter(Boolean)
            .join("; ")}
          . Esse é o estado correto até o leitor do relatório ser conferido contra um relatório
          real da Receita — soltar as travas é decisão do escritório, receita por receita.
        </Aviso>
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Indicador
          rotulo="Aguardando aprovação"
          valor={String(fila.length)}
          detalhe={fila.length > 0 ? "cliente esperando o documento" : "fila vazia"}
          tom={fila.length > 0 ? "destaque" : "neutro"}
        />
        <Indicador rotulo="Enviados" valor={String(enviados.length)} detalhe="últimos 100" />
        <Indicador
          rotulo="Falhados ou descartados"
          valor={String(falhados.length)}
          tom={falhados.length > 0 ? "destaque" : "neutro"}
        />
      </div>

      <Card titulo={`Aguardando aprovação (${fila.length})`}>
        {fila.length === 0 ? (
          <Vazio titulo="Nenhum DARF na fila">
            Quando um cliente responde <strong>1</strong> e informa a data de pagamento, o pedido
            aparece aqui para conferência antes de virar documento.
          </Vazio>
        ) : (
          <ul className="divide-y divide-linha">
            {fila.map((d) => {
              const valor = d.valor_total ?? d.debitos?.saldo_devedor ?? null;
              const resumo = `${formatarMoeda(valor)} para ${formatarData(d.data_consolidacao)}`;
              return (
                <li key={d.id} className="flex flex-wrap items-start gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {d.empresas?.id ? (
                        <Link
                          href={`/empresas/${d.empresas.id}`}
                          className="text-sm font-medium text-marca underline"
                        >
                          {d.empresas.razao_social}
                        </Link>
                      ) : (
                        <span className="text-sm font-medium text-tinta">—</span>
                      )}
                      <Etiqueta tom="atencao">aguardando aprovação</Etiqueta>
                      {d.valor_total && <Etiqueta tom="info">documento já gerado</Etiqueta>}
                    </div>
                    <p className="mt-1 text-sm text-tinta">
                      {d.debitos?.descricao ?? "débito"} ·{" "}
                      <span className="tabular">{formatarMoeda(valor)}</span> · pagamento em{" "}
                      <span className="tabular">{formatarData(d.data_consolidacao)}</span>
                    </p>
                    {d.motivo_aprovacao && (
                      <p className="mt-0.5 text-sm text-tinta-fraca">
                        Por que precisa de conferência: {d.motivo_aprovacao}
                      </p>
                    )}
                    <p className="mt-1 text-xs text-tinta-fraca">
                      {d.empresas?.cnpj && formatarCnpj(d.empresas.cnpj)}
                      {d.debitos?.codigo_receita && ` · receita ${d.debitos.codigo_receita}`}
                      {` · pedido em ${formatarData(d.created_at)}`}
                    </p>
                  </div>
                  <AcoesDarf darfId={d.id} resumo={resumo} />
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card
        titulo="Códigos de receita conferidos"
        descricao="Um código só emite sem revisão depois de alguém registrar como foi conferido."
      >
        {ehAdmin ? (
          <div className="mb-3">
            <FormularioReceita />
          </div>
        ) : (
          <p className="mb-3 text-xs text-tinta-fraca">
            Liberar um código para emissão automática é ação de administrador.
          </p>
        )}
        {(receitas ?? []).length === 0 ? (
          <Vazio titulo="Nenhum código conferido">
            Enquanto esta lista estiver vazia, todo DARF passa por aprovação. Um código só entra
            aqui depois de alguém conferir a periodicidade e a regra dele — semear palpites seria
            exatamente o erro que esta trava existe para impedir.
          </Vazio>
        ) : (
          <Tabela>
            <thead>
              <tr>
                <Th>Código</Th>
                <Th>Descrição</Th>
                <Th alinhar="direita">Teto próprio</Th>
                <Th>Situação</Th>
              </tr>
            </thead>
            <tbody>
              {(receitas ?? []).map((r) => (
                <tr key={r.codigo}>
                  <Td className="tabular">{r.codigo}</Td>
                  <Td>{r.descricao}</Td>
                  <Td alinhar="direita">
                    {r.teto_valor ? formatarMoeda(r.teto_valor) : "usa o teto geral"}
                  </Td>
                  <Td>
                    {r.ativo ? (
                      <Etiqueta tom="sucesso">emite sozinho</Etiqueta>
                    ) : (
                      <Etiqueta tom="neutro">só com aprovação</Etiqueta>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        )}
      </Card>

      <Card titulo={`Histórico (${historico.length})`}>
        {historico.length === 0 ? (
          <Vazio titulo="Nenhum DARF emitido ainda" />
        ) : (
          <Tabela>
            <thead>
              <tr>
                <Th>Cliente</Th>
                <Th>Débito</Th>
                <Th>Pagamento</Th>
                <Th alinhar="direita">Total</Th>
                <Th>Situação</Th>
                <Th>Documento</Th>
              </tr>
            </thead>
            <tbody>
              {historico.map((d) => (
                <tr key={d.id}>
                  <Td>
                    {d.empresas?.id ? (
                      <Link href={`/empresas/${d.empresas.id}`} className="text-marca underline">
                        {d.empresas.razao_social}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </Td>
                  <Td>{d.debitos?.descricao ?? "—"}</Td>
                  <Td className="tabular">{formatarData(d.data_consolidacao)}</Td>
                  <Td alinhar="direita">{formatarMoeda(d.valor_total)}</Td>
                  <Td>
                    <Etiqueta tom={TONS[d.status] ?? "neutro"}>
                      {d.status.replace(/_/g, " ")}
                    </Etiqueta>
                    {d.erro && (
                      <span className="mt-0.5 block text-xs text-tinta-fraca">{d.erro}</span>
                    )}
                  </Td>
                  <Td>
                    {/* O PDF e o código de barras eram gravados e não tinham como
                        ser alcançados pelo painel: o DARF emitido ficava fora do
                        alcance de quem precisa reenviá-lo ao cliente. */}
                    <div className="flex flex-col gap-1">
                      {d.pdf_storage_path && (
                        <a
                          href={`/api/arquivos/darf/${d.id}`}
                          className="text-xs text-marca underline"
                        >
                          baixar PDF
                        </a>
                      )}
                      {d.codigo_barras && (
                        <span className="flex items-center gap-2">
                          <code className="tabular text-xs text-tinta-fraca">
                            {d.codigo_barras.slice(0, 12)}…
                          </code>
                          <Copiar valor={d.codigo_barras} rotulo="copiar código" />
                        </span>
                      )}
                      {!d.pdf_storage_path && !d.codigo_barras && (
                        <span className="text-xs text-tinta-fraca">—</span>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        )}
      </Card>
    </div>
  );
}
