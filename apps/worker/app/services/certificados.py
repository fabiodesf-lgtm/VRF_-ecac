"""Recebimento e guarda do certificado digital do procurador.

Este é o caminho por onde o material mais sensível do sistema entra. A ordem das
etapas é deliberada:

1. **valida** o `.pfx` com a senha e confere que o titular é o procurador
   cadastrado — recusar na entrada evita descobrir o problema no meio de uma
   consulta ao SERPRO;
2. **cifra** o `.pfx` e a senha com AES-256-GCM, cada um amarrado ao seu
   contexto por AAD;
3. **grava** o blob cifrado no bucket privado e os metadados no banco;
4. **desativa** o certificado anterior do mesmo procurador, num único
   ``BEGIN``, para que nunca existam dois ativos nem zero;
5. **audita** a operação.

A senha em claro existe apenas como argumento desta função e nunca é gravada,
logada ou devolvida.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import registrar_auditoria, transacao
from app.security import crypto
from app.security.certificado import (
    CertificadoInvalido,
    DadosCertificado,
    mascarar_documento,
    validar_para_procurador,
)
from app.storage import Storage


class ProcuradorNaoEncontrado(Exception):
    pass


@dataclass(frozen=True)
class CertificadoArmazenado:
    certificado_id: str
    storage_path: str
    dados: DadosCertificado

    @property
    def resumo(self) -> dict[str, object]:
        """Resumo seguro para devolver ao painel e gravar em auditoria."""
        return {
            "certificado_id": self.certificado_id,
            "subject_cn": self.dados.subject_cn,
            "issuer_cn": self.dados.issuer_cn,
            "documento": mascarar_documento(self.dados.documento or ""),
            "not_before": self.dados.not_before.isoformat(),
            "not_after": self.dados.not_after.isoformat(),
            "dias_para_vencer": self.dados.dias_para_vencer,
            "fingerprint_sha256": self.dados.fingerprint_sha256,
        }


async def armazenar_certificado(
    engine: AsyncEngine,
    storage: Storage,
    *,
    procurador_id: str,
    pfx: bytes,
    senha: str,
    chave_mestra: bytes,
    enviado_por: str | None = None,
    exigir_documento_coincidente: bool = True,
) -> CertificadoArmazenado:
    """Valida, cifra e guarda o certificado A1 de um procurador."""
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select id, nome, cpf_cnpj, tipo from public.procuradores "
                    "where id = cast(:id as uuid)"
                ),
                {"id": procurador_id},
            )
        ).first()

    if linha is None:
        raise ProcuradorNaoEncontrado(f"procurador {procurador_id} não existe")

    # 1. Validação. Levanta CertificadoInvalido com mensagem exibível.
    dados = validar_para_procurador(
        pfx,
        senha,
        documento_procurador=linha.cpf_cnpj,
        exigir_documento_coincidente=exigir_documento_coincidente,
    )

    certificado_id = str(uuid.uuid4())
    storage_path = f"procuradores/{procurador_id}/{certificado_id}.pfx.enc"

    # 2. Criptografia em envelope. O AAD amarra cada blob ao seu campo e ao seu
    #    certificado: um ciphertext não pode ser transplantado para outro lugar.
    pfx_cifrado = crypto.cifrar(chave_mestra, pfx, aad=crypto.aad_pfx(certificado_id))
    senha_cifrada = crypto.cifrar_texto(
        chave_mestra, senha, aad=crypto.aad_senha_certificado(certificado_id)
    )
    pfx_sha256 = crypto.sha256_hex(pfx)

    # 3. Storage antes do banco: se o upload falhar, não sobra linha órfã
    #    apontando para um objeto que não existe.
    await storage.gravar(storage_path, pfx_cifrado, content_type="application/octet-stream")

    try:
        async with transacao(engine) as conexao:
            # 4. Desativa o anterior e insere o novo na mesma transação — o
            #    índice parcial `procurador_certificados_um_ativo` recusaria
            #    dois ativos, e fazer isso em duas transações abriria uma janela
            #    sem nenhum certificado válido.
            await conexao.execute(
                text(
                    "update public.procurador_certificados set ativo = false "
                    "where procurador_id = cast(:pid as uuid) and ativo"
                ),
                {"pid": procurador_id},
            )
            await conexao.execute(
                text(
                    """
                    insert into public.procurador_certificados
                        (id, procurador_id, storage_path, subject_cn, issuer_cn,
                         fingerprint_sha256, documento_subject, not_before, not_after,
                         ativo, enviado_por)
                    values
                        (cast(:id as uuid), cast(:pid as uuid), :path, :subject, :issuer,
                         :fp, :doc, :nb, :na, true,
                         cast(nullif(:por, '') as uuid))
                    """
                ),
                {
                    "id": certificado_id,
                    "pid": procurador_id,
                    "path": storage_path,
                    "subject": dados.subject_cn,
                    "issuer": dados.issuer_cn,
                    "fp": dados.fingerprint_sha256,
                    "doc": dados.documento,
                    "nb": dados.not_before,
                    "na": dados.not_after,
                    "por": enviado_por or "",
                },
            )
            await conexao.execute(
                text(
                    """
                    insert into public.procurador_certificado_segredos
                        (certificado_id, senha_cipher, pfx_sha256)
                    values (cast(:id as uuid), :senha, :sha)
                    """
                ),
                {"id": certificado_id, "senha": senha_cifrada, "sha": pfx_sha256},
            )

            # Fecha a tarefa de "certificado vencendo" que este envio resolve.
            await conexao.execute(
                text(
                    """
                    update public.tarefas
                       set status = 'resolvida', resolvido_em = now()
                     where procurador_id = cast(:pid as uuid)
                       and tipo in ('certificado_vencendo', 'erro_certificado')
                       and status in ('aberta', 'em_andamento')
                    """
                ),
                {"pid": procurador_id},
            )

            # 5. Auditoria. Registra o fato, nunca o material.
            await registrar_auditoria(
                conexao,
                acao="certificado.enviado",
                entidade="procurador_certificados",
                entidade_id=certificado_id,
                actor_id=enviado_por,
                actor_tipo="usuario" if enviado_por else "worker",
                depois={
                    "procurador_id": procurador_id,
                    "subject_cn": dados.subject_cn,
                    "issuer_cn": dados.issuer_cn,
                    "not_after": dados.not_after.isoformat(),
                    "fingerprint_sha256": dados.fingerprint_sha256,
                    "documento": mascarar_documento(dados.documento or ""),
                },
            )
    except Exception:
        # Banco falhou depois do upload: remove o objeto para não deixar
        # certificado cifrado sem nenhuma linha que o referencie.
        await storage.apagar(storage_path)
        raise

    return CertificadoArmazenado(
        certificado_id=certificado_id, storage_path=storage_path, dados=dados
    )


async def carregar_certificado_ativo(
    engine: AsyncEngine,
    storage: Storage,
    *,
    procurador_id: str,
    chave_mestra: bytes,
) -> tuple[bytes, str]:
    """Devolve ``(pfx, senha)`` em claro, apenas em memória.

    Usado no momento de falar com o SERPRO. Confere o hash do `.pfx` decifrado
    contra o que foi gravado na entrada — um blob adulterado no storage não
    chega a ser usado.
    """
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select c.id, c.storage_path, c.not_after, s.senha_cipher, s.pfx_sha256
                      from public.procurador_certificados c
                      join public.procurador_certificado_segredos s on s.certificado_id = c.id
                     where c.procurador_id = cast(:pid as uuid) and c.ativo
                    """
                ),
                {"pid": procurador_id},
            )
        ).first()

    if linha is None:
        raise CertificadoInvalido("procurador não tem certificado ativo cadastrado")

    certificado_id = str(linha.id)
    pfx_cifrado = await storage.ler(linha.storage_path)
    pfx = crypto.decifrar(chave_mestra, pfx_cifrado, aad=crypto.aad_pfx(certificado_id))

    if crypto.sha256_hex(pfx) != linha.pfx_sha256:
        raise CertificadoInvalido(
            "o certificado armazenado não corresponde ao hash registrado no envio"
        )

    senha = crypto.decifrar_texto(
        chave_mestra, linha.senha_cipher, aad=crypto.aad_senha_certificado(certificado_id)
    )
    return pfx, senha
