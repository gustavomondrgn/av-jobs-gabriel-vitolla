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

# O Indeed é o caso especial: o site tem Cloudflare na frente e devolve 403 até
# de IP residencial, mas a API do aplicativo — a mesma que o `python-jobspy`
# usa para buscar — aceita consulta por chave. Chave viva volta a vaga; chave
# morta volta lista vazia. E aceita LOTE, o que torna esta a fonte mais barata
# de verificar de todas: 12 vagas numa requisição.
INDEED_GRAPHQL = "https://apis.indeed.com/graphql"
INDEED_LOTE = 12

# Nenhuma fonte ficou de fora: as quatro respondem se a vaga ainda existe.
SEM_VERIFICACAO: frozenset[str] = frozenset()


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

        if source == "indeed":
            return verificar_indeed([source_id]).get(source_id, "desconhecida")

    except requests.RequestException as exc:
        log.debug("Falha checando %s:%s — %s", source, source_id, exc)
        return "desconhecida"

    return "desconhecida"


def verificar_indeed(chaves: list[str]) -> dict[str, Estado]:
    """Verifica várias vagas do Indeed numa requisição só.

    A API devolve apenas as chaves que ainda existem; o que não voltar é vaga
    encerrada. Como a resposta é uma lista e não um erro por item, uma falha da
    requisição inteira precisa deixar TODAS como desconhecidas — devolver
    "fechada" para o lote seria apagar doze mensagens boas de uma vez.
    """
    chaves = [c for c in chaves if c]
    if not chaves:
        return {}

    resultado: dict[str, Estado] = {}
    try:
        from jobspy.indeed.constant import api_headers  # noqa: PLC0415
        headers = {**api_headers, "indeed-co": "BR"}
    except ImportError:
        log.debug("jobspy indisponível — Indeed fica sem verificação")
        return {c: "desconhecida" for c in chaves}

    for i in range(0, len(chaves), INDEED_LOTE):
        lote = chaves[i:i + INDEED_LOTE]
        lista = ",".join(f'"{c}"' for c in lote)
        query = ("query { jobData(input: {jobKeys: [" + lista + "]}) "
                 "{ results { job { key } } } }")
        try:
            resp = requests.post(INDEED_GRAPHQL, headers=headers,
                                 json={"query": query}, timeout=TIMEOUT)
            if not resp.ok:
                resultado.update({c: "desconhecida" for c in lote})
                continue
            dados = resp.json()
            if dados.get("errors"):
                log.debug("Indeed devolveu errors: %s", str(dados["errors"])[:150])
                resultado.update({c: "desconhecida" for c in lote})
                continue
            vivas = {
                r["job"]["key"]
                for r in (dados.get("data", {}).get("jobData", {}).get("results") or [])
                if r.get("job", {}).get("key")
            }
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            log.debug("Falha checando lote do Indeed: %s", exc)
            resultado.update({c: "desconhecida" for c in lote})
            continue

        for c in lote:
            resultado[c] = "aberta" if c in vivas else "fechada"

    return resultado
