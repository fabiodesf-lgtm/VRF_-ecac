import { formatarData } from "./validacao";

/** Dias antes do vencimento em que o certificado passa a merecer alerta. */
export const ALERTA_VENCIMENTO_DIAS = 30;

export type SituacaoCertificado = {
  tom: "sucesso" | "atencao" | "alerta";
  texto: string;
  dias: number | null;
};

/**
 * Classifica a validade do certificado de um procurador.
 *
 * Certificado vencido não é detalhe de cadastro: ele derruba toda consulta ao
 * e-CAC das empresas vinculadas, então a escala de alerta começa cedo.
 */
export function situacaoCertificado(notAfter: string | null | undefined): SituacaoCertificado {
  if (!notAfter) return { tom: "alerta", texto: "sem certificado", dias: null };

  const dias = Math.floor((new Date(notAfter).getTime() - Date.now()) / 86_400_000);
  if (dias < 0) return { tom: "alerta", texto: "vencido", dias };
  if (dias <= 7) return { tom: "alerta", texto: `vence em ${dias} d`, dias };
  if (dias <= ALERTA_VENCIMENTO_DIAS) return { tom: "atencao", texto: `vence em ${dias} d`, dias };
  return { tom: "sucesso", texto: `válido até ${formatarData(notAfter)}`, dias };
}
