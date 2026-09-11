"""Cobertura de RLS: nenhuma tabela nova pode nascer sem política.

Este arquivo existe por um motivo específico. O schema cresce por migration, e
uma tabela criada sem `enable row level security` fica **legível por qualquer
usuário autenticado** — não é um erro que apareça em teste funcional, porque a
aplicação continua funcionando perfeitamente. Aparece num vazamento.

O teste não confere uma lista fixa de tabelas: ele varre o catálogo. Uma tabela
nova entra automaticamente na verificação, que é o que faz a trava valer para o
código que ainda não foi escrito.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)

# Tabelas que, de propósito, têm RLS ligada e **nenhuma** política: negar por
# padrão é o comportamento correto para elas. Só o worker (service_role, que
# ignora RLS) as alcança.
SEM_POLITICA_POR_DESENHO = {
    # Senha e blob do certificado digital. Ninguém no painel precisa ler isto,
    # nem por engano.
    "procurador_certificado_segredos",
}


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    motor = create_async_engine(DATABASE_URL)
    yield motor
    await motor.dispose()


async def test_toda_tabela_tem_rls_ligada(engine: AsyncEngine) -> None:
    async with engine.begin() as conexao:
        sem_rls = (
            await conexao.execute(
                text(
                    """
                    select c.relname as tabela
                      from pg_class c
                      join pg_namespace n on n.oid = c.relnamespace
                     where n.nspname = 'public'
                       and c.relkind = 'r'
                       and not c.relrowsecurity
                     order by c.relname
                    """
                )
            )
        ).all()

    assert not sem_rls, (
        "tabela(s) sem row level security: "
        + ", ".join(linha.tabela for linha in sem_rls)
        + ". Sem RLS, qualquer usuário autenticado do painel lê a tabela inteira."
    )


async def test_toda_tabela_com_rls_tem_politica_ou_e_deliberada(engine: AsyncEngine) -> None:
    """RLS ligada sem política nenhuma nega tudo — ótimo para segredo, ruim por acidente.

    Uma tabela que o painel precisa ler e que ficou sem política vira uma tela
    vazia sem erro, que é das falhas mais difíceis de diagnosticar.
    """
    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    """
                    select c.relname as tabela, count(p.polname) as politicas
                      from pg_class c
                      join pg_namespace n on n.oid = c.relnamespace
                      left join pg_policy p on p.polrelid = c.oid
                     where n.nspname = 'public' and c.relkind = 'r'
                     group by c.relname
                    """
                )
            )
        ).all()

    mudas = {linha.tabela for linha in linhas if linha.politicas == 0}
    inesperadas = mudas - SEM_POLITICA_POR_DESENHO

    assert not inesperadas, (
        "tabela(s) com RLS mas sem política: "
        + ", ".join(sorted(inesperadas))
        + ". Ou falta a política, ou a tabela deve entrar em SEM_POLITICA_POR_DESENHO "
        "com a justificativa."
    )

    # E o contrário: se um dia alguém der política à tabela de segredos, este
    # teste avisa em vez de deixar passar.
    esperadas_mudas = SEM_POLITICA_POR_DESENHO & {linha.tabela for linha in linhas}
    for linha in linhas:
        if linha.tabela in esperadas_mudas:
            assert linha.politicas == 0, (
                f"{linha.tabela} ganhou política de RLS. Ela guarda segredo de "
                "certificado e deve negar acesso a todo usuário do painel."
            )


async def test_visoes_usam_security_invoker(engine: AsyncEngine) -> None:
    """View sem `security_invoker` roda como o dono e **contorna a RLS**.

    É o caminho mais fácil de vazar dado sem perceber: a tabela está protegida,
    a view por cima dela não está.
    """
    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    """
                    select c.relname as visao,
                           coalesce(
                             (select option from unnest(c.reloptions) option
                               where option like 'security_invoker=%'), '') as opcao
                      from pg_class c
                      join pg_namespace n on n.oid = c.relnamespace
                     where n.nspname = 'public' and c.relkind = 'v'
                     order by c.relname
                    """
                )
            )
        ).all()

    assert linhas, "nenhuma view encontrada — o teste não estaria verificando nada"

    sem_invoker = [linha.visao for linha in linhas if not linha.opcao.endswith(("=true", "=on"))]
    assert not sem_invoker, (
        "view(s) sem security_invoker: "
        + ", ".join(sem_invoker)
        + ". Elas rodam com os privilégios do dono e contornam a RLS das tabelas."
    )


async def test_segredos_de_certificado_ficam_fora_das_visoes(engine: AsyncEngine) -> None:
    """Nenhuma view pode expor a senha ou o blob do certificado.

    A tabela nega acesso por RLS; uma view que selecionasse essas colunas
    desfaria a proteção sem que ninguém notasse.
    """
    async with engine.begin() as conexao:
        vazamentos = (
            await conexao.execute(
                text(
                    """
                    select table_name, column_name
                      from information_schema.columns
                     where table_schema = 'public'
                       and table_name in (
                         select table_name from information_schema.views
                          where table_schema = 'public')
                       and (column_name like '%senha%'
                            or column_name like '%cipher%'
                            or column_name like '%pfx%')
                    """
                )
            )
        ).all()

    assert not vazamentos, "view(s) expondo segredo de certificado: " + ", ".join(
        f"{linha.table_name}.{linha.column_name}" for linha in vazamentos
    )
