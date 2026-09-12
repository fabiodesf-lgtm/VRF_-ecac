"use server";

import { revalidatePath } from "next/cache";

import {
  entradaParaValor,
  grupoPorId,
  mesmoValor,
  type CampoConfig,
} from "@/lib/configuracoes";
import type { Json } from "@/lib/database.types";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { variaveisInvalidas, variaveisPermitidas } from "@/lib/templates";
import { ligarKillSwitch } from "../regua/acoes";

export type Resultado = { ok: true; mensagem?: string } | { ok: false; erro: string };

/**
 * Gravação das configurações do sistema.
 *
 * A autorização é da RLS: `configuracoes` só aceita `insert`/`update` de admin, e
 * o privilégio de coluna deixa `valor_cipher` fora de alcance. A checagem aqui
 * existe para dar erro legível em vez de um "row-level security" cru.
 *
 * Grava **só o que mudou**. Reescrever as dezessete chaves a cada salvamento
 * encheria a auditoria de linhas sem mudança e mexeria no `updated_at` de
 * configuração que ninguém tocou.
 */
export async function salvarGrupo(
  grupoId: string,
  _anterior: unknown,
  dados: FormData,
): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };
  if (atual.perfil?.papel !== "admin") {
    return { ok: false, erro: "só um administrador pode alterar a configuração" };
  }

  const grupo = grupoPorId(grupoId);
  const supabase = await criarClienteServidor();

  const { data: guardadas, error: erroLeitura } = await supabase
    .from("configuracoes_publicas")
    .select("chave, valor, descricao");
  if (erroLeitura) return { ok: false, erro: erroLeitura.message };

  const existentes = new Map(
    (guardadas ?? []).map((c) => [c.chave ?? "", { valor: c.valor, descricao: c.descricao }]),
  );

  // Primeiro converte tudo: um grupo com um campo inválido não grava nada. Salvar
  // metade da janela de envio deixaria a régua numa configuração que ninguém
  // escolheu.
  const novos: { campo: CampoConfig; valor: Json }[] = [];
  for (const campo of grupo.campos) {
    const convertido =
      campo.tipo === "booleano"
        ? ({ ok: true, valor: dados.has(campo.chave) } as const)
        : entradaParaValor(campo, String(dados.get(campo.chave) ?? ""));
    if (!convertido.ok) return { ok: false, erro: convertido.erro };
    novos.push({ campo, valor: convertido.valor });
  }

  const alteradas = novos.filter(
    ({ campo, valor }) => !mesmoValor(existentes.get(campo.chave)?.valor, valor),
  );

  if (alteradas.length === 0) {
    return { ok: true, mensagem: "Nada mudou." };
  }

  for (const { campo, valor } of alteradas) {
    // O kill switch tem ação própria, com a sua auditoria e as suas
    // revalidações. Reescrevê-lo aqui criaria um segundo caminho para a trava
    // mais importante da régua.
    if (campo.chave === "regua.kill_switch") {
      const resultado = await ligarKillSwitch(valor === true);
      if (!resultado.ok) return resultado;
      continue;
    }

    const { data: atualizada, error } = await supabase
      .from("configuracoes")
      .update({ valor, updated_by: atual.user.id })
      .eq("chave", campo.chave)
      .select("chave")
      .maybeSingle();

    if (error) return { ok: false, erro: mensagemDeErro(campo, error.message) };

    // Chave que o seed não criou (é o caso de `envio.feriados`, que o worker já
    // lê): inserir é o que tira a funcionalidade da dormência.
    if (!atualizada) {
      const { error: erroInsercao } = await supabase.from("configuracoes").insert({
        chave: campo.chave,
        valor,
        sensivel: false,
        descricao: campo.dica ?? campo.rotulo,
        updated_by: atual.user.id,
      });
      if (erroInsercao) {
        return { ok: false, erro: mensagemDeErro(campo, erroInsercao.message) };
      }
    }

    await registrarAuditoria("configuracao.alterada", campo.chave, {
      chave: campo.chave,
      antes: existentes.get(campo.chave)?.valor ?? null,
      depois: valor,
    });
  }

  revalidatePath("/configuracoes");
  revalidatePath("/regua");
  revalidatePath("/darfs");
  revalidatePath("/lgpd");
  revalidatePath("/operacao");
  revalidatePath("/");

  return {
    ok: true,
    mensagem:
      alteradas.length === 1
        ? `${alteradas[0]!.campo.rotulo} atualizado.`
        : `${alteradas.length} configurações atualizadas.`,
  };
}

/**
 * Salva o texto de um template de mensagem.
 *
 * A validação das variáveis é a razão de esta ação existir em vez de um `update`
 * direto: o renderizador do worker **recusa** template que peça variável não
 * fornecida, e a recusa acontece no envio — então um `{{nome_errado}}` salvo aqui
 * pararia a cobrança de toda a carteira, e a mensagem de erro apareceria num log
 * do worker, não na tela de quem editou.
 */
export async function salvarTemplate(chave: string, corpo: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  const texto = corpo.trim();
  if (!texto) return { ok: false, erro: "o texto da mensagem não pode ficar vazio" };
  if (texto.length > 4000) {
    return { ok: false, erro: "o texto passou de 4000 caracteres — o WhatsApp corta antes disso" };
  }

  const invalidas = variaveisInvalidas(chave, texto);
  if (invalidas.length > 0) {
    const permitidas = variaveisPermitidas(chave).join(", ");
    return {
      ok: false,
      erro:
        `Variável não disponível neste texto: ${invalidas.map((v) => `{{${v}}}`).join(", ")}. ` +
        `O envio falharia. Disponíveis: ${permitidas}.`,
    };
  }

  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("templates")
    .update({ corpo: texto })
    .eq("chave", chave)
    .select("chave, titulo")
    .maybeSingle();

  if (error) {
    if (error.message.includes("row-level security")) {
      return { ok: false, erro: "seu usuário não tem permissão para editar os textos" };
    }
    return { ok: false, erro: error.message };
  }
  if (!data) return { ok: false, erro: "template não encontrado" };

  await registrarAuditoria("template.alterado", chave, { chave, tamanho: texto.length });

  revalidatePath("/configuracoes");
  return { ok: true, mensagem: `"${data.titulo}" atualizado.` };
}

function mensagemDeErro(campo: CampoConfig, mensagem: string): string {
  if (mensagem.includes("row-level security")) {
    return "Seu usuário não tem permissão para alterar a configuração.";
  }
  if (mensagem.includes("configuracoes_um_valor")) {
    return `${campo.rotulo}: esta chave guarda valor cifrado e não é editável pelo painel.`;
  }
  return `${campo.rotulo}: ${mensagem}`;
}

async function registrarAuditoria(
  acao: string,
  entidadeId: string,
  depois: Record<string, Json>,
): Promise<void> {
  const atual = await usuarioAtual();
  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("audit_log").insert({
    acao,
    entidade: "configuracoes",
    entidade_id: entidadeId,
    actor_id: atual?.user.id ?? null,
    actor_tipo: "usuario",
    depois,
  });
  if (error) console.error(`falha ao registrar auditoria de ${acao}:`, error.message);
}
