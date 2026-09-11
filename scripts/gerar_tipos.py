#!/usr/bin/env python3
"""Gera os tipos TypeScript do schema do Supabase a partir do catálogo do Postgres.

Equivalente a ``supabase gen types typescript``, sem a dependência de Docker que
o CLI do Supabase carrega (ele roda o pg-meta em container). Lê o catálogo por
SQL e emite o mesmo formato que o supabase-js espera — incluindo o bloco
``Relationships``, que é o que permite ao cliente tipar um select com recurso
embutido (``empresas(razao_social)``) como objeto ou como array, conforme a
cardinalidade.

Uso:
    python3 scripts/gerar_tipos.py "postgresql://..." > apps/web/lib/database.types.ts
"""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict

# Mapeamento de tipo do Postgres para TypeScript. O que não estiver aqui virai
# `unknown`, que quebra o build de forma visível em vez de silenciosamente
# virar `any`.
TIPOS = {
    "uuid": "string",
    "text": "string",
    "citext": "string",
    "varchar": "string",
    "bpchar": "string",
    "inet": "string",
    "bytea": "string",
    "date": "string",
    "timestamptz": "string",
    "timestamp": "string",
    "time": "string",
    "timetz": "string",
    "int2": "number",
    "int4": "number",
    "int8": "number",
    "float4": "number",
    "float8": "number",
    "numeric": "number",
    "bool": "boolean",
    "json": "Json",
    "jsonb": "Json",
}

CONSULTA_COLUNAS = """
select c.relname                                       as tabela,
       c.relkind                                       as tipo_relacao,
       a.attname                                       as coluna,
       format_type(a.atttypid, null)                   as tipo_formatado,
       t.typname                                       as tipo_nome,
       t.typtype                                       as tipo_categoria,
       not a.attnotnull                                as anulavel,
       (pg_get_expr(d.adbin, d.adrelid) is not null)   as tem_default,
       a.attidentity <> ''                             as identidade,
       a.attgenerated <> ''                            as gerada
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
  join pg_type t on t.oid = a.atttypid
  left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
 where n.nspname = 'public' and c.relkind in ('r', 'v')
 order by c.relname, a.attnum;
"""

CONSULTA_ENUMS = """
select t.typname as nome, e.enumlabel as valor
  from pg_type t
  join pg_namespace n on n.oid = t.typnamespace
  join pg_enum e on e.enumtypid = t.oid
 where n.nspname = 'public'
 order by t.typname, e.enumsortorder;
"""

# Chaves estrangeiras com a informação de cardinalidade. isOneToOne é verdadeiro
# quando as colunas da FK têm restrição de unicidade — nesse caso o lado oposto
# também é único, e o supabase-js tipa o recurso embutido como objeto.
CONSULTA_FKS = """
select con.conname                                     as nome,
       origem.relname                                  as tabela,
       destino.relname                                 as tabela_destino,
       (select array_agg(att.attname order by u.ord)
          from unnest(con.conkey) with ordinality as u(attnum, ord)
          join pg_attribute att
            on att.attrelid = con.conrelid and att.attnum = u.attnum) as colunas,
       (select array_agg(att.attname order by u.ord)
          from unnest(con.confkey) with ordinality as u(attnum, ord)
          join pg_attribute att
            on att.attrelid = con.confrelid and att.attnum = u.attnum) as colunas_destino,
       exists (
         select 1 from pg_index i
          where i.indrelid = con.conrelid
            and i.indisunique
            and i.indpred is null
            and i.indkey::int2[] @> con.conkey
            and array_length(i.indkey::int2[], 1) = array_length(con.conkey, 1)
       )                                               as um_para_um
  from pg_constraint con
  join pg_class origem  on origem.oid = con.conrelid
  join pg_class destino on destino.oid = con.confrelid
  join pg_namespace n   on n.oid = origem.relnamespace
 where con.contype = 'f' and n.nspname = 'public'
 order by origem.relname, con.conname;
"""


def consultar(url: str, sql: str) -> list[list[str]]:
    """Roda a consulta via psql e devolve as linhas já separadas por campo."""
    saida = subprocess.run(
        ["psql", url, "-tAF", "\x1f", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [linha.split("\x1f") for linha in saida.strip().split("\n") if linha]


def ts_bool(valor: str) -> bool:
    return valor == "t"


def tipo_ts(tipo_nome: str, tipo_formatado: str, categoria: str, enums: set[str]) -> str:
    if tipo_formatado.endswith("[]"):
        interno = tipo_ts(tipo_nome.lstrip("_"), tipo_formatado[:-2], categoria, enums)
        return f"{interno}[]"
    if categoria == "e" or tipo_nome in enums:
        return f'Database["public"]["Enums"]["{tipo_nome}"]'
    return TIPOS.get(tipo_nome, "unknown")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    url = sys.argv[1]

    enums: dict[str, list[str]] = defaultdict(list)
    for nome, valor in consultar(url, CONSULTA_ENUMS):
        enums[nome].append(valor)
    nomes_enum = set(enums)

    colunas: dict[str, list[dict[str, object]]] = defaultdict(list)
    eh_view: dict[str, bool] = {}
    for linha in consultar(url, CONSULTA_COLUNAS):
        (tabela, relkind, coluna, formatado, tipo_nome, categoria,
         anulavel, tem_default, identidade, gerada) = linha
        eh_view[tabela] = relkind == "v"
        colunas[tabela].append(
            {
                "nome": coluna,
                "ts": tipo_ts(tipo_nome, formatado, categoria, nomes_enum),
                "anulavel": ts_bool(anulavel),
                # Coluna com default, identidade ou gerada é opcional no insert.
                "opcional_no_insert": ts_bool(tem_default)
                or ts_bool(identidade)
                or ts_bool(gerada),
                "gerada": ts_bool(gerada) or ts_bool(identidade),
            }
        )

    fks: dict[str, list[dict[str, object]]] = defaultdict(list)
    for nome, tabela, destino, cols, cols_destino, um_para_um in consultar(url, CONSULTA_FKS):
        fks[tabela].append(
            {
                "nome": nome,
                "colunas": cols.strip("{}").split(","),
                "destino": destino,
                "colunas_destino": cols_destino.strip("{}").split(","),
                "um_para_um": ts_bool(um_para_um),
            }
        )

    partes: list[str] = [
        "// GERADO AUTOMATICAMENTE — não edite à mão.",
        "//",
        "// Regenere com:",
        "//   python3 scripts/gerar_tipos.py \"$DATABASE_URL\" \\",
        "//     > apps/web/lib/database.types.ts",
        "",
        "export type Json = string | number | boolean | null "
        "| { [key: string]: Json | undefined } | Json[];",
        "",
        "export type Database = {",
        "  public: {",
        "    Tables: {",
    ]

    tabelas = sorted(t for t in colunas if not eh_view[t])
    views = sorted(t for t in colunas if eh_view[t])

    for tabela in tabelas:
        partes.append(f"      {tabela}: {{")
        partes.append("        Row: {")
        for c in colunas[tabela]:
            sufixo = " | null" if c["anulavel"] else ""
            partes.append(f"          {c['nome']}: {c['ts']}{sufixo};")
        partes.append("        };")

        partes.append("        Insert: {")
        for c in colunas[tabela]:
            if c["gerada"]:
                continue
            opcional = "?" if (c["opcional_no_insert"] or c["anulavel"]) else ""
            sufixo = " | null" if c["anulavel"] else ""
            partes.append(f"          {c['nome']}{opcional}: {c['ts']}{sufixo};")
        partes.append("        };")

        partes.append("        Update: {")
        for c in colunas[tabela]:
            if c["gerada"]:
                continue
            sufixo = " | null" if c["anulavel"] else ""
            partes.append(f"          {c['nome']}?: {c['ts']}{sufixo};")
        partes.append("        };")

        partes.append("        Relationships: [")
        for fk in fks.get(tabela, []):
            cols = ", ".join(f'"{c}"' for c in fk["colunas"])
            cols_destino = ", ".join(f'"{c}"' for c in fk["colunas_destino"])
            partes.append("          {")
            partes.append(f'            foreignKeyName: "{fk["nome"]}";')
            partes.append(f"            columns: [{cols}];")
            partes.append(
                f"            isOneToOne: {'true' if fk['um_para_um'] else 'false'};"
            )
            partes.append(f'            referencedRelation: "{fk["destino"]}";')
            partes.append(f"            referencedColumns: [{cols_destino}];")
            partes.append("          },")
        partes.append("        ];")
        partes.append("      };")

    partes.append("    };")
    partes.append("    Views: {")
    for view in views:
        partes.append(f"      {view}: {{")
        partes.append("        Row: {")
        for c in colunas[view]:
            partes.append(f"          {c['nome']}: {c['ts']} | null;")
        partes.append("        };")
        partes.append("        Relationships: [];")
        partes.append("      };")
    partes.append("    };")

    partes.append("    Functions: {")
    partes.append("      [_ in never]: never;")
    partes.append("    };")

    partes.append("    Enums: {")
    for nome in sorted(enums):
        valores = " | ".join(f'"{v}"' for v in enums[nome])
        partes.append(f"      {nome}: {valores};")
    partes.append("    };")

    partes.append("    CompositeTypes: {")
    partes.append("      [_ in never]: never;")
    partes.append("    };")
    partes.append("  };")
    partes.append("};")
    partes.append("")

    print("\n".join(partes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
