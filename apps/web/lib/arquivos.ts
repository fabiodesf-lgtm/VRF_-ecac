import { NextResponse } from "next/server";

import { usuarioAtual } from "@/lib/supabase/server";
import { WorkerError, type ArquivoDoWorker } from "@/lib/worker";

/**
 * Repassa ao navegador um arquivo que vive no Storage do worker.
 *
 * A checagem de sessão é refeita aqui, embora o middleware já proteja `/api`:
 * são documentos fiscais de clientes, e a resposta não passa por RLS nenhuma —
 * a autorização desta rota é a única que existe no caminho.
 */
export async function servirArquivoDoWorker(
  id: string,
  buscar: (id: string) => Promise<ArquivoDoWorker>,
  nomePadrao: string,
): Promise<Response> {
  const atual = await usuarioAtual();
  if (!atual?.perfil?.ativo) {
    return NextResponse.json({ erro: "sessão inválida ou usuário inativo" }, { status: 403 });
  }

  // Formato conferido antes de sair daqui: evita uma chamada ao worker por um id
  // que não pode existir.
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    return NextResponse.json({ erro: "identificador inválido" }, { status: 404 });
  }

  try {
    const arquivo = await buscar(id);
    return new NextResponse(arquivo.bytes, {
      headers: {
        "content-type": arquivo.contentType,
        "content-disposition": `attachment; filename="${arquivo.nomeArquivo ?? nomePadrao}"`,
        // Documento fiscal de cliente não fica em cache de navegador nem de CDN.
        "cache-control": "private, no-store",
      },
    });
  } catch (erro) {
    if (erro instanceof WorkerError) {
      if (erro.status === 404) {
        return NextResponse.json({ erro: erro.message }, { status: 404 });
      }
      return NextResponse.json(
        { erro: `o worker recusou (${erro.status}): ${erro.message}` },
        { status: 502 },
      );
    }
    console.error("falha ao buscar arquivo no worker:", erro);
    return NextResponse.json(
      { erro: "não foi possível falar com o worker" },
      { status: 502 },
    );
  }
}
