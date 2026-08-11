"""AV Jobs Bot — monitora vagas em várias fontes e notifica no Telegram.

Filtra as vagas para o perfil de Assistente Virtual descrito em `profile.md`.
As fontes ficam em `sources.py`; aqui mora só o pipeline: buscar → deduplicar →
pré-filtrar → classificar → notificar.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests
from dotenv import load_dotenv

from bot_control import CommandListener, ControlState, parse_admin_ids
from sources import (
    GupySource, IndeedSource, Job, LinkedInSource, ONMSource, SourceError,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("av-jobs-bot")

ONM_EMAIL = os.getenv("ONM_EMAIL", "").strip()
ONM_PASSWORD = os.getenv("ONM_PASSWORD", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))
DATA_DIR = Path(os.getenv("DATA_DIR", "."))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
PROFILE_FILE = Path(os.getenv("PROFILE_FILE", "profile.md"))
TERMS_FILE = Path(os.getenv("TERMS_FILE", "search_terms.txt"))
# O LinkedIn tem lista própria e curta: lá cada vaga custa duas requisições.
LINKEDIN_TERMS_FILE = Path(os.getenv("LINKEDIN_TERMS_FILE", "search_terms_linkedin.txt"))

# Fontes ativas, separadas por vírgula. Permite desligar uma sem mexer no código.
ENABLED_SOURCES = os.getenv("SOURCES", "onm,gupy,indeed,linkedin")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SEEN_FILE = DATA_DIR / "seen_ids.json"
SKIPPED_LOG_FILE = DATA_DIR / "skipped_jobs.jsonl"
QUOTA_LOG_FILE = DATA_DIR / "quota_log.jsonl"
CONTROL_FILE = DATA_DIR / "bot_state.json"

# Quem pode mandar comando no privado do bot. Vazio = comandos desligados.
ADMIN_IDS = parse_admin_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))

# O container roda em UTC; o relatório tem que sair no horário de Brasília.
TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "22"))
# Para onde vai o relatório diário: "grupo" ou "privado".
REPORT_TO = os.getenv("REPORT_TO", "grupo").strip().lower()


def _tz() -> Any:
    """Fuso local. Cai em UTC-3 fixo se a base de fusos não existir na imagem."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TIMEZONE_NAME)
        except Exception as exc:  # noqa: BLE001
            log.warning("Fuso %s indisponível (%s) — usando UTC-3 fixo", TIMEZONE_NAME, exc)
    from datetime import timedelta
    return timezone(timedelta(hours=-3))


def agora_local() -> datetime:
    return datetime.now(_tz())

REQUEST_TIMEOUT = 30
TELEGRAM_RATE_LIMIT_SECONDS = 1.0
DESCRIPTION_MAX_CHARS = 300

Category = Literal["relevant", "borderline", "irrelevant"]
WorkMode = Literal["remoto", "hibrido", "presencial", "nao_informado"]

# Modalidades que derrubam a vaga: o Gabriel só quer 100% remoto.
REJECTED_WORK_MODES: tuple[WorkMode, ...] = ("presencial", "hibrido")


# ---------------------------------------------------------------------------
# Persistência: IDs já vistos + chaves de deduplicação entre fontes
# ---------------------------------------------------------------------------

def load_seen() -> tuple[set[str], set[str], set[str]]:
    """Lê o estado salvo: (uids vistos, chaves de dedup, fontes já inicializadas).

    Aceita o formato antigo — uma lista de IDs numéricos do ONM, de quando o bot
    era monofonte — e converte para `onm:<id>`. Sem isso, o primeiro ciclo depois
    do upgrade trataria todas as vagas do ONM como novas e reenviaria tudo.
    """
    if not SEEN_FILE.exists():
        return set(), set(), set()
    try:
        with SEEN_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log.warning("Failed to read %s: %s — starting empty", SEEN_FILE, exc)
        return set(), set(), set()

    if isinstance(data, list):  # formato antigo: só o ONM existia
        uids = {str(x) if ":" in str(x) else f"onm:{x}" for x in data}
        log.info("Migrated %d IDs from the old single-source format", len(uids))
        return uids, set(), {"onm"}

    uids = {str(x) for x in (data.get("uids") or [])}
    keys = {str(x) for x in (data.get("dedup_keys") or [])}
    inicializadas = {str(x) for x in (data.get("initialized_sources") or [])}
    return uids, keys, inicializadas


def save_seen(uids: set[str], keys: set[str], inicializadas: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({
            "uids": sorted(uids),
            "dedup_keys": sorted(keys),
            "initialized_sources": sorted(inicializadas),
        }, f)
    tmp.replace(SEEN_FILE)


def _normalize(texto: str) -> str:
    """Minúsculas, sem acento e sem pontuação — para comparar título/empresa."""
    sem_acento = (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "", sem_acento.lower())


def dedup_key(job: Job) -> str:
    """Chave para reconhecer a MESMA vaga anunciada em fontes diferentes.

    A mesma vaga costuma aparecer no Indeed e no LinkedIn com IDs distintos; sem
    isso o grupo receberia a mesma coisa duas vezes.
    """
    return f"{_normalize(job.title)}|{_normalize(job.company)}"


# ---------------------------------------------------------------------------
# Pré-filtro de remoto (grátis, roda antes do classificador)
# ---------------------------------------------------------------------------

REMOTE_RE = re.compile(
    r"home\s*-?\s*office|homeoffice|100\s*%\s*remot|trabalh\w*\s+remot|"
    r"\bremot[oa]s?\b|remotamente|[àa]\s+dist[âa]ncia|"
    r"trabalh\w*\s+(?:de|em)\s+casa|work\s+from\s+home|anywhere",
    re.I,
)


def parece_remoto(job: Job) -> bool:
    """Heurística barata: a fonte diz que é remoto, ou o texto diz."""
    if job.remote_hint == "remoto":
        return True
    return bool(REMOTE_RE.search(f"{job.title}\n{job.description}"))


# ---------------------------------------------------------------------------
# Classificador (Gemini)
# ---------------------------------------------------------------------------

_genai_client: Any = None
_profile_text: str | None = None
_profile_mtime: float | None = None

CLASSIFIER_INSTRUCTIONS = """Você lê vagas e projetos de trabalho em português
brasileiro e faz duas coisas: (A) decide se a vaga interessa ao perfil
informado logo abaixo destas instruções e (B) extrai os dados da vaga.

== (A) CLASSIFICAÇÃO ==

- O PERFIL DO USUÁRIO é a única fonte de verdade sobre o que interessa. Não
  presuma nenhuma área por conta própria — siga o que o perfil diz.
- Descrições têm grafia inconsistente, abreviações, gírias e erros de digitação.
  Trate variações como sinônimos ("assistente virtual"/"AV"/"assistente
  vitual", "secretária remota"/"secretariado remoto", "home office"/"homeoffice"),
  inclusive sem acento.
- Considere o título, a profissão/área, as skills E a descrição. Às vezes a
  descrição é vaga mas a categoria/skills denuncia que é da área.
- Se houver QUALQUER dúvida razoável, retorne "borderline" — é melhor o
  usuário receber uma notificação a mais e ignorar do que perder uma vaga.
- "irrelevant" só para casos claramente fora do perfil.
- EXCEÇÃO ao viés de "borderline": se o perfil marcar alguma regra como
  OBRIGATÓRIA e a vaga violar essa regra explicitamente, retorne "irrelevant"
  mesmo que a função encaixe bem.

== (B) EXTRAÇÃO ==

Extraia SOMENTE o que estiver escrito na vaga. NUNCA invente, deduza ou
complete com suposição. Se a informação não estiver no texto, devolva string
vazia ("") ou "nao_informado" — isso é esperado e correto.

- work_mode: modalidade de trabalho.
  - "remoto" — diz remoto, 100% remoto, home office, à distância, anywhere.
  - "hibrido" — diz híbrido, X dias no escritório, presencial parcial.
  - "presencial" — exige presença física, comparecer ao escritório/loja/clínica,
    ou exige morar/estar em cidade ou região específica para trabalhar no local.
  - "nao_informado" — o texto não fala nada sobre modalidade. Não chute pelo
    tipo da vaga nem pela área.
  ATENÇÃO: a plataforma às vezes informa a modalidade e erra. Se o texto da vaga
  contradisser o que a plataforma diz, confie no TEXTO.
- company: nome da empresa/instituição contratante, se aparecer no texto.
  Se só houver o nome de uma pessoa física, ou nada, devolva "".
- role_type: a função em 2-4 palavras, normalizada (ex.: "Assistente Virtual",
  "SDR", "Assistente Financeiro", "Customer Success"). Se não der pra
  determinar, devolva "".
- summary: resumo da vaga em 1-2 frases curtas (até 250 chars), em português,
  dizendo o que a pessoa vai fazer. Sem enrolação e sem repetir o título.
  Se a descrição for vazia ou inútil, devolva "".
- salary: remuneração citada NO TEXTO, como está escrita (ex.: "R$ 2.000/mês",
  "R$ 25/hora", "a combinar"). Se o texto não citar valor, devolva "".
"""

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["relevant", "borderline", "irrelevant"],
        },
        "reason": {"type": "string"},
        "work_mode": {
            "type": "string",
            "enum": ["remoto", "hibrido", "presencial", "nao_informado"],
        },
        "company": {"type": "string"},
        "role_type": {"type": "string"},
        "summary": {"type": "string"},
        "salary": {"type": "string"},
    },
    "required": [
        "category", "reason", "work_mode",
        "company", "role_type", "summary", "salary",
    ],
}


def load_profile() -> str | None:
    """Lê profile.md (cache por mtime — edições valem em runtime)."""
    global _profile_text, _profile_mtime
    if not PROFILE_FILE.exists():
        if _profile_text is not None:
            log.warning("Profile file %s was removed", PROFILE_FILE)
            _profile_text = None
            _profile_mtime = None
        return None
    mtime = PROFILE_FILE.stat().st_mtime
    if _profile_text is None or mtime != _profile_mtime:
        try:
            _profile_text = PROFILE_FILE.read_text(encoding="utf-8")
            _profile_mtime = mtime
            log.info("Loaded profile from %s (%d chars)", PROFILE_FILE, len(_profile_text))
        except OSError as exc:
            log.error("Failed to read profile %s: %s", PROFILE_FILE, exc)
            return None
    return _profile_text


def get_genai_client() -> Any | None:
    """Lazy init do client do Gemini. None se não configurado/disponível."""
    global _genai_client
    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        return None
    if _genai_client is None:
        try:
            _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            log.info("Gemini client initialized (model=%s)", GEMINI_MODEL)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to init Gemini client: %s", exc)
            return None
    return _genai_client


# O free tier do Gemini tem DOIS tetos: por minuto e por dia. Estourar o de
# minuto é contornável com retry; o DIÁRIO não é — só volta no dia seguinte, e
# até lá todas as vagas passam a ser notificadas SEM FILTRO.
GEMINI_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "4"))
GEMINI_RETRY_CAP_SECONDS = 65.0
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s")
_QUOTA_ID_RE = re.compile(r"quotaId['\"]?:\s*['\"]([^'\"]+)")
_QUOTA_VALUE_RE = re.compile(r"quotaValue['\"]?:\s*['\"]?(\d+)")


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _quota_scope(exc: Exception) -> str:
    """Distingue teto diário de teto por minuto: 'day', 'minute' ou '?'."""
    match = _QUOTA_ID_RE.search(str(exc))
    if not match:
        return "?"
    quota_id = match.group(1)
    if "PerDay" in quota_id:
        return "day"
    if "PerMinute" in quota_id:
        return "minute"
    return "?"


def _quota_value(exc: Exception) -> str:
    match = _QUOTA_VALUE_RE.search(str(exc))
    return match.group(1) if match else "?"


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    """Usa o retryDelay que a própria API sugere; senão, backoff exponencial."""
    match = _RETRY_DELAY_RE.search(str(exc))
    if match:
        return min(float(match.group(1)) + 1.0, GEMINI_RETRY_CAP_SECONDS)
    return min(2.0 ** attempt, GEMINI_RETRY_CAP_SECONDS)


# ---------------------------------------------------------------------------
# Telemetria do dia
# ---------------------------------------------------------------------------

_stats_day: str | None = None
_stats: dict[str, int] = {}
_stats_por_fonte: dict[str, dict[str, int]] = {}
_daily_quota_announced = False


def _hoje() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _reset_stats(today: str) -> None:
    global _stats_day, _stats, _stats_por_fonte, _daily_quota_announced
    _stats_day = today
    _stats = {
        "classified": 0, "rate_limited": 0, "unfiltered": 0,
        "prefiltered": 0, "deduped": 0, "sent": 0, "skipped": 0,
    }
    _stats_por_fonte = {}
    _daily_quota_announced = False


def _rollover_se_preciso() -> None:
    global _stats_day
    today = _hoje()
    if _stats_day != today:
        if _stats_day is not None:
            _persist_stats(_stats_day)
        _reset_stats(today)


def _bump(key: str, fonte: str | None = None) -> None:
    _rollover_se_preciso()
    _stats[key] = _stats.get(key, 0) + 1
    if fonte:
        por_fonte = _stats_por_fonte.setdefault(fonte, {})
        por_fonte[key] = por_fonte.get(key, 0) + 1


def _persist_stats(day: str) -> None:
    """Grava o resumo do dia em quota_log.jsonl para revisão posterior."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"day": day, "model": GEMINI_MODEL, **_stats, "por_fonte": _stats_por_fonte}
    try:
        with QUOTA_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("Failed to write quota log: %s", exc)


def log_filter_stats() -> None:
    """Loga o placar do dia. Chamado ao fim de cada ciclo."""
    if not _stats:
        return
    unfiltered = _stats.get("unfiltered", 0)
    line = (
        f"FILTRO (hoje {_stats_day}): {_stats.get('classified', 0)} classificadas, "
        f"{_stats.get('prefiltered', 0)} cortadas no pre-filtro, "
        f"{_stats.get('deduped', 0)} duplicadas, "
        f"{_stats.get('sent', 0)} enviadas, "
        f"{_stats.get('rate_limited', 0)} rate-limits, "
        f"{unfiltered} vagas notificadas SEM FILTRO"
    )
    if unfiltered:
        log.warning("%s  <-- free tier nao esta aguentando o volume", line)
    else:
        log.info(line)


def _fallback_analysis(reason: str) -> dict[str, Any]:
    """Análise vazia para quando o Gemini está indisponível — notifica sem filtrar."""
    return {
        "category": "relevant",
        "reason": reason,
        "work_mode": "nao_informado",
        "company": "",
        "role_type": "",
        "summary": "",
        "salary": "",
    }


def analyze_job(job: Job) -> dict[str, Any]:
    """Classifica e extrai os dados de uma vaga com o Gemini.

    Em caso de erro ou falta de config, cai no fallback seguro
    (category='relevant') — nunca se perde vaga por falha do filtro.
    """
    profile = load_profile()
    client = get_genai_client()

    if profile is None or client is None:
        _bump("unfiltered", job.source)
        return _fallback_analysis("filtro desativado (sem profile.md ou GEMINI_API_KEY)")

    prompt = (
        f"{CLASSIFIER_INSTRUCTIONS}\n\n"
        f"=== PERFIL DO USUÁRIO ===\n{profile}\n\n"
        f"=== VAGA A ANALISAR ===\n{job.to_classifier_text()}"
    )

    global _daily_quota_announced

    data: dict[str, Any] | None = None
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CLASSIFIER_SCHEMA,
                    temperature=0.1,
                ),
            )
            data = json.loads(resp.text)
            _bump("classified", job.source)
            break
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit(exc):
                _bump("rate_limited", job.source)

                # Cota diária esgotada: retry é inútil, só volta amanhã.
                if _quota_scope(exc) == "day":
                    if not _daily_quota_announced:
                        _daily_quota_announced = True
                        log.error(
                            "COTA DIARIA DO GEMINI ESGOTADA (limite=%s/dia, model=%s). "
                            "A partir de agora TODAS as vagas vao para o grupo SEM FILTRO "
                            "ate a cota resetar. Migrar para um modelo pago.",
                            _quota_value(exc), GEMINI_MODEL,
                        )
                    _bump("unfiltered", job.source)
                    return _fallback_analysis("cota diaria do Gemini esgotada — vaga sem filtro")

                if attempt < GEMINI_MAX_ATTEMPTS:
                    delay = _retry_delay_seconds(exc, attempt)
                    log.warning(
                        "Rate limit por minuto na vaga %s (tentativa %d/%d) — aguardando %.0fs",
                        job.uid, attempt, GEMINI_MAX_ATTEMPTS, delay,
                    )
                    time.sleep(delay)
                    continue

            log.error("Analysis failed for %s: %s — defaulting to 'relevant'", job.uid, exc)
            _bump("unfiltered", job.source)
            return _fallback_analysis(f"erro no classificador ({type(exc).__name__})")

    if data is None:
        _bump("unfiltered", job.source)
        return _fallback_analysis("classificador sem resposta")

    category: Category = data.get("category", "borderline")
    reason: str = (data.get("reason") or "").strip()[:200]
    if category not in ("relevant", "borderline", "irrelevant"):
        log.warning("Analysis returned invalid category %r for %s", category, job.uid)
        category = "borderline"
        reason = reason or "categoria inválida do classificador"

    work_mode: WorkMode = data.get("work_mode", "nao_informado")
    if work_mode not in ("remoto", "hibrido", "presencial", "nao_informado"):
        log.warning("Analysis returned invalid work_mode %r for %s", work_mode, job.uid)
        work_mode = "nao_informado"

    # Regra dura do Gabriel: só 100% remoto. Presencial/híbrido cai fora mesmo
    # que a função encaixe — não confia só no viés do classificador.
    if work_mode in REJECTED_WORK_MODES and category != "irrelevant":
        log.info("Job %s dropped by work_mode=%s (was %s)", job.uid, work_mode, category)
        category = "irrelevant"
        reason = f"vaga {work_mode} — só entram vagas remotas"

    return {
        "category": category,
        "reason": reason,
        "work_mode": work_mode,
        "company": (data.get("company") or "").strip()[:120],
        "role_type": (data.get("role_type") or "").strip()[:80],
        "summary": (data.get("summary") or "").strip()[:300],
        "salary": (data.get("salary") or "").strip()[:80],
    }


def log_skipped_job(job: Job, analysis: dict[str, Any]) -> None:
    """Anexa a vaga descartada em skipped_jobs.jsonl pra revisão posterior."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "skipped_at": datetime.now(timezone.utc).isoformat(),
        "uid": job.uid,
        "source": job.source,
        "title": job.title,
        "reason": analysis.get("reason", ""),
        "work_mode": analysis.get("work_mode", ""),
        "role_type": analysis.get("role_type", ""),
        "url": job.url,
    }
    try:
        with SKIPPED_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("Failed to log skipped job %s: %s", job.uid, exc)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str, chat_id: str | int | None = None) -> None:
    resp = requests.post(
        TELEGRAM_URL,
        json={
            "chat_id": TELEGRAM_CHAT_ID if chat_id is None else chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not resp.ok:
        log.error("Telegram error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()


def is_transient_send_error(exc: requests.RequestException) -> bool:
    """Diz se vale retentar o envio no próximo ciclo.

    Falha de rede, timeout, 429 e 5xx são passageiros — a vaga fica fora do
    seen_ids e é reenviada. Um 4xx (fora o 429) é problema da mensagem em si
    (HTML inválido, chat errado, bot removido do grupo): retentar todo ciclo só
    repetiria o mesmo erro para sempre e travaria as vagas seguintes.
    """
    resp = exc.response
    if resp is None:
        return True
    return resp.status_code == 429 or resp.status_code >= 500


def truncate_description(text: str, max_chars: int = DESCRIPTION_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    space = cut.rfind(" ")  # corta na última palavra completa
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "..."


WORK_MODE_LABELS: dict[str, str] = {
    "remoto": "🏠 100% remoto",
    "nao_informado": "🏠 Modalidade não informada",
    "hibrido": "🏠 Híbrido",
    "presencial": "🏠 Presencial",
}


def format_job(job: Job, analysis: dict[str, Any] | None = None,
               source_label: str = "") -> str:
    analysis = analysis or _fallback_analysis("")
    category = analysis.get("category", "relevant")

    type_label = "📋 PROJETO" if "Projeto" in job.job_type else "🏢 VAGA"
    if category == "borderline":
        type_label = f"🤔 {type_label} (talvez)"
    if source_label:
        type_label = f"{type_label} · {html.escape(source_label)}"

    title = html.escape(job.title or "(sem título)")

    # Empresa extraída pelo classificador; se não houver, a que a fonte informou.
    company = analysis.get("company") or job.company or "Não informada"
    company_line = f"👤 {html.escape(company)}"

    role_type = analysis.get("role_type") or ""
    if role_type and job.category:
        prof_line = f"🏷 {html.escape(role_type)} · {html.escape(job.category)}"
    elif role_type or job.category:
        prof_line = f"🏷 {html.escape(role_type or job.category)}"
    else:
        prof_line = ""

    work_mode_line = WORK_MODE_LABELS.get(analysis.get("work_mode", "nao_informado"), "")

    skills_line = "🔧 " + html.escape(", ".join(job.skills)) if job.skills else ""

    # Valores: o que a fonte informa tem prioridade; senão o que o classificador
    # achou no texto da vaga.
    if job.salary_min is not None and job.salary_max is not None:
        budget_line = f"💰 R${job.salary_min:.0f} – R${job.salary_max:.0f}"
    elif job.salary_max is not None:
        budget_line = f"💰 até R${job.salary_max:.0f}"
    elif job.salary_min is not None:
        budget_line = f"💰 a partir de R${job.salary_min:.0f}"
    elif job.salary:
        budget_line = f"💰 {html.escape(job.salary)}"
    elif analysis.get("salary"):
        budget_line = f"💰 {html.escape(analysis['salary'])}"
    else:
        budget_line = "💰 Valor não informado"

    date_line = f"📅 {html.escape(job.published_at)}" if job.published_at else ""

    # Resumo do classificador; sem ele, a descrição crua truncada.
    description = analysis.get("summary") or truncate_description(job.description)
    description_html = f"<i>{html.escape(description)}</i>" if description else ""

    lines = [type_label, "", f"📌 <b>{title}</b>", "", company_line]
    for line in (prof_line, work_mode_line, skills_line, budget_line, date_line):
        if line:
            lines.append(line)
    if description_html:
        lines.extend(["", description_html])
    if category == "borderline" and analysis.get("reason"):
        lines.extend(["", f"<i>🤖 Motivo: {html.escape(analysis['reason'])}</i>"])
    lines.extend(["", f'🔗 <a href="{job.url}">Ver vaga</a>'])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Relatório diário
# ---------------------------------------------------------------------------

def montar_relatorio(fontes: list[Any], estado: ControlState | None = None,
                     titulo: str = "Relatório do dia") -> str:
    """Monta o resumo do dia: o que cada fonte trouxe e o que virou mensagem."""
    _rollover_se_preciso()
    agora = agora_local()
    linhas = [f"📊 <b>{html.escape(titulo)}</b> — {agora.strftime('%d/%m/%Y')}", ""]

    enviadas = _stats.get("sent", 0)
    if enviadas:
        linhas.append(f"✅ <b>{enviadas}</b> vaga(s) enviadas ao grupo")
    else:
        linhas.append("😴 Nenhuma vaga enviada hoje")
    linhas.append("")

    linhas.append("<b>Por fonte</b>")
    houve_fonte = False
    for f in fontes:
        por_fonte = _stats_por_fonte.get(f.name, {})
        env = por_fonte.get("sent", 0)
        desc = por_fonte.get("skipped", 0)
        pre = por_fonte.get("prefiltered", 0)
        dup = por_fonte.get("deduped", 0)
        if not any((env, desc, pre, dup)):
            continue
        houve_fonte = True
        label = html.escape(getattr(f, "label", f.name))
        detalhes = [f"{env} enviada(s)"]
        if desc:
            detalhes.append(f"{desc} descartada(s)")
        if pre:
            detalhes.append(f"{pre} cortada(s) no pré-filtro")
        if dup:
            detalhes.append(f"{dup} repetida(s)")
        linhas.append(f"• <b>{label}</b>: {' · '.join(detalhes)}")
    if not houve_fonte:
        linhas.append("<i>nenhuma vaga nova processada hoje</i>")

    pausadas = estado.paused() if estado else set()
    if pausadas:
        nomes = ", ".join(
            html.escape(getattr(f, "label", f.name)) for f in fontes if f.name in pausadas
        )
        linhas.extend(["", f"⏸ <b>Pausadas:</b> {nomes}"])

    # Saúde do filtro: o número que diz se a cota de IA está aguentando.
    sem_filtro = _stats.get("unfiltered", 0)
    linhas.extend(["", "<b>Filtro</b>"])
    linhas.append(f"• {_stats.get('classified', 0)} vaga(s) analisadas pela IA")
    if sem_filtro:
        linhas.append(
            f"• ⚠️ <b>{sem_filtro} enviada(s) SEM FILTRO</b> — a cota de IA não "
            f"aguentou o volume"
        )
    else:
        linhas.append("• Nenhuma vaga passou sem filtro (cota saudável)")

    return "\n".join(linhas)


def enviar_relatorio(fontes: list[Any], estado: ControlState | None,
                     titulo: str = "Relatório do dia") -> None:
    texto = montar_relatorio(fontes, estado, titulo)

    # No privado o relatório é ferramenta de trabalho do Gustavo/Gabriel; no
    # grupo é conteúdo para os alunos. Configurável porque a escolha é do dono.
    destinos: list[str | int]
    if REPORT_TO in ("privado", "private", "dm"):
        destinos = sorted(ADMIN_IDS)
        if not destinos:
            log.warning("REPORT_TO=privado mas TELEGRAM_ADMIN_IDS está vazio — "
                        "mandando pro grupo")
            destinos = [TELEGRAM_CHAT_ID]
    else:
        destinos = [TELEGRAM_CHAT_ID]

    for destino in destinos:
        try:
            send_telegram(texto, chat_id=destino)
            log.info("Relatório enviado para %s.", destino)
        except requests.RequestException as exc:
            log.error("Falha enviando relatório para %s: %s", destino, exc)


# ---------------------------------------------------------------------------
# Comandos do bot
# ---------------------------------------------------------------------------

AJUDA = """🤖 <b>Comandos do AV Jobs Bot</b>

/status — visão geral: fontes, próxima checagem, números do dia
/fontes — lista as fontes e o estado de cada uma
/pausar &lt;fonte&gt; — para de buscar naquela fonte (ou <code>tudo</code>)
/retomar &lt;fonte&gt; — volta a buscar (ou <code>tudo</code>)
/relatorio — manda o relatório do dia agora
/id — mostra seu ID do Telegram (funciona para qualquer pessoa)
/ajuda — esta mensagem

Exemplos:
<code>/pausar linkedin</code>
<code>/retomar tudo</code>

<i>Estes comandos só funcionam aqui no privado — no grupo ficariam à vista de todo mundo.</i>"""


def _fmt_duracao(segundos: int) -> str:
    if segundos <= 0:
        return "agora"
    if segundos < 60:
        return f"{segundos}s"
    minutos = segundos // 60
    if minutos < 60:
        return f"{minutos}min"
    return f"{minutos // 60}h{minutos % 60:02d}"


def build_handlers(fontes: list[Any], estado: ControlState) -> dict[str, Any]:
    """Cria os handlers dos comandos, fechando sobre as fontes e o estado."""
    por_nome = {f.name: f for f in fontes}

    def _resolver(args: list[str]) -> tuple[list[Any] | None, str]:
        """Traduz o argumento do comando em uma lista de fontes."""
        if not args:
            return None, ("Faltou dizer a fonte. Use <code>tudo</code> ou uma de: "
                          + ", ".join(f"<code>{n}</code>" for n in por_nome))
        alvo = args[0].strip().lower()
        if alvo in ("tudo", "todas", "all"):
            return list(fontes), ""
        if alvo in por_nome:
            return [por_nome[alvo]], ""
        return None, (f"Fonte desconhecida: <code>{html.escape(alvo)}</code>. "
                      "Conhecidas: " + ", ".join(f"<code>{n}</code>" for n in por_nome))

    def ajuda(_args: list[str]) -> str:
        return AJUDA

    def status(_args: list[str]) -> str:
        agora_mono = time.monotonic()
        pausadas = estado.paused()
        linhas = [f"📡 <b>Status</b> — {agora_local().strftime('%d/%m %H:%M')}", ""]
        for f in fontes:
            label = html.escape(getattr(f, "label", f.name))
            if f.name in pausadas:
                linhas.append(f"⏸ <b>{label}</b> — pausada")
                continue
            prox = _fmt_duracao(f.segundos_para_proxima(agora_mono))
            ultima = (
                f.last_fetch_at.astimezone(_tz()).strftime("%H:%M")
                if f.last_fetch_at else "ainda não rodou"
            )
            vagas = f" · {f.last_count} vagas" if f.last_count is not None else ""
            linhas.append(
                f"▶️ <b>{label}</b> — última {ultima}{vagas} · próxima em {prox}"
            )
        linhas.extend([
            "",
            f"Hoje: <b>{_stats.get('sent', 0)}</b> enviadas · "
            f"{_stats.get('skipped', 0)} descartadas · "
            f"{_stats.get('classified', 0)} analisadas pela IA",
        ])
        if _stats.get("unfiltered", 0):
            linhas.append(f"⚠️ {_stats['unfiltered']} enviadas SEM FILTRO (cota de IA)")
        return "\n".join(linhas)

    def listar_fontes(_args: list[str]) -> str:
        pausadas = estado.paused()
        linhas = ["🗂 <b>Fontes</b>", ""]
        for f in fontes:
            marca = "⏸ pausada" if f.name in pausadas else "▶️ ativa"
            linhas.append(
                f"<code>{f.name}</code> — {html.escape(getattr(f, 'label', f.name))} "
                f"· a cada {f.interval_seconds // 60}min · {marca}"
            )
        return "\n".join(linhas)

    def pausar(args: list[str]) -> str:
        alvos, erro = _resolver(args)
        if alvos is None:
            return erro
        mudou = [f for f in alvos if estado.pause(f.name)]
        if not mudou:
            return "Nada mudou — já estava(m) pausada(s)."
        nomes = ", ".join(html.escape(getattr(f, "label", f.name)) for f in mudou)
        return f"⏸ Pausado: <b>{nomes}</b>\nNão vou mais buscar vagas aí até você mandar /retomar."

    def retomar(args: list[str]) -> str:
        alvos, erro = _resolver(args)
        if alvos is None:
            return erro
        mudou = [f for f in alvos if estado.resume(f.name)]
        if not mudou:
            return "Nada mudou — já estava(m) ativa(s)."
        nomes = ", ".join(html.escape(getattr(f, "label", f.name)) for f in mudou)
        return f"▶️ Retomado: <b>{nomes}</b>"

    def relatorio(_args: list[str]) -> str:
        return montar_relatorio(fontes, estado, "Relatório parcial")

    return {
        "ajuda": ajuda, "help": ajuda, "start": ajuda,
        "status": status,
        "fontes": listar_fontes,
        "pausar": pausar, "pause": pausar,
        "retomar": retomar, "resume": retomar,
        "relatorio": relatorio, "relatório": relatorio,
    }


# ---------------------------------------------------------------------------
# Fontes ativas
# ---------------------------------------------------------------------------

def _interval_for(nome: str, default: int) -> int:
    """Intervalo da fonte, sobrescrevível por `INTERVAL_<FONTE>` no ambiente."""
    bruto = os.getenv(f"INTERVAL_{nome.upper()}", "").strip()
    if not bruto:
        return default
    try:
        # Piso de 60s: intervalo menor que isso só serve pra tomar bloqueio.
        return max(60, int(bruto))
    except ValueError:
        log.warning("INTERVAL_%s=%r não é número — usando %ds", nome.upper(), bruto, default)
        return default


def build_sources() -> list[Any]:
    """Monta a lista de fontes ativas a partir da env `SOURCES`."""
    pedidas = [s.strip().lower() for s in ENABLED_SOURCES.split(",") if s.strip()]
    ativas: list[Any] = []

    for nome in pedidas:
        if nome == "onm":
            if not (ONM_EMAIL and ONM_PASSWORD):
                log.warning("Fonte 'onm' pedida mas ONM_EMAIL/ONM_PASSWORD faltando — pulando")
                continue
            ativas.append(ONMSource(
                ONM_EMAIL, ONM_PASSWORD,
                interval_seconds=_interval_for("onm", ONMSource.default_interval),
            ))
        elif nome == "gupy":
            ativas.append(GupySource(
                terms_file=TERMS_FILE,
                interval_seconds=_interval_for("gupy", GupySource.default_interval),
            ))
        elif nome == "indeed":
            ativas.append(IndeedSource(
                terms_file=TERMS_FILE,
                interval_seconds=_interval_for("indeed", IndeedSource.default_interval),
            ))
        elif nome == "linkedin":
            ativas.append(LinkedInSource(
                terms_file=LINKEDIN_TERMS_FILE,
                interval_seconds=_interval_for("linkedin", LinkedInSource.default_interval),
            ))
        else:
            log.warning("Fonte desconhecida em SOURCES: %r — ignorando", nome)

    if not ativas:
        log.error("Nenhuma fonte ativa. Ajuste a variável SOURCES (atual: %r)", ENABLED_SOURCES)
        sys.exit(1)
    return ativas


def coletar(fontes: list[Any], estado: ControlState | None = None) -> tuple[list[Job], set[str]]:
    """Busca em todas as fontes. Uma que falhe não derruba as outras.

    Retorna as vagas e o conjunto de fontes que responderam **sem erro** — uma
    fonte que só falhou não pode ser dada como inicializada, senão na próxima
    vez que ela responder o catálogo inteiro dela vira "novidade".
    """
    todas: list[Job] = []
    ok: set[str] = set()
    agora = time.monotonic()

    for fonte in fontes:
        # Pausada pelo /pausar: nem consulta.
        if estado is not None and estado.is_paused(fonte.name):
            continue
        if not fonte.is_due(agora):
            continue
        # Marca antes de tentar: se a fonte estiver com problema, ela espera o
        # intervalo dela em vez de ser martelada a cada ciclo.
        fonte.mark_fetched(agora)

        inicio = time.monotonic()
        try:
            jobs = fonte.fetch()
        except (SourceError, requests.RequestException) as exc:
            log.error("Fonte %s falhou: %s — seguindo com as outras", fonte.name, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("Fonte %s quebrou de forma inesperada: %s", fonte.name, exc)
            continue

        fonte.last_count = len(jobs)
        log.info("Fonte %s: %d vagas em %.1fs", fonte.name, len(jobs), time.monotonic() - inicio)
        todas.extend(jobs)
        ok.add(fonte.name)
    return todas, ok


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def check_new_jobs(fontes: list[Any], seen_uids: set[str], seen_keys: set[str],
                   inicializadas: set[str],
                   estado: ControlState | None = None) -> tuple[set[str], set[str], set[str]]:
    """Executa um ciclo. Retorna (uids vistos, chaves vistas, fontes inicializadas)."""
    labels = {f.name: getattr(f, "label", f.name) for f in fontes}
    jobs, fontes_ok = coletar(fontes, estado)
    if not fontes_ok:
        # Nenhuma fonte estava no horário dela — situação normal, não é erro.
        log.debug("Nenhuma fonte no horário neste tick.")
        return seen_uids, seen_keys, inicializadas
    if not jobs:
        log.warning("Fontes %s consultadas, nenhuma vaga retornada.",
                    ", ".join(sorted(fontes_ok)))
        return seen_uids, seen_keys, inicializadas

    uids_do_ciclo = {j.uid for j in jobs}

    # Fonte nova (ou primeira execução): registra o acervo existente SEM notificar.
    # Sem isto, ligar uma fonte despejaria o catálogo inteiro dela no grupo.
    novas = fontes_ok - inicializadas
    if novas:
        registradas = [j for j in jobs if j.source in novas]
        for j in registradas:
            seen_uids.add(j.uid)
            seen_keys.add(dedup_key(j))
        inicializadas |= novas
        log.info(
            "Primeira coleta de %s — %d vagas registradas sem notificar (evita flood).",
            ", ".join(sorted(novas)), len(registradas),
        )
        save_seen(seen_uids, seen_keys, inicializadas)

    novos = [j for j in jobs if j.uid not in seen_uids]
    if not novos:
        log.info("No new jobs (checked %d).", len(jobs))
        log_filter_stats()
        return seen_uids | uids_do_ciclo, seen_keys, inicializadas

    log.info("Found %d new job(s).", len(novos))

    # Mais antigo → mais recente, para o grupo receber em ordem cronológica.
    novos.sort(key=lambda j: j.published_at or "")

    retry_uids: set[str] = set()

    for job in novos:
        # 1) mesma vaga em outra fonte (ou já enviada antes)?
        chave = dedup_key(job)
        if chave in seen_keys:
            log.info("Job %s duplicada (já vista como %s) — ignorando", job.uid, chave[:40])
            _bump("deduped", job.source)
            seen_uids.add(job.uid)
            continue

        # 2) pré-filtro de remoto, de graça, antes de gastar classificador
        fonte = next((f for f in fontes if f.name == job.source), None)
        if fonte is not None and getattr(fonte, "prefilter_remote", False):
            if not parece_remoto(job):
                _bump("prefiltered", job.source)
                seen_uids.add(job.uid)
                seen_keys.add(chave)
                continue

        # 3) classificador
        analysis = analyze_job(job)
        category = analysis["category"]
        log.info(
            "Job %s → %s (%s) — %s",
            job.uid, category, analysis["work_mode"], analysis["reason"],
        )

        if category == "irrelevant":
            log_skipped_job(job, analysis)
            _bump("skipped", job.source)
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        # 4) envio
        try:
            send_telegram(format_job(job, analysis, labels.get(job.source, "")))
            log.info("Notified %s (%s) — %s", job.uid, category, job.title)
            _bump("sent", job.source)
        except requests.RequestException as exc:
            if is_transient_send_error(exc):
                log.error("Failed to send %s: %s — sera reenviada no proximo ciclo",
                          job.uid, exc)
                retry_uids.add(job.uid)
            else:
                log.error("Failed to send %s: %s — erro permanente, vaga descartada",
                          job.uid, exc)
                seen_uids.add(job.uid)
            continue

        seen_uids.add(job.uid)
        seen_keys.add(chave)
        time.sleep(TELEGRAM_RATE_LIMIT_SECONDS)

    seen_uids |= uids_do_ciclo - retry_uids
    save_seen(seen_uids, seen_keys, inicializadas)
    log_filter_stats()
    return seen_uids, seen_keys, inicializadas


def validate_env() -> None:
    missing = [
        name for name, val in {
            "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
            "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        }.items() if not val
    ]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


def main() -> None:
    validate_env()
    fontes = build_sources()
    log.info("AV Jobs Bot starting (tick=%ds, data_dir=%s)", CHECK_INTERVAL, DATA_DIR)
    for f in fontes:
        log.info(
            "  fonte %-9s a cada %4dmin  (pre-filtro de remoto: %s)",
            f.name, f.interval_seconds // 60, "sim" if f.prefilter_remote else "nao",
        )

    # Pré-carrega profile e client pra mostrar o status do filtro no startup
    profile = load_profile()
    client = get_genai_client()
    if profile and client:
        log.info("Filter ENABLED (model=%s, profile=%s)", GEMINI_MODEL, PROFILE_FILE)
    else:
        reasons = []
        if not GENAI_AVAILABLE:
            reasons.append("google-genai não instalado")
        elif not GEMINI_API_KEY:
            reasons.append("GEMINI_API_KEY ausente")
        if profile is None:
            reasons.append(f"{PROFILE_FILE} não encontrado")
        log.warning("Filter DISABLED — notificando TUDO. Motivo(s): %s", "; ".join(reasons))

    _reset_stats(_hoje())
    seen_uids, seen_keys, inicializadas = load_seen()
    pendentes = {f.name for f in fontes} - inicializadas
    if pendentes:
        log.info("Fontes ainda não inicializadas: %s", ", ".join(sorted(pendentes)))

    estado = ControlState(CONTROL_FILE)

    # O listener sobe sempre, inclusive sem nenhum admin cadastrado — é o que
    # faz o /id funcionar no cadastro do primeiro. Nenhum comando com efeito
    # roda para quem não está em TELEGRAM_ADMIN_IDS.
    CommandListener(
        token=TELEGRAM_TOKEN,
        admin_ids=ADMIN_IDS,
        state=estado,
        handlers=build_handlers(fontes, estado),
    ).start()
    if ADMIN_IDS:
        log.info("Admins do bot: %s", ", ".join(str(i) for i in sorted(ADMIN_IDS)))
    else:
        log.warning(
            "TELEGRAM_ADMIN_IDS vazio — nenhum comando com efeito vai funcionar. "
            "Mande /id no privado do bot e cadastre o número que ele responder."
        )

    log.info("Relatório diário às %dh (%s), destino: %s",
             REPORT_HOUR, TIMEZONE_NAME, REPORT_TO)

    while True:
        try:
            seen_uids, seen_keys, inicializadas = check_new_jobs(
                fontes, seen_uids, seen_keys, inicializadas, estado
            )
        except requests.RequestException as exc:
            log.error("Network error: %s — will retry next cycle", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error: %s — will retry next cycle", exc)

        # Relatório do dia. O dia é o LOCAL, não o UTC — senão viraria às 21h.
        try:
            agora = agora_local()
            dia = agora.strftime("%Y-%m-%d")
            if agora.hour >= REPORT_HOUR and not estado.ja_relatou(dia):
                enviar_relatorio(fontes, estado)
                estado.marcar_relatado(dia)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro no relatório diário: %s", exc)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
