"""ONM Jobs Bot — monitora novos jobs em "O Mercado de Trabalho" e notifica via Telegram."""

from __future__ import annotations

import html
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests
from dotenv import load_dotenv

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
log = logging.getLogger("onm-bot")

ONM_EMAIL = os.getenv("ONM_EMAIL", "").strip()
ONM_PASSWORD = os.getenv("ONM_PASSWORD", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))
DATA_DIR = Path(os.getenv("DATA_DIR", "."))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
PROFILE_FILE = Path(os.getenv("PROFILE_FILE", "profile.md"))

LOGIN_URL = "https://auth.onovomercado.com.br/api/auth/login"
PROJECTS_URL = (
    "https://api.onovomercado.com.br/mercado-de-trabalho/v1/projects"
    "?page=1&limit=15&sortDir=DESC"
)
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

JWT_COOKIE_NAME = "onm-sso-jwt-token"
SEEN_IDS_FILE = DATA_DIR / "seen_ids.json"
SKIPPED_LOG_FILE = DATA_DIR / "skipped_jobs.jsonl"

REQUEST_TIMEOUT = 30
TELEGRAM_RATE_LIMIT_SECONDS = 1.0
DESCRIPTION_MAX_CHARS = 300

Category = Literal["relevant", "borderline", "irrelevant"]


# ---------------------------------------------------------------------------
# Persistência de IDs vistos
# ---------------------------------------------------------------------------

def load_seen_ids() -> set[int]:
    if not SEEN_IDS_FILE.exists():
        return set()
    try:
        with SEEN_IDS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log.warning("Failed to read %s: %s — starting empty", SEEN_IDS_FILE, exc)
        return set()


def save_seen_ids(ids: set[int]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_IDS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(sorted(ids), f)
    tmp.replace(SEEN_IDS_FILE)


# ---------------------------------------------------------------------------
# ONM API
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Erro de autenticação — token expirado ou credenciais inválidas."""


def login() -> str:
    """Autentica na API do ONM e retorna o JWT do cookie onm-sso-jwt-token."""
    log.info("Logging in to ONM as %s", ONM_EMAIL)
    resp = requests.post(
        LOGIN_URL,
        json={"email": ONM_EMAIL, "password": ONM_PASSWORD, "client": "mdt"},
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.cookies.get(JWT_COOKIE_NAME)
    if not token:
        raise AuthError(
            f"Login OK mas cookie {JWT_COOKIE_NAME} não encontrado na resposta"
        )
    log.info("Login successful (token len=%d)", len(token))
    return token


def fetch_projects(token: str) -> list[dict[str, Any]]:
    """Busca os projects mais recentes. Lança AuthError se receber 401."""
    resp = requests.get(
        PROJECTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        raise AuthError("Token expirado (401)")
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("content", []) or []


# ---------------------------------------------------------------------------
# Classificador (Gemini)
# ---------------------------------------------------------------------------

_genai_client: Any = None
_profile_text: str | None = None
_profile_mtime: float | None = None

CLASSIFIER_INSTRUCTIONS = """Você é um filtro de oportunidades de trabalho freelancer.
Recebe descrições de projetos/vagas em português brasileiro de uma plataforma
chamada "O Mercado de Trabalho" e decide se interessam ao perfil informado.

Importante:
- Você está classificando para um estúdio de DESENVOLVIMENTO DE SOFTWARE.
- Descrições têm grafia inconsistente, abreviações, gírias e erros de digitação.
  Trate "LP", "lendingpage", "landing page", "pagina de vendas" (sem acento)
  como sinônimos. Idem para "n8n"/"N8N"/"en oito en", "WP"/"WordPress", etc.
- Considere o título, a profissão/área, as skills E a descrição. Às vezes a
  descrição é vaga mas a categoria/skills denuncia que é da área.
- Se houver QUALQUER dúvida razoável, retorne "borderline" — é melhor o
  usuário receber uma notificação a mais e ignorar do que perder um job.
- "irrelevant" só para casos claros: design puro sem dev, redação pura,
  trabalhos fora de TI (engenharia civil, advocacia, medicina etc. SEM
  componente de software).

Retorne JSON com:
- category: "relevant" | "borderline" | "irrelevant"
- reason: uma frase curta em português explicando a decisão (até 100 chars)
"""

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["relevant", "borderline", "irrelevant"],
        },
        "reason": {"type": "string"},
    },
    "required": ["category", "reason"],
}


def load_profile() -> str | None:
    """Lê profile.md (com cache por mtime — edições no arquivo são pegas em runtime)."""
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
    """Lazy init do client do Gemini. Retorna None se não configurado/disponível."""
    global _genai_client
    if not GENAI_AVAILABLE:
        return None
    if not GEMINI_API_KEY:
        return None
    if _genai_client is None:
        try:
            _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            log.info("Gemini client initialized (model=%s)", GEMINI_MODEL)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to init Gemini client: %s", exc)
            return None
    return _genai_client


def job_to_classifier_input(job: dict[str, Any]) -> str:
    """Serializa o job num blob de texto compacto pro classificador."""
    title = (job.get("title") or "").strip()
    description = (job.get("description") or "").strip()
    job_type = "Vaga (CLT/PJ)" if job.get("type") == "POSITION" else "Projeto freelance"

    profession_obj = job.get("profession") or {}
    profession = (profession_obj.get("description") or "").strip()
    area = ((profession_obj.get("occupationArea") or {}).get("title") or "").strip()

    skills = job.get("skills") or []
    skill_names = [s.get("description") for s in skills if s and s.get("description")]
    skills_str = ", ".join(skill_names)

    parts = [f"Tipo: {job_type}", f"Título: {title}"]
    if profession or area:
        parts.append(f"Categoria: {profession}{' · ' + area if area else ''}")
    if skills_str:
        parts.append(f"Skills: {skills_str}")
    parts.append(f"Descrição: {description or '(sem descrição)'}")
    return "\n".join(parts)


def classify_job(job: dict[str, Any]) -> tuple[Category, str]:
    """Classifica um job. Em caso de erro/falta de config, retorna 'relevant' (fallback seguro)."""
    profile = load_profile()
    client = get_genai_client()

    if profile is None or client is None:
        return ("relevant", "filtro desativado (sem profile.md ou GEMINI_API_KEY)")

    prompt = (
        f"{CLASSIFIER_INSTRUCTIONS}\n\n"
        f"=== PERFIL DO USUÁRIO ===\n{profile}\n\n"
        f"=== JOB A CLASSIFICAR ===\n{job_to_classifier_input(job)}"
    )

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
        category: Category = data.get("category", "borderline")
        reason: str = (data.get("reason") or "").strip()[:200]
        if category not in ("relevant", "borderline", "irrelevant"):
            log.warning("Classifier returned invalid category %r for job %s", category, job.get("id"))
            return ("borderline", reason or "categoria inválida do classificador")
        return (category, reason)
    except Exception as exc:  # noqa: BLE001
        log.error("Classifier failed for job %s: %s — defaulting to 'relevant'", job.get("id"), exc)
        return ("relevant", f"erro no classificador ({type(exc).__name__})")


def log_skipped_job(job: dict[str, Any], reason: str) -> None:
    """Anexa o job descartado em skipped_jobs.jsonl pra revisão posterior."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "skipped_at": datetime.now(timezone.utc).isoformat(),
        "id": job.get("id"),
        "title": job.get("title"),
        "type": job.get("type"),
        "reason": reason,
        "url": f"https://omercadodetrabalho.com/"
               f"{'vagas' if job.get('type') == 'POSITION' else 'projetos'}/{job.get('id')}",
    }
    try:
        with SKIPPED_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("Failed to log skipped job %s: %s", job.get("id"), exc)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> None:
    resp = requests.post(
        TELEGRAM_URL,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not resp.ok:
        log.error("Telegram error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()


def truncate_description(text: str, max_chars: int = DESCRIPTION_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # corta na última palavra completa
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "..."


def format_job(job: dict[str, Any], category: Category = "relevant", reason: str = "") -> str:
    job_type = job.get("type") or ""
    type_label = "🏢 VAGA" if job_type == "POSITION" else "📋 PROJETO"
    if category == "borderline":
        type_label = f"🤔 {type_label} (talvez)"

    title = html.escape(job.get("title") or "(sem título)")

    author = (job.get("author") or {}).get("name") or "Desconhecido"
    author = html.escape(author)

    profession_obj = job.get("profession") or {}
    profession = profession_obj.get("description") or ""
    area = ((profession_obj.get("occupationArea") or {}).get("title")) or ""
    if profession and area:
        prof_line = f"🏷 {html.escape(profession)} · {html.escape(area)}"
    elif profession:
        prof_line = f"🏷 {html.escape(profession)}"
    elif area:
        prof_line = f"🏷 {html.escape(area)}"
    else:
        prof_line = ""

    skills = job.get("skills") or []
    skill_names = [s.get("description") for s in skills if s and s.get("description")]
    skills_line = ""
    if skill_names:
        skills_line = "🔧 " + html.escape(", ".join(skill_names))

    budget_line = ""
    bmin = job.get("budgetMin")
    bmax = job.get("budgetMax")
    has_min = bmin not in (None, 0, 0.0)
    has_max = bmax not in (None, 0, 0.0)
    if has_min and has_max:
        budget_line = f"💰 R${bmin:.0f} – R${bmax:.0f}"
    elif has_max:
        budget_line = f"💰 até R${bmax:.0f}"
    elif has_min:
        budget_line = f"💰 a partir de R${bmin:.0f}"

    created = job.get("createdAt") or ""
    date_line = f"📅 {html.escape(created)}" if created else ""

    description = truncate_description(job.get("description") or "")
    description_html = f"<i>{html.escape(description)}</i>" if description else ""

    job_id = job.get("id")
    # path baseado no type — ajustar se necessário
    path = "vagas" if job_type == "POSITION" else "projetos"
    link = f"https://omercadodetrabalho.com/{path}/{job_id}"

    lines = [
        f"{type_label}",
        "",
        f"📌 <b>{title}</b>",
        "",
        f"👤 {author}",
    ]
    for line in (prof_line, skills_line, budget_line, date_line):
        if line:
            lines.append(line)
    if description_html:
        lines.append("")
        lines.append(description_html)
    if category == "borderline" and reason:
        lines.append("")
        lines.append(f"<i>🤖 Motivo: {html.escape(reason)}</i>")
    lines.append("")
    lines.append(f'🔗 <a href="{link}">Ver no ONM</a>')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def check_new_jobs(token: str, seen_ids: set[int], first_run: bool) -> tuple[str, set[int], bool]:
    """Executa um ciclo. Retorna (novo_token, novo_seen_ids, novo_first_run).

    Se receber AuthError, re-autentica e retenta uma vez.
    """
    try:
        jobs = fetch_projects(token)
    except AuthError as exc:
        log.warning("%s — re-authenticating", exc)
        token = login()
        jobs = fetch_projects(token)

    current_ids = {int(j["id"]) for j in jobs if j.get("id") is not None}

    if first_run:
        log.info("First run — saving %d existing job IDs (no notifications).", len(current_ids))
        save_seen_ids(current_ids)
        return token, current_ids, False

    new_ids = current_ids - seen_ids
    if not new_ids:
        log.info("No new jobs (checked %d).", len(current_ids))
        return token, seen_ids | current_ids, False

    log.info("Found %d new job(s).", len(new_ids))
    new_jobs = [j for j in jobs if int(j.get("id", -1)) in new_ids]
    # mais antigo → mais recente (a API vem DESC)
    for job in reversed(new_jobs):
        job_id = int(job["id"])
        category, reason = classify_job(job)
        log.info("Job %s classified as %s — %s", job_id, category, reason)

        if category == "irrelevant":
            log_skipped_job(job, reason)
            seen_ids.add(job_id)
            continue

        try:
            send_telegram(format_job(job, category, reason))
            log.info("Notified job %s (%s) — %s", job_id, category, job.get("title"))
        except requests.RequestException as exc:
            log.error("Failed to send job %s: %s", job_id, exc)
            # não atualiza seen_ids para esse job — tenta de novo no próximo ciclo
            continue
        seen_ids.add(job_id)
        time.sleep(TELEGRAM_RATE_LIMIT_SECONDS)

    seen_ids |= current_ids
    save_seen_ids(seen_ids)
    return token, seen_ids, False


def validate_env() -> None:
    missing = [
        name for name, val in {
            "ONM_EMAIL": ONM_EMAIL,
            "ONM_PASSWORD": ONM_PASSWORD,
            "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
            "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        }.items() if not val
    ]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


def main() -> None:
    validate_env()
    log.info("ONM Jobs Bot starting (interval=%ds, data_dir=%s)", CHECK_INTERVAL, DATA_DIR)

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

    seen_ids = load_seen_ids()
    first_run = len(seen_ids) == 0 and not SEEN_IDS_FILE.exists()
    token: str | None = None

    while True:
        try:
            if token is None:
                token = login()
            token, seen_ids, first_run = check_new_jobs(token, seen_ids, first_run)
        except AuthError as exc:
            log.error("Auth error: %s — will retry next cycle", exc)
            token = None
        except requests.RequestException as exc:
            log.error("Network error: %s — will retry next cycle", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error: %s — will retry next cycle", exc)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
