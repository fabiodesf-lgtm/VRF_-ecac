/**
 * Preparo de termos de busca para o PostgREST.
 *
 * O `or()` do supabase-js monta uma expressão separada por vírgula, com
 * parênteses e `.` como sintaxe. Um termo digitado contendo esses caracteres
 * quebraria a expressão — ou, pior, mudaria o filtro. O `%` e o `_` são
 * curingas do LIKE: deixá-los passar faz uma busca por "100%" varrer a tabela.
 *
 * Então o termo é limpo na borda, como o CNPJ é validado na borda.
 */
export function termoParaBusca(bruto: string | undefined | null): string | null {
  if (!bruto) return null;
  const limpo = bruto
    .replace(/[,()*\\%_]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return limpo.length >= 2 ? limpo : null;
}
