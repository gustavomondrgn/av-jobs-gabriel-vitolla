"""Reconstrói o regime de contratação (PJ / CLT) das vagas já processadas.

O bot nunca extraiu esse campo: até hoje PJ é preferência no `profile.md`, não
critério de corte. Então `job_events` guarda título, empresa, salário, URL e o
motivo da IA — e nenhuma coluna diz PJ ou CLT. A descrição também não é gravada.

Este script refaz o caminho de trás pra frente:

    banco → URL de cada vaga → descrição na fonte → IA diz o regime

O que estiver fora do ar não volta (404). Nesses casos sobra uma heurística por
palavra-chave sobre título + salário + motivo da IA, e a linha sai marcada como
`heuristica` — nunca somada junto com o que a IA leu de fato. Vaga sem nenhum
sinal vira `nao_recuperavel` e aparece no relatório como tal: um total que
esconde o que não foi possível medir é pior do que não ter total.

Uso:

    export DATABASE_URL=postgresql://...       # produção
    python scripts/regime_pj.py --desde 2026-08-01 --csv regime.csv

    python scripts/regime_pj.py --sem-ia       # só heurística, não gasta cota
    python scripts/regime_pj.py --limite 50    # amostra, pra conferir antes

O cache em `scripts/.cache-regime.jsonl` guarda o veredito por uid. Rodar de
novo não recompra descrição nem cota de IA; só as vagas novas custam.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import requests  # noqa: E402

from bot import vitality  # noqa: E402
from bot.sources import strip_html  # noqa: E402

log = logging.getLogger("regime")

# Dois caches, e a separação é o ponto: buscar a descrição custa requisição na
# fonte e só dá certo enquanto o anúncio está no ar; classificar é de graça e
# muda toda vez que a regra é ajustada. Juntar os dois num cache só obrigaria a
# rebuscar 1.000 anúncios a cada vírgula mexida na classificação.
CACHE_DESC = Path(__file__).resolve().parent / ".cache-descricoes.jsonl"
CACHE_IA = Path(__file__).resolve().parent / ".cache-regime-ia.jsonl"
TIMEOUT = 20
INDEED_LOTE = 12
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

REGIMES = ("nao_clt", "clt", "ambos", "nao_informado")
BARRADOS = ("skipped", "ingles", "senior", "sem_remoto")


# ---------------------------------------------------------------------------
# 1. Banco
# ---------------------------------------------------------------------------

SQL = """
SELECT uid, source, title, company, url, salary, reason, role_type,
       status, local_day, closed_at
  FROM job_events
 WHERE local_day >= %s
 ORDER BY local_day, uid
"""


class Vaga:
    """Uma vaga única, com todos os eventos dela colapsados numa linha só.

    O banco tem uma linha por (uid, status): a mesma vaga aparece como `queued`
    e depois como `sent`. Contar as linhas cruas dobraria a vaga publicada e
    deixaria a barrada valendo um — exatamente o viés que a pergunta não pode
    ter.
    """

    __slots__ = ("uid", "source", "title", "company", "url", "salary", "reason",
                 "role_type", "dia", "status", "publicada", "fechada")

    def __init__(self, linha: dict[str, Any]) -> None:
        self.uid = linha["uid"]
        self.source = linha["source"]
        self.title = linha["title"] or ""
        self.company = linha["company"] or ""
        self.url = linha["url"] or ""
        self.salary = linha["salary"] or ""
        self.reason = linha["reason"] or ""
        self.role_type = linha["role_type"] or ""
        self.dia = linha["local_day"]
        self.status = linha["status"]
        self.publicada = linha["status"] == "sent"
        self.fechada = linha["closed_at"] is not None

    def absorver(self, linha: dict[str, Any]) -> None:
        # O dia que conta é o primeiro em que a vaga apareceu, não o da
        # publicação: a pergunta é "quantas PJ CHEGARAM por dia".
        if linha["local_day"] < self.dia:
            self.dia = linha["local_day"]
        if linha["status"] == "sent":
            self.publicada = True
            self.status = "sent"
        elif not self.publicada and linha["status"] in BARRADOS:
            self.status = linha["status"]
        # Campos textuais: fica o mais longo, que costuma ser o mais completo.
        for campo in ("title", "company", "url", "salary", "reason", "role_type"):
            novo = linha.get(campo) or ""
            if len(novo) > len(getattr(self, campo)):
                setattr(self, campo, novo)
        if linha["closed_at"] is not None:
            self.fechada = True

    @property
    def source_id(self) -> str:
        """`onm:12345` → `12345`. É a chave que as APIs de detalhe pedem.

        O Indeed tem uma pegadinha: o `python-jobspy` entrega o id já prefixado
        com `in-`, e é assim que ele foi gravado no banco. A API do Indeed não
        conhece esse prefixo — manda a chave com `in-` e ela responde lista
        vazia, como se a vaga não existisse. Aqui o prefixo sai.
        """
        bruto = self.uid.split(":", 1)[1] if ":" in self.uid else self.uid
        if self.source == "indeed" and bruto.startswith("in-"):
            return bruto[3:]
        return bruto

    @property
    def destino(self) -> str:
        return "publicada" if self.publicada else "barrada"


def _agrupar(linhas: Iterable[dict[str, Any]]) -> list[Vaga]:
    vagas: dict[str, Vaga] = {}
    for linha in linhas:
        uid = linha["uid"]
        if uid in vagas:
            vagas[uid].absorver(linha)
        else:
            vagas[uid] = Vaga(linha)
    return list(vagas.values())


def carregar_csv(caminho: str) -> list[Vaga]:
    """Lê o dump do `\\copy` da VPS.

    O Postgres de produção roda dentro do Docker e não tem porta aberta pra
    fora — expor um banco na internet para responder uma pergunta seria um
    preço alto. O dump resolve igual, e ainda deixa a análise reproduzível sem
    depender do banco estar de pé.
    """
    with open(caminho, encoding="utf-8", newline="") as f:
        linhas = []
        for r in csv.DictReader(f):
            r["local_day"] = date.fromisoformat(r["local_day"])
            r["closed_at"] = r.get("closed_at") or None
            linhas.append(r)
    return _agrupar(linhas)


def carregar_vagas(dsn: str, desde: date) -> list[Vaga]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, connect_timeout=15) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SQL, (desde,))
            return _agrupar(cur)


# ---------------------------------------------------------------------------
# 2. Rebuscar a descrição na fonte
# ---------------------------------------------------------------------------

def _token_onm() -> str | None:
    email, senha = os.getenv("ONM_EMAIL", ""), os.getenv("ONM_PASSWORD", "")
    if not (email and senha):
        log.warning("Sem ONM_EMAIL/ONM_PASSWORD — vagas do ONM ficam sem descrição")
        return None
    try:
        from bot.sources import ONMSource
        return ONMSource(email, senha).login()
    except Exception as exc:  # noqa: BLE001
        log.warning("Login no ONM falhou (%s) — ONM fica sem descrição", exc)
        return None


def descricao_onm(source_id: str, token: str | None) -> str:
    if not token:
        return ""
    try:
        resp = requests.get(vitality.ONM_DETALHE.format(source_id),
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=TIMEOUT)
        if not resp.ok:
            return ""
        return strip_html(resp.json().get("description"))
    except (requests.RequestException, ValueError):
        return ""


def descricao_gupy(source_id: str) -> str:
    try:
        resp = requests.get(vitality.GUPY_DETALHE.format(source_id),
                            headers={"User-Agent": _UA}, timeout=TIMEOUT)
        if not resp.ok:
            return ""
        corpo = resp.json()
        d = corpo.get("data") if isinstance(corpo.get("data"), dict) else corpo
        partes = [d.get(c) for c in ("description", "responsibilities", "prerequisites")]
        return strip_html(" ".join(p for p in partes if p))
    except (requests.RequestException, ValueError, AttributeError):
        return ""


def descricao_linkedin(source_id: str) -> str:
    try:
        resp = requests.get(vitality.LINKEDIN_DETALHE.format(source_id),
                            headers={"User-Agent": _UA}, timeout=TIMEOUT)
        return strip_html(resp.text) if resp.ok else ""
    except requests.RequestException:
        return ""


def descricoes_indeed(chaves: list[str]) -> dict[str, str]:
    """Em lote: o Indeed devolve até 12 descrições por requisição.

    Mesmo endpoint que o `vitality` usa para saber se a vaga morreu, só que
    pedindo o corpo do anúncio junto. Chave que não volta é vaga fora do ar.
    """
    out: dict[str, str] = {}
    chaves = [c for c in chaves if c]
    if not chaves:
        return out
    try:
        from jobspy.indeed.constant import api_headers
        headers = {**api_headers, "indeed-co": "BR"}
    except ImportError:
        log.warning("jobspy indisponível — Indeed fica sem descrição")
        return out

    for i in range(0, len(chaves), INDEED_LOTE):
        lote = chaves[i:i + INDEED_LOTE]
        lista = ",".join('"' + c + '"' for c in lote)
        query = ("query { jobData(input: {jobKeys: [" + lista + "]}) "
                 "{ results { job { key description { html } } } } }")
        try:
            resp = requests.post(vitality.INDEED_GRAPHQL, headers=headers,
                                 json={"query": query}, timeout=TIMEOUT)
            if not resp.ok:
                continue
            dados = resp.json()
            for r in (dados.get("data", {}).get("jobData", {}).get("results") or []):
                job = r.get("job") or {}
                chave = job.get("key")
                if chave:
                    out[chave] = strip_html((job.get("description") or {}).get("html"))
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            log.debug("Lote do Indeed falhou: %s", exc)
    return out


# ---------------------------------------------------------------------------
# 3. Classificação
# ---------------------------------------------------------------------------

INSTRUCOES = """Você lê um anúncio de vaga em português e responde UMA coisa:
o trabalho anunciado é vínculo CLT ou não é?

- "nao_clt" — não é vínculo celetista. Vale tanto quando o anúncio nomeia o
  regime (PJ, pessoa jurídica, MEI, prestação de serviço, nota fiscal,
  autônomo, cooperado) quanto quando a NATUREZA do trabalho já exclui CLT:
  freelance, trabalho por projeto, serviço pontual, por demanda, por hora
  avulsa, diária, sem vínculo empregatício.
- "clt" — o anúncio diz CLT, carteira assinada, registro em carteira, regime
  celetista, efetivação, ou oferece benefícios que só existem em vínculo
  celetista (vale-transporte, vale-refeição/alimentação, FGTS, 13º, férias
  remuneradas).
- "ambos" — oferece as duas coisas ("CLT ou PJ", "a combinar entre CLT e PJ").
- "nao_informado" — você leu o anúncio inteiro e ele não diz nem sugere nada
  sobre isso.

O que NÃO conta como sinal, e você deve ignorar:
- ser remoto. Vaga remota não é PJ por ser remota.
- o tamanho ou o nome da empresa. Empresa grande não é CLT por ser grande.
- o valor pago, sozinho.
- estágio, aprendiz e temporário: nenhum dos três é PJ. Se o anúncio for só
  isso e nada mais, responda "nao_informado".

Na dúvida entre "clt" e "nao_clt", responda "nao_informado". Anúncio que não
fala do assunto é o caso mais comum e é uma resposta correta — inventar um
regime que o texto não tem estraga a contagem inteira.

Em "evidencia", devolva o trecho exato do anúncio que sustenta a resposta (até
120 caracteres). Se for "nao_informado", devolva "".
"""

ESQUEMA = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": list(REGIMES)},
        "evidencia": {"type": "string"},
    },
    "required": ["regime", "evidencia"],
}

# Termos "fortes" nomeiam o regime e decidem sozinhos. Os "fracos" são indícios
# que só valem quando nenhum forte apareceu — e a evidência sai marcada como
# fraca, para dar pra separar depois se a conta ficar dependendo deles.
# As siglas ficam num padrão à parte, sem `re.IGNORECASE`: "PJ" minúsculo não
# existe em anúncio, e ignorar a caixa faria "MEI" casar com nome próprio e
# "NF" com qualquer coisa. Já as palavras inteiras precisam ignorar a caixa,
# porque "Pessoa Jurídica" aparece capitalizada em metade dos anúncios.
FORTE_NAO_CLT_SIGLA = re.compile(r"\bPJ\b|\bP\.J\.|\bMEI\b")
FORTE_NAO_CLT = re.compile(
    r"pessoa jur[ií]dica|prestador[ae]?s? de servi[çc]os?|"
    r"presta[çc][ãa]o de servi[çc]os?|contrato de presta[çc][ãa]o|"
    r"aut[ôo]nom[oa]s?\b|freelanc\w*|\bfreela\b|por projeto|"
    r"trabalho pontual|servi[çc]o pontual|cooperad[oa]s?\b|"
    r"sem v[íi]nculo empregat[íi]cio|sem v[íi]nculo\b", re.I)
FORTE_CLT = re.compile(
    r"\bCLT\b|carteira assinada|registro em carteira|carteira de trabalho|"
    r"celetista|regime da consolida[çc][ãa]o|ap[óo]s? per[íi]odo de experi[êe]ncia,?"
    r" efetiva", re.I)
FRACO_CLT = re.compile(
    r"vale[- ]transporte|vale[- ]refei[çc][ãa]o|vale[- ]alimenta[çc][ãa]o|"
    r"\bVT\b|\bVR\b|\bVA\b|\bFGTS\b|13[ºo°]? sal[áa]rio|f[ée]rias remunerada", re.I)
FRACO_NAO_CLT = re.compile(
    # "nota fiscal" desceu de forte para fraco: numa vaga de assistente
    # financeiro, emitir nota fiscal é a TAREFA do cargo, não a forma de
    # pagamento. Como esse é justamente o público do bot, o termo errava para
    # o lado que mais engana.
    # "diária" sozinha pega "busca diária de excelência". Só vale com dinheiro
    # do lado, que é quando a palavra fala mesmo de forma de pagamento.
    r"nota fiscal|\bNFe\b|por hora|/hora|hora trabalhada|por demanda|"
    r"di[áa]rias? de R\$|valor da di[áa]ria|pagamento por di[áa]ria|"
    r"por entrega|comiss[ãa]o pura|100% comiss", re.I)


# Armadilhas do português. "Atuar de forma autônoma" é trabalhar com autonomia,
# não ser autônomo — e aparece em anúncio CLT o tempo todo. "Empresa de
# prestação de serviços" descreve o negócio do anunciante, não o contrato de
# quem for contratado. Sem esta lista, os dois viram falso "não-CLT" e a
# resposta inteira infla para o lado que o cliente quer ouvir, que é o pior
# jeito de errar.
ARMADILHAS = re.compile(
    r"de (?:forma|maneira|modo) aut[ôo]noma|com autonomia|"
    r"autonomia (?:para|na|no|de)|perfil aut[ôo]nomo|postura aut[ôo]noma|"
    r"(?:empresa|companhia|grupo|multinacional|l[íi]der|refer[êe]ncia|"
    r"setor|ramo|[áa]rea|neg[óo]cio)s? (?:de|em|no|na) presta[çc][ãa]o de "
    r"servi[çc]os?|presta[çc][ãa]o de servi[çc]os? (?:de sa[úu]de|"
    r"educacionais|p[úu]blicos|ao cliente|de limpeza)|"
    # "PJ" como segmento de cliente, não como contrato. Vaga de banco e de
    # vendas B2B fala de "carteira PJ" e "clientes PJ" o tempo todo, e ler isso
    # como regime transformaria vaga celetista de bancário em vaga PJ.
    r"(?:clientes?|carteiras?|segmentos?|p[úu]blicos?|contas?|mercados?|"
    r"portf[óo]lios?|atendimento a|vendas? para|foco em) (?:de )?"
    r"(?:PJ|pessoas? jur[ií]dicas?)|"
    r"PJ (?:de varejo|e PF|/ ?PF|ou PF)|\bPF (?:e|/|ou) PJ\b|"
    # Documento do cliente, não contrato de quem se candidata.
    r"documenta[çc][ãa]o do cliente|cliente,? pessoa jur[ií]dica|"
    r"pessoa jur[ií]dica ou pessoa f[íi]sica|"
    r"pessoa f[íi]sica ou pessoa jur[ií]dica|"
    # A empresa se apresentando: "Prestadora de serviços de TI busca analista".
    # Aqui "prestadora" é o anunciante, e o vínculo oferecido costuma ser CLT.
    r"(?:somos uma |a )?prestadora de servi[çc]os?", re.I)

JANELA = 70

# Palavras que denunciam que o trecho fala do CONTRATO de quem vai ser
# contratado. Sem elas, "PJ" quase sempre é outra coisa: segmento de cliente
# ("crédito PJ"), perfil do público ("advogadas autônomas"), tarefa do cargo
# ("gestão de pagamento de freelances") ou até agente de software ("agentes
# autônomos"). Medido numa amostra de 30 vereditos em 20/08/2026: 16 eram
# falso positivo, todos por falta desse contexto.
CONTEXTO_CONTRATO = re.compile(
    r"contrat\w*|regime|modalidade|modelo|\btipo\b|v[íi]nculo|admiss[ãa]o|"
    r"\bCNPJ\b|\bCLT\b|honor[áa]rio|mediante nota|emiss[ãa]o de nota|\bRPA\b|"
    r"busca\w*|procura\w*|precisa\w* de|seleciona\w*|\bvaga\b|"
    r"remunera\w*|(?:atuar|trabalhar) como", re.I)

# ...menos quando a palavra de contrato é de OUTRO contrato que não o seu.
# "regime de tributação" fala do imposto do cliente, não do vínculo da vaga.
CONTEXTO_FALSO = re.compile(
    r"regimes? de tributa[çc][ãa]o|pagamento de freela|gest[ãa]o de freela|"
    r"contrata[çc][ãa]o de freela|contratar freela|"
    r"contrata[çc][ãa]o de prestador|fornecedores e prestador", re.I)

# Termos que dispensam o contexto: quem escreve isso está falando do vínculo e
# de nada mais.
INEQUIVOCO = re.compile(
    r"sem v[íi]nculo empregat[íi]cio|contrato de presta[çc][ãa]o de servi[çc]o|"
    r"regime (?:de contrata[çc][ãa]o )?PJ|contrata[çc][ãa]o (?:no modelo |via )?PJ|"
    r"modelo (?:de contrata[çc][ãa]o )?:? ?PJ|\bPJ\b ?[-–|] ?(?:CNPJ|remoto)|"
    r"\bMEI\b ?/|via MEI|"
    # "(PJ Remoto)" no título, "(PJ)" depois do cargo. Entre parênteses e
    # colado no nome da vaga, PJ é o contrato — não sobra outro sentido.
    r"\(PJ\b(?![^)]*\b(?:e|ou|/) ?PF)", re.I)


def _achar(padrao: re.Pattern[str], texto: str,
           filtrar: bool = False) -> re.Match[str] | None:
    """Primeira ocorrência do padrão.

    `filtrar` só vale para os padrões de não-CLT: as armadilhas são frases que
    fingem ser "não-CLT" e não têm nada a ver com o lado CLT. Aplicar o filtro
    nos dois lados faria um "de forma autônoma" perto de um "CLT" apagar o
    "CLT" também, e o anúncio inteiro sairia como indefinido.
    """
    for m in padrao.finditer(texto):
        if not filtrar:
            return m
        redor = texto[max(0, m.start() - JANELA):m.end() + JANELA]
        if ARMADILHAS.search(redor) or CONTEXTO_FALSO.search(redor):
            continue
        # Ou a expressão já é inequívoca por si, ou precisa de uma palavra de
        # contrato por perto. "PJ" solto no meio de um texto não diz nada.
        if INEQUIVOCO.search(redor) or CONTEXTO_CONTRATO.search(redor):
            return m
    return None


def _trecho(texto: str, m: re.Match[str]) -> str:
    """A evidência com um pedaço do redor — sem isso não dá pra auditar nada."""
    redor = texto[max(0, m.start() - 45):m.end() + 45].strip()
    return re.sub(r"\s+", " ", redor)[:120]


def heuristica(texto: str) -> tuple[str, str]:
    """Devolve (regime, evidência). `""` quando não há sinal nenhum.

    Os dois regimes aparecendo no mesmo anúncio não é empate a resolver: é o
    anúncio dizendo "CLT ou PJ, você escolhe", que é uma resposta em si.
    """
    m_nc = (_achar(FORTE_NAO_CLT_SIGLA, texto, filtrar=True)
            or _achar(FORTE_NAO_CLT, texto, filtrar=True))
    m_clt = _achar(FORTE_CLT, texto)
    if m_nc and m_clt:
        return "ambos", _trecho(texto, m_nc) + "  ||  " + _trecho(texto, m_clt)
    if m_nc:
        return "nao_clt", _trecho(texto, m_nc)
    if m_clt:
        return "clt", _trecho(texto, m_clt)
    for padrao, regime in ((FRACO_CLT, "clt"), (FRACO_NAO_CLT, "nao_clt")):
        m = _achar(padrao, texto, filtrar=regime == "nao_clt")
        if m:
            return regime, "indício fraco: " + _trecho(texto, m)
    return "", ""


def sinal_estrutural(v: "Vaga") -> tuple[str, str]:
    """Sinal que vem da própria plataforma, não do texto do anúncio.

    O Mercado de Trabalho separa "vaga" de "projeto" na própria URL. Projeto
    ali é trabalho freelance por definição — não existe projeto celetista. É o
    sinal mais confiável do conjunto, porque não depende de o anunciante ter
    escrito a palavra certa.
    """
    if v.source == "onm" and "/projetos/" in v.url:
        return "nao_clt", "projeto freelance no O Mercado de Trabalho"
    return "", ""


class Classificador:
    """Gemini com o mesmo modelo do bot. Sem chave, o script roda só na heurística."""

    def __init__(self, ativo: bool = True) -> None:
        self.cliente = None
        self.modelo = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
        chave = os.getenv("GEMINI_API_KEY", "").strip()
        if not (ativo and chave):
            return
        try:
            from google import genai
            self.cliente = genai.Client(api_key=chave)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini indisponível (%s) — só heurística", exc)

    def classificar(self, titulo: str, descricao: str) -> tuple[str, str] | None:
        if self.cliente is None:
            return None
        from google.genai import types as genai_types
        prompt = (INSTRUCOES + "\n\n=== ANÚNCIO ===\nTítulo: " + titulo + "\n\n"
                  + descricao[:12000])
        try:
            resp = self.cliente.models.generate_content(
                model=self.modelo, contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ESQUEMA, temperature=0.0),
            )
            d = json.loads(resp.text)
            return d.get("regime", "nao_informado"), (d.get("evidencia") or "")[:120]
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha classificando %r: %s", titulo[:40], exc)
            return None


# ---------------------------------------------------------------------------
# 4. Cache
# ---------------------------------------------------------------------------

def ler_cache(caminho: Path) -> dict[str, dict[str, Any]]:
    if not caminho.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with caminho.open(encoding="utf-8") as f:
        for linha in f:
            if not linha.strip():
                continue
            try:
                d = json.loads(linha)
                out[d["uid"]] = d
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def gravar_cache(caminho: Path, registro: dict[str, Any]) -> None:
    with caminho.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 5. Relatório
# ---------------------------------------------------------------------------

def montar_relatorio(registros: list[dict[str, Any]]) -> str:
    por_dia: dict[Any, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in registros:
        d = por_dia[r["dia"]]
        d["total"] += 1
        d[r["regime"] or "nao_recuperavel"] += 1
        d["origem_" + r["origem"]] += 1
        if r["origem"] in ("ia", "descricao_termo", "descricao_sem_mencao",
                           "plataforma"):
            d["leu_anuncio"] += 1
        if r["regime"] in ("nao_clt", "ambos"):
            d["nc_publicada" if r["destino"] == "publicada" else "nc_barrada"] += 1
        if r["destino"] == "publicada":
            d["publicadas"] += 1

    linhas = [
        "Dia          Total  Não-CLT   CLT  CLT ou PJ  Não diz  Não recup.  |  "
        "não-CLT publ./barr.",
        "-" * 100,
    ]
    tot: dict[str, int] = defaultdict(int)
    for dia in sorted(por_dia):
        d = por_dia[dia]
        for k, v in d.items():
            tot[k] += v
        linhas.append(
            "{:<12} {:>5} {:>8} {:>5} {:>10} {:>8} {:>11}  |  {:>5} / {:<5}".format(
                str(dia), d["total"], d["nao_clt"], d["clt"], d["ambos"],
                d["nao_informado"], d["nao_recuperavel"],
                d["nc_publicada"], d["nc_barrada"]))
    linhas.append("-" * 100)
    linhas.append(
        "{:<12} {:>5} {:>8} {:>5} {:>10} {:>8} {:>11}  |  {:>5} / {:<5}".format(
            "TOTAL", tot["total"], tot["nao_clt"], tot["clt"], tot["ambos"],
            tot["nao_informado"], tot["nao_recuperavel"],
            tot["nc_publicada"], tot["nc_barrada"]))

    dias = max(len(por_dia), 1)
    linhas += [
        "",
        "Vagas analisadas: {} em {} dia(s) — média {:.1f}/dia".format(
            tot["total"], dias, tot["total"] / dias),
        "",
        "De onde saiu cada veredito:",
        "  consegui olhar o anúncio ......... {} ({:.0%})".format(
            tot["leu_anuncio"], tot["leu_anuncio"] / max(tot["total"], 1)),
        "     · pela IA ..................... {}".format(tot["origem_ia"]),
        "     · pelo termo no texto ......... {}".format(tot["origem_descricao_termo"]),
        "     · a plataforma já diz ......... {}".format(tot["origem_plataforma"]),
        "     · anúncio não menciona nada ... {}".format(
            tot["origem_descricao_sem_mencao"]),
        "  anúncio fora do ar ............... {}".format(
            tot["origem_banco_termo"] + tot["origem_nao_recuperavel"]),
        "     · termo no título/salário ..... {}".format(tot["origem_banco_termo"]),
        "     · sem sinal nenhum ............ {}".format(tot["origem_nao_recuperavel"]),
        "",
        "A pergunta do Gabriel — quantas NÃO são CLT:",
        "  não-CLT confirmada ............... {} ({:.0%} do total) — {:.1f}/dia".format(
            tot["nao_clt"] + tot["ambos"],
            (tot["nao_clt"] + tot["ambos"]) / max(tot["total"], 1),
            (tot["nao_clt"] + tot["ambos"]) / dias),
        "  CLT confirmada ................... {} ({:.0%})".format(
            tot["clt"], tot["clt"] / max(tot["total"], 1)),
        "  o anúncio não deixa saber ........ {} ({:.0%})".format(
            tot["nao_informado"] + tot["nao_recuperavel"],
            (tot["nao_informado"] + tot["nao_recuperavel"]) / max(tot["total"], 1)),
        "",
        "Se o corte fosse por regime, passariam a concorrer por dia:",
        "  só o que é não-CLT declarado ..... {:.1f}/dia".format(
            (tot["nao_clt"] + tot["ambos"]) / dias),
        "  não-CLT + as indefinidas ......... {:.1f}/dia".format(
            (tot["nao_clt"] + tot["ambos"] + tot["nao_informado"]
             + tot["nao_recuperavel"]) / dias),
        "  como está hoje (sem corte) ....... {:.1f}/dia".format(tot["total"] / dias),
        "",
        "Lembrete: 'concorrer' não é 'ser publicada'. O teto diário continua valendo,",
        "então cortar CLT muda quem ganha a disputa, não quantas mensagens o grupo recebe.",
    ]
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------

def buscar_descricoes(vagas: list[Vaga]) -> dict[str, str]:
    """Baixa o que ainda falta e devolve `uid -> descrição` para todas.

    Roda uma vez; da segunda em diante o cache responde. Descrição vazia
    também é gravada — significa "anúncio fora do ar", e sem gravar isso o
    script tentaria ressuscitar os mesmos 404 a cada execução.
    """
    cache = ler_cache(CACHE_DESC)
    faltam = [v for v in vagas if v.uid not in cache]
    if not faltam:
        log.info("Todas as %d descrições já estavam em cache", len(vagas))
        return {u: d.get("desc", "") for u, d in cache.items()}

    log.info("Buscando %d descrições (%d já em cache)", len(faltam), len(cache))
    token = _token_onm() if any(v.source == "onm" for v in faltam) else None
    lote = descricoes_indeed([v.source_id for v in faltam if v.source == "indeed"])

    for i, v in enumerate(faltam, 1):
        if v.source == "onm":
            desc = descricao_onm(v.source_id, token)
        elif v.source == "gupy":
            desc = descricao_gupy(v.source_id)
        elif v.source == "linkedin":
            desc = descricao_linkedin(v.source_id)
        else:
            desc = lote.get(v.source_id, "")
        registro = {"uid": v.uid, "desc": desc}
        gravar_cache(CACHE_DESC, registro)
        cache[v.uid] = registro
        if i % 50 == 0:
            log.info("%d/%d descrições", i, len(faltam))

    return {u: d.get("desc", "") for u, d in cache.items()}


def processar(vagas: list[Vaga], clf: Classificador,
              descricoes: dict[str, str]) -> list[dict[str, Any]]:
    cache_ia = ler_cache(CACHE_IA)
    registros: list[dict[str, Any]] = []

    for v in vagas:
        desc = descricoes.get(v.uid, "")

        # A origem do veredito importa tanto quanto o veredito. Ler o anúncio
        # inteiro e não achar menção a regime É a resposta ("não informado");
        # não conseguir o anúncio é outra coisa, e as duas não podem virar a
        # mesma linha no relatório.
        regime, evidencia = sinal_estrutural(v)
        origem = "plataforma" if regime else ""

        if not regime and len(desc) > 80:
            if v.uid in cache_ia:
                regime = cache_ia[v.uid]["regime"]
                evidencia = cache_ia[v.uid].get("evidencia", "")
                origem = "ia"
            else:
                r = clf.classificar(v.title, desc)
                if r is not None:
                    regime, evidencia = r
                    origem = "ia"
                    gravar_cache(CACHE_IA, {"uid": v.uid, "regime": regime,
                                            "evidencia": evidencia})
                else:
                    regime, evidencia = heuristica(desc)
                    origem = "descricao_termo" if regime else "descricao_sem_mencao"
                    if not regime:
                        regime = "nao_informado"
        elif not regime:
            # Anúncio fora do ar. Sobra o que o banco guardou — título, salário
            # e o motivo da IA. Pega pouco, e a linha fica marcada como tal.
            regime, evidencia = heuristica(
                v.title + " " + v.salary + " " + v.reason)
            origem = "banco_termo" if regime else "nao_recuperavel"

        registros.append({"uid": v.uid, "source": v.source, "title": v.title[:120],
                          "regime": regime, "evidencia": evidencia,
                          "origem": origem, "status": v.status,
                          "dia": v.dia, "destino": v.destino})

    return registros


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Regime PJ/CLT das vagas já processadas")
    p.add_argument("--desde", help="AAAA-MM-DD (padrão: 30 dias atrás)")
    p.add_argument("--entrada", help="dump CSV do job_events (dispensa o banco)")
    p.add_argument("--dsn", default=os.getenv("DATABASE_URL", ""))
    p.add_argument("--limite", type=int, help="processa só as N primeiras vagas")
    p.add_argument("--sem-ia", action="store_true",
                   help="só heurística, sem gastar cota")
    p.add_argument("--csv", help="grava o detalhe vaga a vaga neste arquivo")
    a = p.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if a.entrada:
        vagas = carregar_csv(a.entrada)
        if a.desde:
            corte = date.fromisoformat(a.desde)
            vagas = [v for v in vagas if v.dia >= corte]
    elif a.dsn:
        desde = date.fromisoformat(a.desde) if a.desde else date.today() - timedelta(days=30)
        vagas = carregar_vagas(a.dsn, desde)
    else:
        print("Falta --entrada (dump CSV) ou DATABASE_URL/--dsn.", file=sys.stderr)
        return 2
    log.info("%d vagas únicas", len(vagas))
    if a.limite:
        vagas = vagas[:a.limite]

    descricoes = buscar_descricoes(vagas)
    registros = processar(vagas, Classificador(ativo=not a.sem_ia), descricoes)

    if a.csv:
        with open(a.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["dia", "source", "uid", "title",
                                              "regime", "origem", "destino",
                                              "status", "evidencia"])
            w.writeheader()
            for r in sorted(registros, key=lambda x: (str(x["dia"]), x["source"])):
                w.writerow({k: r.get(k, "") for k in w.fieldnames})
        log.info("Detalhe gravado em %s", a.csv)

    print()
    print(montar_relatorio(registros))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
