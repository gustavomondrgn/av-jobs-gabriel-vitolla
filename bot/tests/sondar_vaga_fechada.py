"""Dá para saber se uma vaga já foi fechada? Uma sonda por fonte.

Antes de prometer a bolinha verde/vermelha no painel, é preciso saber se cada
plataforma responde a pergunta "esta vaga ainda existe?" — e se a resposta
distingue *fechada* de *bloqueou nosso acesso*, que são coisas muito diferentes:
a primeira apaga a mensagem no grupo, a segunda apagaria uma vaga boa por engano.

    python bot/tests/sondar_vaga_fechada.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402
import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from sources import LinkedInSource, ONMSource  # noqa: E402

load_dotenv()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def cabec(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


# ---------------------------------------------------------------------------

def sondar_onm() -> None:
    cabec("ONM — endpoint de detalhe por id")
    email = os.getenv("ONM_EMAIL", "")
    senha = os.getenv("ONM_PASSWORD", "")
    if not email:
        print("  sem credenciais no .env"); return

    fonte = ONMSource(email, senha)
    fonte.login()
    abertas = fonte._get_projects()
    if not abertas:
        print("  listagem vazia"); return

    viva = abertas[0]["id"]
    print(f"  vaga que ESTÁ na listagem: id={viva}")

    base = "https://api.onovomercado.com.br/mercado-de-trabalho/v1/projects"
    h = {"Authorization": f"Bearer {fonte._token}"}

    for rotulo, pid in [("aberta (da listagem)", viva),
                        ("antiga (id bem menor)", viva - 3000),
                        ("inexistente", 999999999)]:
        try:
            r = requests.get(f"{base}/{pid}", headers=h, timeout=25)
            corpo = r.text[:110].replace("\n", " ")
            print(f"  {rotulo:<24} id={pid:<10} HTTP {r.status_code}  {corpo}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {rotulo:<24} id={pid:<10} ERRO {type(exc).__name__}")


def sondar_linkedin() -> None:
    cabec("LinkedIn — guest API de detalhe (a mesma que o bot já usa)")
    fonte = LinkedInSource(terms_file=Path("bot/config/search_terms_linkedin.txt"))
    try:
        cards = fonte._buscar("assistente virtual")[:3]
    except Exception as exc:  # noqa: BLE001
        print(f"  busca falhou: {type(exc).__name__}: {exc}")
        cards = []

    ids = [c["id"] for c in cards] if cards else []
    if ids:
        print(f"  ids vivos encontrados na busca: {ids}")
    alvos = [("aberta (da busca)", ids[0])] if ids else []
    alvos += [("id antigo/provável fechado", "3700000000"),
              ("id inexistente", "1")]

    for rotulo, jid in alvos:
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            print(f"  {rotulo:<28} id={jid:<12} HTTP {r.status_code}  "
                  f"{len(r.text)} bytes")
        except Exception as exc:  # noqa: BLE001
            print(f"  {rotulo:<28} id={jid:<12} ERRO {type(exc).__name__}")


def sondar_gupy() -> None:
    cabec("Gupy — página pública da vaga")
    r = requests.get("https://employability-portal.gupy.io/api/v1/jobs",
                     params={"jobName": "assistente virtual", "limit": 3},
                     headers={"User-Agent": UA}, timeout=30)
    dados = (r.json() or {}).get("data") or []
    if not dados:
        print("  a busca não devolveu vagas"); return

    for v in dados[:2]:
        url = v.get("jobUrl") or ""
        if not url:
            continue
        try:
            rr = requests.get(url, headers={"User-Agent": UA}, timeout=30,
                              allow_redirects=True)
            fechada = any(p in rr.text.lower() for p in
                          ("vaga encerrada", "não está mais", "encerrada",
                           "no longer", "expirada"))
            print(f"  aberta  HTTP {rr.status_code}  {len(rr.text)} bytes  "
                  f"marcador-de-fechada={fechada}")
            print(f"     {url[:90]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERRO {type(exc).__name__}: {exc}")

    # Uma URL com id inventado, para ver como a Gupy responde ao inexistente.
    if dados and (u := dados[0].get("jobUrl")):
        falsa = u.rstrip("/") + "000"
        try:
            rr = requests.get(falsa, headers={"User-Agent": UA}, timeout=30)
            print(f"  inexistente  HTTP {rr.status_code}  {len(rr.text)} bytes")
        except Exception as exc:  # noqa: BLE001
            print(f"  inexistente  ERRO {type(exc).__name__}")


def sondar_indeed() -> None:
    cabec("Indeed — página da vaga (Cloudflare na frente, ver aprendizados)")
    r = requests.get("https://br.indeed.com/viewjob?jk=0000000000000000",
                     headers={"User-Agent": UA}, timeout=25)
    print(f"  HTTP {r.status_code}  cf-mitigated={r.headers.get('cf-mitigated')}")
    print("  (403/challenge aqui confirma que o site não serve para checagem)")


if __name__ == "__main__":
    for fn in (sondar_onm, sondar_linkedin, sondar_gupy, sondar_indeed):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  SONDA FALHOU: {type(exc).__name__}: {exc}")
