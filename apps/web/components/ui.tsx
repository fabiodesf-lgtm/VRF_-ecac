/**
 * Kit de UI do painel.
 *
 * Ponto único de importação (`@/components/ui`) para as ~25 telas e formulários.
 * As implementações vivem em `components/ui/`, separadas por natureza: os
 * primitivos e as peças de página são componentes de servidor; diálogo, avisos
 * flutuantes, botões de ação e controles de URL são de cliente, cada um com a
 * sua diretiva.
 */

export {
  AreaTexto,
  Aviso,
  Botao,
  Campo,
  Card,
  Entrada,
  Etiqueta,
  Selecao,
  Tabela,
  Td,
  Th,
  Vazio,
  TONS,
  type Tom,
} from "./ui/primitivos";

export {
  Cabecalho,
  Descricao,
  Esqueleto,
  EsqueletoCartao,
  Indicador,
  Item,
} from "./ui/layout";

export { ProvedorAvisos, useAvisos } from "./ui/avisos";
export { Dialogo, RodapeDialogo } from "./ui/dialogo";
export { Alternador, BotaoAcao, Copiar, type ResultadoSimples } from "./ui/acao";
export {
  Abas,
  Busca,
  Filtro,
  LimparFiltros,
  Paginacao,
  useNavegacaoPorUrl,
} from "./ui/url";
