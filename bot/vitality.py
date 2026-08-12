"""A vaga ainda está aberta?

Três das quatro fontes respondem isso de graça: quando o anúncio sai do ar, o
endpoint de detalhe passa a devolver 404. Medido em 12/08:

    ONM       api.onovomercado.com.br/.../projects/{id}          200 → 404
    LinkedIn  linkedin.com/jobs-guest/jobs/api/jobPosting/{id}    200 → 404
    Gupy      employability-portal.gupy.io/api/v1/jobs/{id}       200 → 404
    Indeed    br.indeed.com/viewjob                403 (Cloudflare) — não serve

**Regra de ouro deste módulo: na dúvida, a vaga está aberta.** Só um 404
explícito conta como fechada. Timeout, erro de rede, 403, 429 e 5xx devolvem
"desconhecida" — porque a diferença entre *a vaga foi encerrada* e *a plataforma
nos bloqueou* é a diferença entre apagar uma mensagem certa e apagar uma vaga
boa do grupo. Errar para o lado de deixar no ar é barato; o contrário não é.
"""

from __future__ import annotations

import logging
from typing import Literal

import requests

log = logging.getLogger("av-jobs-bot.vitality")

Estado = Literal["aberta", "fechada", "desconhecida"]

TIMEOUT = 20

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ONM_DETALHE = "https://api.onovomercado.com.br/mercado-de-trabalho/v1/projects/{}"
GUPY_DETALHE = "https://employability-portal.gupy.io/api/v1/jobs/{}"
LINKEDIN_DETALHE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

# Fontes que não têm como ser verificadas. Vaga delas nunca é marcada como
# fechada — melhor um anúncio velho no grupo do que um apagão por engano.
SEM_VERIFICACAO = frozenset({"indeed"})


def _classificar(resp: requests.Response) -> Estado:
    if resp.status_code == 404:
        return "fechada"
    if resp.ok:
        return "aberta"
    # 403 e 429 são bloqueio, não encerramento. 5xx é problema deles.
    log.debug("Resposta inconclusiva (%s) — tratando como desconhecida",
              resp.status_code)
    return "desconhecida"


def verificar(source: str, source_id: str, *,
              token_onm: str | None = None) -> Estado:
    """Diz se a vaga ainda está no ar. Nunca levanta exceção."""
    if source in SEM_VERIFICACAO:
        return "desconhecida"

    try:
        if source == "onm":
            if not token_onm:
                return "desconhecida"
            resp = requests.get(
                ONM_DETALHE.format(source_id),
                headers={"Authorization": f"Bearer {token_onm}"},
                timeout=TIMEOUT,
            )
            # 401 = nosso token venceu, não que a vaga acabou.
            if resp.status_code == 401:
                return "desconhecida"
            return _classificar(resp)

        if source == "gupy":
            resp = requests.get(GUPY_DETALHE.format(source_id),
                                headers={"User-Agent": _UA}, timeout=TIMEOUT)
            return _classificar(resp)

        if source == "linkedin":
            resp = requests.get(LINKEDIN_DETALHE.format(source_id),
                                headers={"User-Agent": _UA}, timeout=TIMEOUT)
            return _classificar(resp)

    except requests.RequestException as exc:
        log.debug("Falha checando %s:%s — %s", source, source_id, exc)
        return "desconhecida"

    return "desconhecida"
