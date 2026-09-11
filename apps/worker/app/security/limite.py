"""Limite de requisições, para a única rota pública do worker.

O webhook da Evolution é a superfície exposta: qualquer um pode alcançá-la, e a
autenticação é um token no caminho. Duas coisas justificam o limite:

**Token descoberto.** Se o token vazar — log de acesso de terceiro, URL num
print —, quem o tiver pode injetar mensagem falsa. O limite não substitui a troca
do token, mas transforma "milhares de opt-outs forjados em segundos" em algo
lento o bastante para ser notado.

**Laço de reentrega.** A Evolution reenvia o que não recebe 200. Um defeito que
faça a rota devolver erro vira um laço que consome o worker inteiro; o limite é o
que impede um cliente de derrubar a cobrança de todos os outros.

Implementado em memória, com janela deslizante por chave. Sem Redis: o worker é
um processo, o volume é de dezenas de eventos por minuto, e uma dependência a
menos é uma peça a menos para operar. **Se um dia houver mais de uma réplica, o
limite passa a ser por réplica** — o que ainda ajuda, mas deixa de ser exato.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class LimitePorJanela:
    """Janela deslizante: no máximo ``maximo`` eventos em ``janela_s`` segundos."""

    maximo: int
    janela_s: float
    # Teto de chaves distintas guardadas. Sem ele, um atacante variando a chave
    # faria a própria defesa consumir a memória do processo.
    max_chaves: int = 10_000
    _eventos: dict[str, deque[float]] = field(default_factory=dict, init=False)

    def permitir(self, chave: str, *, agora: float | None = None) -> bool:
        """Registra uma tentativa. False quando passou do limite."""
        instante = time.monotonic() if agora is None else agora
        limite_inferior = instante - self.janela_s

        registros = self._eventos.get(chave)
        if registros is None:
            if len(self._eventos) >= self.max_chaves:
                self._limpar(limite_inferior)
            if len(self._eventos) >= self.max_chaves:
                # Ainda cheio depois da limpeza: recusar é mais seguro que
                # crescer sem teto. Acontecendo isso, o limite está mal
                # dimensionado — e o log da rota diz.
                return False
            registros = deque()
            self._eventos[chave] = registros

        while registros and registros[0] < limite_inferior:
            registros.popleft()

        if len(registros) >= self.maximo:
            return False

        registros.append(instante)
        return True

    def restantes(self, chave: str, *, agora: float | None = None) -> int:
        instante = time.monotonic() if agora is None else agora
        registros = self._eventos.get(chave)
        if not registros:
            return self.maximo
        limite_inferior = instante - self.janela_s
        vivos = sum(1 for r in registros if r >= limite_inferior)
        return max(self.maximo - vivos, 0)

    def _limpar(self, limite_inferior: float) -> None:
        """Descarta as chaves sem evento vivo."""
        vazias = [
            chave
            for chave, registros in self._eventos.items()
            if not registros or registros[-1] < limite_inferior
        ]
        for chave in vazias:
            del self._eventos[chave]

    def zerar(self) -> None:
        self._eventos.clear()
