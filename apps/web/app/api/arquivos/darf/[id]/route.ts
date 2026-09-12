import { servirArquivoDoWorker } from "@/lib/arquivos";
import { baixarPdfDarf } from "@/lib/worker";

export async function GET(
  _pedido: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return servirArquivoDoWorker(id, baixarPdfDarf, "darf.pdf");
}
