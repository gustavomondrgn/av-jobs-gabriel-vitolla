"""Fontes de vagas.

Cada fonte sabe buscar na sua plataforma e devolver uma lista de `Job`
normalizado. Todo o resto do bot (classificador, filtro, Telegram) trabalha só
com `Job` e não sabe de onde a vaga veio.

Para adicionar uma fonte: escreva uma classe com `name`, `fetch()` e
`prefilter_remote`, e registre em `build_sources()` no fim do arquivo.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("av-jobs-bot.sources")

REQUEST_TIMEOUT = 30

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def strip_html(texto: str | None) -> str:
    """Tira tags e entidades HTML — várias fontes devolvem a descrição em HTML."""
    if not texto:
        return ""
    limpo = _TAG_RE.sub(" ", texto)
    limpo = html_mod.unescape(limpo)
    limpo = _WS_RE.sub(" ", limpo)
    return re.sub(r"\n{3,}", "\n\n", limpo).strip()


# ---------------------------------------------------------------------------
# Vaga normalizada
# ---------------------------------------------------------------------------

@dataclass
class Job:
    """Uma vaga, no formato que o resto do bot entende."""

    source: str
    source_id: str
    title: str
    url: str
    description: str = ""
    company: str = ""
    location: str = ""
    published_at: str = ""
    skills: list[str] = field(default_factory=list)
    job_type: str = ""       # rótulo legível: "Vaga (CLT/PJ)", "Projeto freelance"
    category: str = ""       # área/profissão informada pela fonte
    salary: str = ""         # remuneração como texto, quando a fonte informa
    salary_min: float | None = None
    salary_max: float | None = None
    remote_hint: str = ""    # o que a FONTE afirma: remoto/hibrido/presencial/""

    @property
    def uid(self) -> str:
        """ID único global. Sem o prefixo, IDs de fontes diferentes colidiriam."""
        return f"{self.source}:{self.source_id}"

    def to_classifier_text(self) -> str:
        """Serializa a vaga no blob de texto que vai pro classificador."""
        partes: list[str] = []
        if self.job_type:
            partes.append(f"Tipo: {self.job_type}")
        partes.append(f"Título: {self.title}")
        if self.company:
            partes.append(f"Empresa: {self.company}")
        if self.category:
            partes.append(f"Categoria: {self.category}")
        if self.location:
            partes.append(f"Local informado: {self.location}")
        if self.remote_hint:
            partes.append(f"Modalidade informada pela plataforma: {self.remote_hint}")
        if self.skills:
            partes.append(f"Skills: {', '.join(self.skills)}")
        partes.append(f"Descrição: {self.description or '(sem descrição)'}")
        return "\n".join(partes)


class SourceError(Exception):
    """Falha ao buscar numa fonte. Não derruba o ciclo — as outras seguem."""


# ---------------------------------------------------------------------------
# Base: ritmo próprio por fonte
# ---------------------------------------------------------------------------

class BaseSource:
    """Cada fonte tem seu próprio intervalo.

    O ONM custa 1 requisição por ciclo e pode ser consultado de 10 em 10 min.
    Gupy, Indeed e LinkedIn custam uma requisição POR TERMO de busca — com 23
    termos, consultar de 10 em 10 minutos daria ~3.300 requisições/dia numa API
    pública e gratuita. Bater menos é mais seguro e não perde vaga: uma vaga
    publicada às 14h05 é notificada às 15h em vez de às 14h10.
    """

    name = "base"
    label = "Base"
    prefilter_remote = False
    default_interval = 600

    def __init__(self, interval_seconds: int | None = None) -> None:
        self.interval_seconds = (
            self.default_interval if interval_seconds is None else interval_seconds
        )
        self._last_fetch: float | None = None
        # Relógio de parede, só para exibir no /status — o agendamento usa
        # monotonic, que não anda pra trás se o relógio do host for ajustado.
        self.last_fetch_at: datetime | None = None
        self.last_count: int | None = None

    def is_due(self, agora: float) -> bool:
        """Já passou o intervalo desta fonte? Na primeira vez, sempre sim."""
        if self._last_fetch is None:
            return True
        return (agora - self._last_fetch) >= self.interval_seconds

    def mark_fetched(self, agora: float) -> None:
        self._last_fetch = agora
        self.last_fetch_at = datetime.now(timezone.utc)

    def segundos_para_proxima(self, agora: float) -> int:
        """Quanto falta para esta fonte rodar de novo (0 = já pode)."""
        if self._last_fetch is None:
            return 0
        return max(0, int(self.interval_seconds - (agora - self._last_fetch)))

    def fetch(self) -> list[Job]:  # pragma: no cover - contrato
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ONM — O Mercado de Trabalho
# ---------------------------------------------------------------------------

class AuthError(SourceError):
    """Token expirado ou credenciais inválidas."""


class ONMSource(BaseSource):
    """Fonte original. API JSON com login; guarda o JWT em memória."""

    name = "onm"
    label = "O Mercado de Trabalho"
    # As vagas do ONM quase nunca informam modalidade, e a regra do Gabriel é
    # não descartar por omissão. Então nada de exigir palavra de remoto aqui.
    prefilter_remote = False
    # Uma requisição por ciclo: barato, pode ser frequente.
    default_interval = 600

    LOGIN_URL = "https://auth.onovomercado.com.br/api/auth/login"
    PROJECTS_URL = (
        "https://api.onovomercado.com.br/mercado-de-trabalho/v1/projects"
        "?page=1&limit=15&sortDir=DESC"
    )
    JWT_COOKIE_NAME = "onm-sso-jwt-token"

    def __init__(self, email: str, password: str,
                 interval_seconds: int | None = None) -> None:
        super().__init__(interval_seconds)
        self.email = email
        self.password = password
        self._token: str | None = None

    def login(self) -> str:
        log.info("Logging in to ONM as %s", self.email)
        resp = requests.post(
            self.LOGIN_URL,
            json={"email": self.email, "password": self.password, "client": "mdt"},
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        token = resp.cookies.get(self.JWT_COOKIE_NAME)
        if not token:
            raise AuthError(
                f"Login OK mas cookie {self.JWT_COOKIE_NAME} não veio na resposta"
            )
        log.info("Login successful (token len=%d)", len(token))
        self._token = token
        return token

    def _get_projects(self) -> list[dict[str, Any]]:
        resp = requests.get(
            self.PROJECTS_URL,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            raise AuthError("Token expirado (401)")
        resp.raise_for_status()
        return resp.json().get("content", []) or []

    def fetch(self) -> list[Job]:
        if self._token is None:
            self.login()
        try:
            raw = self._get_projects()
        except AuthError as exc:
            log.warning("%s — re-authenticating", exc)
            self.login()
            raw = self._get_projects()
        return [self._to_job(j) for j in raw if j.get("id") is not None]

    def _to_job(self, j: dict[str, Any]) -> Job:
        is_position = j.get("type") == "POSITION"
        profession = j.get("profession") or {}
        area = (profession.get("occupationArea") or {}).get("title") or ""
        prof_desc = profession.get("description") or ""
        categoria = " · ".join(p for p in (prof_desc, area) if p)

        skills = [
            s.get("description") for s in (j.get("skills") or [])
            if s and s.get("description")
        ]

        bmin, bmax = j.get("budgetMin"), j.get("budgetMax")
        return Job(
            source=self.name,
            source_id=str(j["id"]),
            title=(j.get("title") or "").strip(),
            url=f"https://omercadodetrabalho.com/"
                f"{'vagas' if is_position else 'projetos'}/{j['id']}",
            description=strip_html(j.get("description")),
            company=((j.get("author") or {}).get("name") or "").strip(),
            published_at=j.get("createdAt") or "",
            skills=skills,
            job_type="Vaga (CLT/PJ)" if is_position else "Projeto freelance",
            category=categoria,
            salary_min=bmin if bmin not in (None, 0, 0.0) else None,
            salary_max=bmax if bmax not in (None, 0, 0.0) else None,
        )


# ---------------------------------------------------------------------------
# Gupy
# ---------------------------------------------------------------------------

class GupySource(BaseSource):
    """Portal público da Gupy. API JSON aberta, sem login.

    Chamada por termo de busca, já filtrando `workplaceType=remote` no servidor.
    O filtro do servidor não é confiável (empresa preenche errado), mas corta a
    maior parte do volume — o classificador continua sendo a palavra final.
    """

    name = "gupy"
    label = "Gupy"
    # Já vem filtrado por remoto no servidor; exigir palavra-chave no texto
    # descartaria vaga remota legítima que não repete "home office" na descrição.
    prefilter_remote = False
    # Uma requisição por termo (~23) e o endpoint é lento: de hora em hora.
    default_interval = 3600

    API_URL = "https://employability-portal.gupy.io/api/v1/jobs"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    def __init__(self, terms_file: Path, limit_per_term: int = 20,
                 interval_seconds: int | None = None) -> None:
        super().__init__(interval_seconds)
        self.terms_file = terms_file
        self.limit_per_term = limit_per_term

    def fetch(self) -> list[Job]:
        vistos: set[str] = set()
        jobs: list[Job] = []
        # Relê o arquivo a cada ciclo (cache por mtime), igual ao profile.md.
        for termo in load_terms(self.terms_file):
            try:
                resp = requests.get(
                    self.API_URL,
                    headers=self.HEADERS,
                    params={
                        "jobName": termo,
                        "workplaceType": "remote",
                        # Sem isto a ordem não é garantida; com ele vem do mais
                        # novo pro mais antigo, que é o que o bot precisa.
                        "sortBy": "publishedDate",
                        "limit": self.limit_per_term,
                        "offset": 0,
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json().get("data") or []
            except (requests.RequestException, ValueError) as exc:
                # Um termo que falha não derruba os outros.
                log.warning("Gupy: termo %r falhou: %s", termo, exc)
                continue

            for item in data:
                job = self._to_job(item)
                if job is None or job.source_id in vistos:
                    continue
                vistos.add(job.source_id)
                jobs.append(job)
        return jobs

    def _to_job(self, item: dict[str, Any]) -> Job | None:
        job_id = item.get("id")
        if job_id is None:
            return None

        cidade = (item.get("city") or "").strip()
        estado = (item.get("state") or "").strip()
        local = ", ".join(p for p in (cidade, estado) if p)

        empresa = (item.get("careerPageName") or "").strip()
        # A Gupy usa esse rótulo quando a empresa opta por não se identificar.
        if empresa.lower() in ("empresa confidencial", "confidencial"):
            empresa = ""

        modalidade = {
            "remote": "remoto",
            "on-site": "presencial",
            "hybrid": "hibrido",
        }.get((item.get("workplaceType") or "").lower(), "")

        return Job(
            source=self.name,
            source_id=str(job_id),
            title=(item.get("name") or "").strip(),
            url=item.get("jobUrl") or "",
            description=strip_html(item.get("description")),
            company=empresa,
            location=local,
            published_at=item.get("publishedDate") or "",
            skills=[s for s in (item.get("skills") or []) if isinstance(s, str)],
            job_type="Vaga (CLT/PJ)",
            remote_hint=modalidade,
        )


# ---------------------------------------------------------------------------
# Indeed (via JobSpy)
# ---------------------------------------------------------------------------

def _cell(row: Any, chave: str, default: Any = "") -> Any:
    """Lê uma célula do DataFrame tratando coluna ausente e NaN do pandas."""
    try:
        valor = row.get(chave)
    except Exception:  # noqa: BLE001
        return default
    if valor is None:
        return default
    # NaN != NaN — jeito de detectar sem importar pandas aqui.
    if isinstance(valor, float) and valor != valor:
        return default
    return valor


class IndeedSource(BaseSource):
    """Indeed Brasil via `python-jobspy`.

    O site do Indeed é protegido por Cloudflare e devolve 403 até de IP
    residencial. O JobSpy não usa o site: fala com a API do aplicativo, que
    responde rápido e já traz a descrição completa da vaga.

    Armadilha medida em 11/08: o parâmetro `is_remote` do Indeed **não filtra**
    — a busca volta idêntica com e sem ele. O que funciona é pôr a palavra de
    remoto no próprio termo ("assistente home office" deu 19 vagas remotas em
    20, contra 0 de "assistente"). Daí o `remote_qualifier`.
    """

    name = "indeed"
    label = "Indeed"
    # A flag da fonte erra bastante (acertou 11 de 20 no teste); o pré-filtro
    # por texto do main.py é quem segura a regra de remoto aqui.
    prefilter_remote = True
    # Uma requisição por termo. Sem rate limit observado, mas sem exagero.
    default_interval = 3600

    def __init__(self, terms_file: Path, results_per_term: int = 15,
                 hours_old: int = 48, remote_qualifier: str = "home office",
                 interval_seconds: int | None = None) -> None:
        super().__init__(interval_seconds)
        self.terms_file = terms_file
        self.results_per_term = results_per_term
        self.hours_old = hours_old
        self.remote_qualifier = remote_qualifier

    def fetch(self) -> list[Job]:
        try:
            from jobspy import scrape_jobs
        except ImportError as exc:  # pragma: no cover
            raise SourceError(
                "python-jobspy não instalado — `pip install python-jobspy`"
            ) from exc

        vistos: set[str] = set()
        jobs: list[Job] = []

        for termo in load_terms(self.terms_file):
            busca = f"{termo} {self.remote_qualifier}".strip()
            try:
                df = scrape_jobs(
                    site_name=["indeed"],
                    search_term=busca,
                    location="Brasil",
                    country_indeed="brazil",
                    results_wanted=self.results_per_term,
                    hours_old=self.hours_old,
                )
            except Exception as exc:  # noqa: BLE001
                # Um termo que falha não derruba os outros.
                log.warning("Indeed: termo %r falhou: %s", busca, exc)
                continue

            if df is None or len(df) == 0:
                continue

            for _, row in df.iterrows():
                job = self._to_job(row)
                if job is None or job.source_id in vistos:
                    continue
                vistos.add(job.source_id)
                jobs.append(job)

        return jobs

    def _to_job(self, row: Any) -> Job | None:
        job_id = _cell(row, "id")
        url = str(_cell(row, "job_url") or "")
        if not job_id and not url:
            return None

        is_remote = _cell(row, "is_remote", False)
        modalidade = "remoto" if bool(is_remote) else ""

        # Salário como texto: o Indeed varia moeda e periodicidade, e escrever
        # "R$" em cima de valor em outra moeda seria mentira.
        salario = ""
        minimo = _cell(row, "min_amount", None)
        maximo = _cell(row, "max_amount", None)
        moeda = str(_cell(row, "currency") or "").strip()
        periodo = {
            "yearly": "/ano", "monthly": "/mês",
            "weekly": "/semana", "daily": "/dia", "hourly": "/hora",
        }.get(str(_cell(row, "interval") or "").lower(), "")
        if minimo or maximo:
            simbolo = "R$" if moeda in ("BRL", "") else f"{moeda} "
            if minimo and maximo:
                salario = f"{simbolo}{float(minimo):,.0f} – {simbolo}{float(maximo):,.0f}{periodo}"
            else:
                valor = float(maximo or minimo)
                salario = f"{simbolo}{valor:,.0f}{periodo}"
            salario = salario.replace(",", ".")

        return Job(
            source=self.name,
            source_id=str(job_id or url),
            title=str(_cell(row, "title") or "").strip(),
            url=url,
            description=strip_html(str(_cell(row, "description") or "")),
            company=str(_cell(row, "company") or "").strip(),
            location=str(_cell(row, "location") or "").strip(),
            published_at=str(_cell(row, "date_posted") or "")[:10],
            job_type="Vaga (CLT/PJ)",
            salary=salario,
            remote_hint=modalidade,
        )


# ---------------------------------------------------------------------------
# LinkedIn (guest API pública, sem login)
# ---------------------------------------------------------------------------

class LinkedInSource(BaseSource):
    """LinkedIn pela guest API — a mesma que a página pública usa deslogada.

    Nenhum repositório que exige **login** entra aqui: automação com sessão
    logada é o caminho conhecido para a conta ser restringida, e a conta seria a
    do Gabriel. Deslogado o LinkedIn responde normalmente.

    Busca em DUAS FASES, porque a busca não devolve a descrição da vaga:

      Fase 1 — uma requisição por termo, barata, traz título/empresa/local/data.
      Fase 2 — descrição SÓ das vagas que passarem no pré-filtro barato.

    Medido em 11/08: de 60 vagas encontradas, 11 sobreviveram ao pré-filtro —
    49 requisições de descrição economizadas (82% a menos). Buscar a descrição
    de todas custaria mais requisições que Gupy e Indeed somadas.
    """

    name = "linkedin"
    label = "LinkedIn"
    # A fonte já faz o pré-filtro internamente, no lugar certo (antes de gastar
    # a requisição de descrição). Repetir o filtro do main.py depois, agora com
    # critério diferente, só arriscaria derrubar vaga remota legítima.
    prefilter_remote = False
    # A mais cara em requisições por vaga e a mais restritiva das plataformas.
    default_interval = 7200

    BUSCA_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    DETALHE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
    GEOID_BRASIL = "106057199"  # `location=Brasil` em português NÃO funciona

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }

    _CARD_RE = re.compile(r"<li>(.*?)</li>", re.S)
    _TITULO_RE = re.compile(r"base-search-card__title[^>]*>(.*?)</h3>", re.S)
    _EMPRESA_RE = re.compile(r"base-search-card__subtitle[^>]*>.*?>(.*?)</a>", re.S)
    _LOCAL_RE = re.compile(r"job-search-card__location[^>]*>(.*?)</span>", re.S)
    _DATA_RE = re.compile(r'datetime="([^"]+)"')
    _URL_RE = re.compile(r'href="(https://br\.linkedin\.com/jobs/view/[^"?]+)')
    _ID_RE = re.compile(r"-(\d+)$")

    _DESC_RE = re.compile(
        r'<div[^>]*class="[^"]*(?:show-more-less-html__markup|description__text)'
        r'[^"]*"[^>]*>(.*?)</div>\s*(?:<div|</section)',
        re.S,
    )
    _CRITERIO_RE = re.compile(
        r"description__job-criteria-subheader[^>]*>(.*?)</h3>.*?"
        r"description__job-criteria-text[^>]*>(.*?)</span>",
        re.S,
    )

    # Sinais baratos de remoto, avaliados só com título e local.
    _REMOTO_RE = re.compile(
        r"home\s*-?\s*office|homeoffice|remot[oa]|remote|anywhere|"
        r"trabalh\w*\s+(?:de|em)\s+casa|work\s+from\s+home",
        re.I,
    )

    def __init__(self, terms_file: Path, hours_old: int = 24,
                 max_details_per_cycle: int = 40, delay_seconds: float = 3.0,
                 interval_seconds: int | None = None) -> None:
        super().__init__(interval_seconds)
        self.terms_file = terms_file
        self.hours_old = hours_old
        # Teto de segurança: se um dia a busca despejar 300 candidatas, o bot
        # não sai fazendo 300 requisições de descrição sem avisar.
        self.max_details_per_cycle = max_details_per_cycle
        self.delay_seconds = delay_seconds

    # -- transporte ---------------------------------------------------------

    class RateLimited(SourceError):
        """429 que não passou nem com backoff. Encerra o ciclo desta fonte."""

    def _get(self, url: str, params: dict | None = None,
             tentativas: int = 3) -> requests.Response:
        """GET com backoff no 429.

        O LinkedIn é a plataforma mais restritiva das quatro e responde 429 sem
        aviso. Esperar e tentar de novo resolve o soluço; se insistir, é sinal de
        que o IP está marcado e continuar só piora — aí o ciclo é abortado e a
        fonte espera o intervalo dela.
        """
        for tentativa in range(1, tentativas + 1):
            resp = requests.get(url, headers=self.HEADERS, params=params,
                                timeout=REQUEST_TIMEOUT)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            if tentativa == tentativas:
                raise self.RateLimited(
                    f"429 persistente após {tentativas} tentativas — abortando o ciclo"
                )
            espera = self.delay_seconds * (2 ** tentativa)
            log.warning("LinkedIn: 429, aguardando %.0fs (tentativa %d/%d)",
                        espera, tentativa, tentativas)
            time.sleep(espera)
        raise self.RateLimited("429 persistente")  # inalcançável, só para o type checker

    # -- fase 1 -------------------------------------------------------------

    def _buscar(self, termo: str) -> list[dict[str, str]]:
        resp = self._get(self.BUSCA_URL, params={
            "keywords": termo,
            "geoId": self.GEOID_BRASIL,
            "f_WT": "2",                           # só remoto (não confiável, mas ajuda)
            "f_TPR": f"r{self.hours_old * 3600}",  # publicadas nas últimas N horas
            "start": 0,
        })

        def campo(bloco: str, padrao: re.Pattern[str]) -> str:
            m = padrao.search(bloco)
            return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

        achadas: list[dict[str, str]] = []
        for card in self._CARD_RE.findall(resp.text):
            url = campo(card, self._URL_RE)
            if not url:
                continue
            m_id = self._ID_RE.search(url)
            if not m_id:
                continue
            achadas.append({
                "id": m_id.group(1),
                "titulo": html_mod.unescape(campo(card, self._TITULO_RE)),
                "empresa": html_mod.unescape(campo(card, self._EMPRESA_RE)),
                "local": html_mod.unescape(campo(card, self._LOCAL_RE)),
                "data": campo(card, self._DATA_RE),
                "url": url,
            })
        return achadas

    def _promissora(self, c: dict[str, str]) -> bool:
        """Pré-filtro barato: vale gastar uma requisição pra ler a descrição?

        Vaga remota no LinkedIn costuma vir com local "Brasil" em vez de cidade.
        Não é garantia — o classificador é quem decide no fim, com a descrição
        na mão.
        """
        local = c["local"].strip().lower()
        if local in ("brasil", "brazil"):
            return True
        return bool(self._REMOTO_RE.search(c["titulo"]))

    # -- fase 2 -------------------------------------------------------------

    def _detalhar(self, job_id: str) -> tuple[str, str]:
        """Busca a descrição da vaga. Retorna (descrição, critérios)."""
        resp = self._get(self.DETALHE_URL.format(job_id))

        m = self._DESC_RE.search(resp.text)
        descricao = ""
        if m:
            bruto = re.sub(r"<br\s*/?>", "\n", m.group(1))
            bruto = re.sub(r"</(p|li|ul|div)>", "\n", bruto)
            descricao = strip_html(bruto)

        criterios = " · ".join(
            f"{strip_html(k)}: {strip_html(v)}"
            for k, v in self._CRITERIO_RE.findall(resp.text)
        )
        return descricao, criterios

    # -- orquestração -------------------------------------------------------

    def fetch(self) -> list[Job]:
        candidatas: dict[str, dict[str, str]] = {}
        for termo in load_terms(self.terms_file):
            try:
                for c in self._buscar(termo):
                    candidatas.setdefault(c["id"], c)  # dedup entre termos
            except self.RateLimited as exc:
                # Insistir com o IP marcado só piora: para tudo e espera o
                # intervalo. O que já foi coletado até aqui continua valendo.
                log.warning("LinkedIn: %s — parando as buscas deste ciclo", exc)
                break
            except requests.RequestException as exc:
                log.warning("LinkedIn: termo %r falhou: %s", termo, exc)
                continue
            time.sleep(self.delay_seconds)

        promissoras = [c for c in candidatas.values() if self._promissora(c)]
        log.info(
            "LinkedIn: %d vagas encontradas, %d promissoras (%d descrições economizadas)",
            len(candidatas), len(promissoras), len(candidatas) - len(promissoras),
        )

        if len(promissoras) > self.max_details_per_cycle:
            log.warning(
                "LinkedIn: %d promissoras acima do teto de %d — buscando só as mais recentes",
                len(promissoras), self.max_details_per_cycle,
            )
            promissoras.sort(key=lambda c: c["data"], reverse=True)
            promissoras = promissoras[: self.max_details_per_cycle]

        jobs: list[Job] = []
        for c in promissoras:
            try:
                descricao, criterios = self._detalhar(c["id"])
            except self.RateLimited as exc:
                log.warning("LinkedIn: %s — parando as descrições deste ciclo "
                            "(as %d já coletadas seguem)", exc, len(jobs))
                break
            except requests.RequestException as exc:
                # Sem descrição o classificador decidiria no escuro; melhor pular
                # e pegar no próximo ciclo — a vaga não entra no seen_ids.
                log.warning("LinkedIn: descrição da vaga %s falhou: %s", c["id"], exc)
                time.sleep(self.delay_seconds)
                continue

            # Espaça as requisições em qualquer caminho de sucesso.
            time.sleep(self.delay_seconds)

            if not descricao:
                log.debug("LinkedIn: vaga %s sem descrição extraível — pulando", c["id"])
                continue

            jobs.append(Job(
                source=self.name,
                source_id=c["id"],
                title=c["titulo"],
                url=c["url"],
                description=descricao,
                company=c["empresa"],
                location=c["local"],
                published_at=c["data"],
                job_type="Vaga (CLT/PJ)",
                category=criterios,
                # De propósito vazio: o f_WT=2 do LinkedIn afirma remoto e erra
                # (vaga "Trabalhe de casa" com "Local de trabalho: Guarulhos/SP"
                # na descrição). Dizer "remoto" aqui enviesaria o classificador.
                remote_hint="",
            ))

        return jobs


# ---------------------------------------------------------------------------
# Termos de busca (arquivo editável, mesmo espírito do profile.md)
# ---------------------------------------------------------------------------

DEFAULT_TERMS = [
    "assistente virtual", "assistente administrativo", "assistente executivo",
    "secretaria remota", "atendimento ao cliente", "suporte ao cliente",
    "assistente comercial", "sdr", "closer", "assistente de vendas",
    "auxiliar administrativo", "assistente financeiro", "customer success",
    "assistente de agendamento",
]


_terms_cache: dict[str, tuple[float, list[str]]] = {}


def load_terms(caminho: Path) -> list[str]:
    """Lê os termos de busca do arquivo. Uma linha por termo, `#` é comentário.

    Cache invalidado por mtime, igual ao `profile.md`: rodando local, editar o
    arquivo já vale no ciclo seguinte, sem restart.
    """
    if not caminho.exists():
        log.info("Arquivo de termos %s não existe — usando lista padrão", caminho)
        return list(DEFAULT_TERMS)

    chave = str(caminho)
    try:
        mtime = caminho.stat().st_mtime
    except OSError:
        mtime = 0.0
    em_cache = _terms_cache.get(chave)
    if em_cache and em_cache[0] == mtime:
        return em_cache[1]

    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.error("Falha lendo %s: %s — usando lista padrão", caminho, exc)
        return list(DEFAULT_TERMS)

    termos = [ln.strip() for ln in linhas]
    termos = [t for t in termos if t and not t.startswith("#")]
    if not termos:
        log.warning("%s está vazio — usando lista padrão", caminho)
        return list(DEFAULT_TERMS)

    log.info("Carregados %d termos de busca de %s", len(termos), caminho)
    _terms_cache[chave] = (mtime, termos)
    return termos
