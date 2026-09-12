import { Abas, Aviso, Cabecalho, Card } from "@/components/ui";
import {
  GRUPOS_CONFIG,
  grupoPorId,
  valorBooleano,
  valorParaEntrada,
} from "@/lib/configuracoes";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { formatarData } from "@/lib/validacao";
import { FormularioGrupo, type ValoresIniciais } from "./formulario";
import { EditorTemplates, type Template } from "./templates";

export const dynamic = "force-dynamic";

const ABA_TEXTOS = "textos";

export default async function Configuracoes({
  searchParams,
}: {
  searchParams: Promise<{ aba?: string }>;
}) {
  const { aba } = await searchParams;
  const supabase = await criarClienteServidor();
  const atual = await usuarioAtual();
  const ehAdmin = atual?.perfil?.papel === "admin";

  const [{ data: configs, error }, { data: templates }] = await Promise.all([
    supabase
      .from("configuracoes_publicas")
      .select("chave, valor, descricao, updated_at")
      .order("chave"),
    supabase.from("templates").select("chave, titulo, corpo, descricao").order("chave"),
  ]);

  const guardadas = new Map((configs ?? []).map((c) => [c.chave ?? "", c]));
  const mostrandoTextos = aba === ABA_TEXTOS;
  const grupo = grupoPorId(aba);

  const iniciais: ValoresIniciais = Object.fromEntries(
    grupo.campos.map((campo) => {
      const guardado = guardadas.get(campo.chave)?.valor;
      return [
        campo.chave,
        campo.tipo === "booleano"
          ? valorBooleano(guardado)
          : valorParaEntrada(campo, guardado),
      ];
    }),
  );

  // Chave que a tela declara e o banco não tem: acontece com configuração que o
  // worker lê mas o seed não criou (`envio.feriados`). Salvar o grupo a cria.
  const inexistentes = grupo.campos.filter((c) => !guardadas.has(c.chave));

  const ultimaAlteracao = (configs ?? [])
    .map((c) => c.updated_at)
    .filter((d): d is string => Boolean(d))
    .sort()
    .at(-1);

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="Configurações"
        descricao="O que o sistema lê para decidir quando cobrar, quanto emitir e o que dizer."
      />

      {error && (
        <Aviso tom="alerta">Não foi possível carregar a configuração: {error.message}</Aviso>
      )}

      <Abas
        padrao={GRUPOS_CONFIG[0]!.id}
        abas={[
          ...GRUPOS_CONFIG.map((g) => ({ valor: g.id, rotulo: g.titulo })),
          { valor: ABA_TEXTOS, rotulo: "Textos das mensagens" },
        ]}
      />

      {mostrandoTextos ? (
        <EditorTemplates templates={(templates ?? []) as Template[]} />
      ) : (
        <>
          <Card titulo={grupo.titulo} descricao={grupo.descricao}>
            {inexistentes.length > 0 && (
              <div className="mb-4">
                <Aviso tom="atencao">
                  {inexistentes.length === 1
                    ? `A chave ${inexistentes[0]!.chave} ainda não existe no banco`
                    : `${inexistentes.length} chaves deste grupo ainda não existem no banco`}
                  . O worker usa o valor padrão até alguém salvar aqui pela primeira vez.
                </Aviso>
              </div>
            )}
            <FormularioGrupo grupo={grupo} iniciais={iniciais} ehAdmin={ehAdmin} />
          </Card>

          {ultimaAlteracao && (
            <p className="text-xs text-tinta-fraca">
              Última alteração de configuração:{" "}
              <span className="tabular">{formatarData(ultimaAlteracao)}</span> · o histórico
              completo está em <strong>Auditoria</strong>.
            </p>
          )}
        </>
      )}
    </div>
  );
}
