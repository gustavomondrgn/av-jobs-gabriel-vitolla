"""AV Jobs Bot — monitora vagas em várias fontes e notifica no Telegram.

Filtra as vagas para o perfil de Assistente Virtual descrito em `profile.md`.
As fontes ficam em `sources.py`; aqui mora só o pipeline:

    buscar → deduplicar → filtros baratos → classificar → ENFILEIRAR → despachar

O penúltimo passo é o que mudou em 12/08: aprovar uma vaga deixou de significar
publicá-la. A vaga aprovada entra numa fila com nota (`dispatch.py`) e um
despachante publica as melhores, no máximo N por dia, espaçadas dentro da janela
de horário. Sem isso o grupo recebia tudo, de madrugada inclusive.
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

import filters
import vitality
from bot_control import CommandListener
from dispatch import SendQueue
from publicadas import RegistroPublicadas
from sources import (
    GupySource, IndeedSource, Job, LinkedInSource, ONMSource, SourceError,
)
from store import Store
from telemetry import DailyStats

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

# Os arquivos de configuração do filtro moram ao lado do código, em bot/config/.
# O caminho é resolvido a partir DESTE arquivo, não do diretório de trabalho:
# assim `python bot/main.py` funciona de qualquer pasta e o container não
# depende de onde o CMD foi chamado.
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"


def _config_file(env_var: str, nome: str) -> Path:
    """Resolve um arquivo de configuração, tolerando caminho velho no ambiente.

    Cuidado que já custou caro uma vez: até 12/08 estes arquivos ficavam na raiz
    e o painel do Coolify guarda `PROFILE_FILE=profile.md` de lá. Honrar esse
    valor cegamente depois da mudança de pastas apontaria para um arquivo
    inexistente — e o bot trata "sem profile.md" como **filtro desligado**, ou
    seja, publicaria tudo. Se o caminho do ambiente não existir, cai para
    `bot/config/`, que existe sempre porque vai dentro da imagem.
    """
    padrao = CONFIG_DIR / nome
    bruto = os.getenv(env_var, "").strip()
    if not bruto:
        return padrao
    caminho = Path(bruto)
    if caminho.exists():
        return caminho
    log.warning("%s=%r não existe — usando %s", env_var, bruto, padrao)
    return padrao


PROFILE_FILE = _config_file("PROFILE_FILE", "profile.md")
TERMS_FILE = _config_file("TERMS_FILE", "search_terms.txt")
# O LinkedIn tem lista própria e curta: lá cada vaga custa duas requisições.
LINKEDIN_TERMS_FILE = _config_file("LINKEDIN_TERMS_FILE", "search_terms_linkedin.txt")

# Fontes ativas, separadas por vírgula. Permite desligar uma sem mexer no código.
ENABLED_SOURCES = os.getenv("SOURCES", "onm,gupy,indeed,linkedin")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SEEN_FILE = DATA_DIR / "seen_ids.json"
SKIPPED_LOG_FILE = DATA_DIR / "skipped_jobs.jsonl"
QUOTA_LOG_FILE = DATA_DIR / "quota_log.jsonl"
STATS_FILE = DATA_DIR / "daily_stats.json"
QUEUE_FILE = DATA_DIR / "send_queue.json"
PUBLICADAS_FILE = DATA_DIR / "publicadas.json"

# O container roda em UTC; tudo que é "dia" para o usuário é dia de Brasília.
TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "22"))
# Para onde vai o relatório diário: "grupo" ou "privado".
REPORT_TO = os.getenv("REPORT_TO", "grupo").strip().lower()
# Quem recebe o relatório quando REPORT_TO=privado. Não dá poder nenhum sobre o
# bot — desde 12/08 não existe comando administrativo.
REPORT_CHAT_IDS = [
    p.strip() for p in os.getenv("REPORT_CHAT_IDS", "").replace(";", ",").split(",")
    if p.strip()
]

# Chats que tinham menu de comandos administrativos antes de 12/08. Não dá
# nenhum poder a eles — serve só para o bot conseguir APAGAR o menu antigo, que
# o Telegram guarda por escopo de chat e sobrevive à remoção dos comandos.
# Reaproveita TELEGRAM_ADMIN_IDS porque é exatamente essa a lista, e ela já está
# cadastrada no Coolify.
def _ids(bruto: str) -> set[int]:
    saida: set[int] = set()
    for pedaco in (bruto or "").replace(";", ",").split(","):
        pedaco = pedaco.strip()
        if pedaco.lstrip("-").isdigit():
            saida.add(int(pedaco))
    return saida


CHATS_MENU_LEGADO = _ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))


def _flag(nome: str, padrao: bool) -> bool:
    bruto = os.getenv(nome, "").strip().lower()
    if not bruto:
        return padrao
    return bruto in ("1", "true", "sim", "yes", "on")


# --- Controle de volume e horário -----------------------------------------
# Padrões pedidos pelo Gabriel em 12/08. O painel sobrescreve em runtime.
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "8"))
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "6"))
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "23"))
QUEUE_TTL_DAYS = int(os.getenv("QUEUE_TTL_DAYS", "3"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "0"))

# --- Vaga encerrada --------------------------------------------------------
# O que fazer quando a plataforma tira o anúncio do ar: "apagar" some com a
# mensagem no grupo (padrão, pedido do Gabriel — vaga morta é ruído num feed de
# vagas), "marcar" a mantém riscada com um aviso, "nada" só registra no painel.
ACAO_VAGA_ENCERRADA = os.getenv("ACAO_VAGA_ENCERRADA", "apagar").strip().lower()
# De quanto em quanto tempo cada vaga é reexaminada, e quantas por ciclo.
# Com 8 publicações/dia e 30 dias de acompanhamento são ~240 vagas vivas; a uma
# checagem por dia cada uma, dá ~240 requisições/dia no total, contra as ~1.450
# que o bot já faz para buscar. O Indeed é checado em lote, então na prática
# fica ainda abaixo disso.
RECHECK_HORAS = int(os.getenv("RECHECK_HORAS", "24"))
RECHECK_POR_CICLO = int(os.getenv("RECHECK_POR_CICLO", "12"))
RECHECK_DIAS = int(os.getenv("RECHECK_DIAS", "30"))

# --- Regras de corte -------------------------------------------------------
REJECT_ENGLISH = _flag("REJECT_ENGLISH", True)
REJECT_SENIOR = _flag("REJECT_SENIOR", True)
REQUIRE_EXPLICIT_REMOTE = _flag("REQUIRE_EXPLICIT_REMOTE", True)

# --- Apresentação do bot no privado ---------------------------------------
SITE_URL = os.getenv("SITE_URL", "https://gabrielvitolla.com.br").strip()
INSTAGRAM_URL = os.getenv(
    "INSTAGRAM_URL", "https://instagram.com/gabriel.assistentevirtual"
).strip()
SUPORTE_TELEGRAM = os.getenv("SUPORTE_TELEGRAM", "").strip()


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


def hoje_local() -> str:
    """O "dia" do bot.

    Precisa ser o dia LOCAL, não o UTC. Com UTC, os contadores viravam às 21h de
    Brasília e o relatório das 22h somava só a última hora — era esse o bug do
    relatório reportado pelo Gabriel.
    """
    return agora_local().strftime("%Y-%m-%d")


REQUEST_TIMEOUT = 30
TELEGRAM_RATE_LIMIT_SECONDS = 1.0
DESCRIPTION_MAX_CHARS = 300

Category = Literal["relevant", "borderline", "irrelevant"]
WorkMode = Literal["remoto", "hibrido", "presencial", "nao_informado"]

# Modalidades que sempre derrubam a vaga. "nao_informado" entra ou não conforme
# `require_explicit_remote` — ver `modos_recusados()`.
REJECTED_WORK_MODES: tuple[WorkMode, ...] = ("presencial", "hibrido")

# As sete famílias de vaga do perfil, mais um escape. O painel liga e desliga
# cada uma por fonte, então isto precisa ser uma lista FECHADA: se o
# classificador pudesse inventar categoria, apareceria opção nova no painel a
# cada semana e o que o Gabriel desligasse não corresponderia a nada.
# Mudar esta lista exige mudar `CATEGORIAS` em admin/src/lib/config-tipos.ts.
CATEGORIAS: tuple[str, ...] = (
    "secretariado",      # Assistente Virtual, Secretária Remota, Assist. Executivo
    "atendimento",       # Atendimento e suporte ao cliente
    "comercial",         # SDR, BDR, Closer, Inside Sales
    "administrativo",    # Auxiliar/Analista Administrativo, Backoffice
    "financeiro",        # Contas a pagar/receber, cobrança, faturamento
    "agenda_clinicas",   # Secretária de consultório, agendamento
    "customer_success",  # CS, pós-venda, relacionamento
    "outro",             # encaixa no perfil mas não em nenhuma das acima
)

STATS = DailyStats(STATS_FILE, historico=QUOTA_LOG_FILE)
FILA = SendQueue(QUEUE_FILE, validade_dias=QUEUE_TTL_DAYS)
PUBLICADAS = RegistroPublicadas(PUBLICADAS_FILE, dias_de_vida=RECHECK_DIAS)
STORE = Store()


# ---------------------------------------------------------------------------
# Configuração efetiva (ambiente + painel)
# ---------------------------------------------------------------------------

def config_atual() -> dict[str, Any]:
    """Junta o que veio do ambiente com o que o painel escreveu no banco.

    O painel ganha quando define a chave. Sem banco, sobra só o ambiente — que
    é o modo como o bot rodou até agora e continua rodando se o Postgres cair.
    """
    base: dict[str, Any] = {
        "daily_limit": DAILY_LIMIT,
        "window_start": SEND_WINDOW_START,
        "window_end": SEND_WINDOW_END,
        "min_score": MIN_SCORE,
        "reject_english": REJECT_ENGLISH,
        "reject_senior": REJECT_SENIOR,
        "require_explicit_remote": REQUIRE_EXPLICIT_REMOTE,
        "sources": {},
    }
    base["acao_vaga_encerrada"] = ACAO_VAGA_ENCERRADA
    base["recheck_horas"] = RECHECK_HORAS
    base.update({k: v for k, v in STORE.config().items() if v is not None})
    return base


def regra(cfg: dict[str, Any], fonte: str, chave: str) -> Any:
    """Valor de uma regra para uma fonte: o específico dela, senão o geral.

    É o que permite ao Gabriel afrouxar uma regra só no ONM sem afrouxar no
    resto — pedido explícito dele para o painel.
    """
    por_fonte = (cfg.get("sources") or {}).get(fonte) or {}
    if chave in por_fonte and por_fonte[chave] is not None:
        return por_fonte[chave]
    return cfg.get(chave)


def fonte_ligada(cfg: dict[str, Any], fonte: str) -> bool:
    por_fonte = (cfg.get("sources") or {}).get(fonte) or {}
    return bool(por_fonte.get("enabled", True))


def modos_recusados(cfg: dict[str, Any], fonte: str) -> tuple[str, ...]:
    if regra(cfg, fonte, "require_explicit_remote"):
        return REJECTED_WORK_MODES + ("nao_informado",)
    return REJECTED_WORK_MODES


def categoria_ligada(cfg: dict[str, Any], fonte: str, categoria: str) -> bool:
    """Esta fonte pode trazer vagas desta família?

    Ausente = ligada. O padrão é permissivo porque o painel só grava o que o
    Gabriel mexeu: uma categoria nova, criada depois da última vez que ele
    salvou, precisa nascer funcionando em vez de sumir em silêncio.
    """
    por_fonte = (cfg.get("sources") or {}).get(fonte) or {}
    categorias = por_fonte.get("categorias")
    if not isinstance(categorias, dict):
        return True
    return categorias.get(categoria, True) is not False


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


def parece_remoto(job: Job) -> bool:
    """Heurística barata: a fonte diz que é remoto, ou o texto diz."""
    if job.remote_hint == "remoto":
        return True
    return filters.texto_menciona_remoto(f"{job.title}\n{job.description}")


# ---------------------------------------------------------------------------
# Classificador (Gemini)
# ---------------------------------------------------------------------------

_genai_client: Any = None
_profile_text: str | None = None
_profile_mtime: float | None = None

CLASSIFIER_INSTRUCTIONS = """Você lê vagas e projetos de trabalho em português
brasileiro e faz três coisas: (A) decide se a vaga interessa ao perfil informado
logo abaixo destas instruções, (B) extrai os dados da vaga e (C) dá uma nota de
qualidade à oportunidade.

== (A) CLASSIFICAÇÃO ==

- O PERFIL DO USUÁRIO é a única fonte de verdade sobre o que interessa. Não
  presuma nenhuma área por conta própria — siga o que o perfil diz.
- Descrições têm grafia inconsistente, abreviações, gírias e erros de digitação.
  Trate variações como sinônimos ("assistente virtual"/"AV"/"assistente
  vitual", "secretária remota"/"secretariado remoto", "home office"/"homeoffice"),
  inclusive sem acento.
- Considere o título, a profissão/área, as skills E a descrição. Às vezes a
  descrição é vaga mas a categoria/skills denuncia que é da área.
- Se houver QUALQUER dúvida razoável, retorne "borderline".
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
- language: idioma em que o ANÚNCIO está escrito — "pt", "en", "es" ou "outro".
  É o idioma do texto, não o idioma exigido do candidato. Vaga escrita em
  português que pede inglês fluente é "pt".
- requires_english: true só se a vaga EXIGIR inglês (fluente, avançado,
  conversação, atendimento a cliente estrangeiro). Inglês "desejável" ou
  "diferencial" é false.
- seniority: "estagio", "junior", "pleno", "senior" ou "nao_informado".
  Use "senior" quando o título ou o texto posicionar a vaga como sênior, ou
  quando exigir 5+ anos de experiência na função. Liderar equipe também conta
  como "senior". Não confunda com a vaga que apenas *reporta* a alguém sênior.
- company: nome da empresa/instituição contratante, se aparecer no texto.
  Se só houver o nome de uma pessoa física, ou nada, devolva "".
- role_type: a função em 2-4 palavras, normalizada (ex.: "Assistente Virtual",
  "SDR", "Assistente Financeiro", "Customer Success"). Se não der pra
  determinar, devolva "".
- categoria: em qual das famílias abaixo a vaga se encaixa MELHOR. Escolha
  exatamente uma; na dúvida entre duas, use a que descreve a maior parte do
  trabalho do dia a dia.
  - "secretariado" — assistente virtual, secretária remota, assistente
    executivo, assistente pessoal, apoio direto a uma pessoa ou diretoria.
  - "atendimento" — atender e dar suporte a cliente (WhatsApp, chat, telefone,
    e-mail, help desk), recepção remota.
  - "comercial" — vender ou prospectar: SDR, BDR, closer, inside sales,
    assistente comercial, prospecção ativa.
  - "administrativo" — rotinas administrativas, backoffice, cadastro,
    documentos, processos, apoio operacional interno.
  - "financeiro" — contas a pagar/receber, cobrança, faturamento, conciliação,
    apoio à contabilidade.
  - "agenda_clinicas" — agendamento e secretariado de consultório, clínica ou
    profissional de saúde; appointment setter.
  - "customer_success" — CS, pós-venda, onboarding, retenção, relacionamento
    contínuo com carteira de clientes.
  - "outro" — encaixa no perfil mas não em nenhuma das famílias acima.
- summary: resumo da vaga em 1-2 frases curtas (até 250 chars), em português,
  dizendo o que a pessoa vai fazer. Sem enrolação e sem repetir o título.
  Se a descrição for vazia ou inútil, devolva "".
- salary: remuneração citada NO TEXTO, como está escrita (ex.: "R$ 2.000/mês",
  "R$ 25/hora", "a combinar"). Se o texto não citar valor, devolva "".

== (C) NOTA DE QUALIDADE (score, 0 a 100) ==

Isto é o mais importante depois da classificação. O bot recebe MUITO mais vagas
aprovadas do que pode publicar: ele publica só um punhado por dia e escolhe pela
nota. Uma nota que não separa as vagas faz o bot publicar as erradas.

Portanto: **use a faixa inteira e seja severo**. Não concentre tudo entre 70 e
80. Se metade das vagas receber nota parecida, a nota não serviu para nada.

Comece em 50 e ajuste:

  +25 função exatamente de assistência virtual / secretariado remoto (o núcleo
      do perfil, o que o aluno procura de verdade)
  +10 função de atendimento, comercial, administrativo, financeiro ou CS
  +15 remuneração declarada e boa para o mercado brasileiro de assistente
      virtual (referência: R$ 1.500 a R$ 3.000/mês em jornada integral)
  +10 descrição completa e específica: responsabilidades claras, ferramentas
      citadas, jornada definida
  +10 empresa identificada e que aparenta estrutura real
   +5 processo com contato ou candidatura direta

  -15 descrição genérica, curta ou vaga demais para julgar
  -15 exige 3+ anos de experiência ou formação superior específica
  -10 exige inglês
  -10 remuneração declarada baixa (abaixo de R$ 1.200/mês em jornada integral)
  -20 a classificação foi "borderline" (encaixa no leque, mas não é o alvo)

Referências de calibragem:
  90+  assistente virtual 100% remota, empresa real, salário declarado e bom
  70   vaga sólida de atendimento/administrativo remoto, descrição decente
  50   encaixa na área, mas descrição rasa e sem salário
  30   borderline com pouca informação
  10   passou por pouco, quase não vale a vaga do dia
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
        "language": {"type": "string", "enum": ["pt", "en", "es", "outro"]},
        "requires_english": {"type": "boolean"},
        "seniority": {
            "type": "string",
            "enum": ["estagio", "junior", "pleno", "senior", "nao_informado"],
        },
        "categoria": {"type": "string", "enum": list(CATEGORIAS)},
        "score": {"type": "integer"},
        "company": {"type": "string"},
        "role_type": {"type": "string"},
        "summary": {"type": "string"},
        "salary": {"type": "string"},
    },
    "required": [
        "category", "reason", "work_mode", "language", "requires_english",
        "seniority", "score", "categoria", "company", "role_type", "summary",
        "salary",
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

def _bump(key: str, fonte: str | None = None, n: int = 1) -> None:
    STATS.bump(hoje_local(), key, fonte, n=n)


def log_filter_stats() -> None:
    """Loga o placar do dia. Chamado ao fim de cada ciclo."""
    totais = STATS.totais()
    if not totais:
        return
    unfiltered = totais.get("unfiltered", 0)
    line = (
        f"FILTRO (hoje {STATS.dia}): {totais.get('classified', 0)} classificadas, "
        f"{totais.get('prefiltered', 0)} cortadas no pre-filtro, "
        f"{totais.get('ingles', 0)} em ingles, "
        f"{totais.get('senior', 0)} senior, "
        f"{totais.get('sem_remoto', 0)} sem remoto declarado, "
        f"{totais.get('deduped', 0)} duplicadas, "
        f"{totais.get('queued', 0)} enfileiradas, "
        f"{totais.get('sent', 0)} publicadas, "
        f"{totais.get('rate_limited', 0)} rate-limits, "
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
        "language": "pt",
        "requires_english": False,
        "seniority": "nao_informado",
        "categoria": "outro",
        # Nota baixa de propósito: sem classificador não dá para afirmar que a
        # vaga é boa, então ela só sai se não houver nada melhor esperando.
        "score": 25,
        "company": "",
        "role_type": "",
        "summary": "",
        "salary": "",
    }


def analyze_job(job: Job, cfg: dict[str, Any]) -> dict[str, Any]:
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
                    if STATS.marcar_cota_anunciada():
                        log.error(
                            "COTA DIARIA DO GEMINI ESGOTADA (limite=%s/dia, model=%s). "
                            "A partir de agora TODAS as vagas vao para a fila SEM FILTRO "
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

    language = data.get("language") or "pt"
    seniority = data.get("seniority") or "nao_informado"
    requires_english = bool(data.get("requires_english"))

    categoria = data.get("categoria") or "outro"
    if categoria not in CATEGORIAS:
        log.warning("Categoria inválida %r em %s — usando 'outro'", categoria, job.uid)
        categoria = "outro"

    try:
        score = max(0, min(100, int(data.get("score", 50))))
    except (TypeError, ValueError):
        score = 50

    analise = {
        "category": category,
        "reason": reason,
        "work_mode": work_mode,
        "language": language,
        "requires_english": requires_english,
        "seniority": seniority,
        "categoria": categoria,
        "score": score,
        "company": (data.get("company") or "").strip()[:120],
        "role_type": (data.get("role_type") or "").strip()[:80],
        "summary": (data.get("summary") or "").strip()[:300],
        "salary": (data.get("salary") or "").strip()[:80],
    }

    # As regras duras rodam DEPOIS do classificador e passam por cima da
    # categoria dele. O modelo é bom lendo a vaga e ruim obedecendo regra
    # absoluta — então quem lê é ele, quem decide é o código.
    recusados = modos_recusados(cfg, job.source)
    if work_mode in recusados and category != "irrelevant":
        motivo = ("não declara trabalho remoto" if work_mode == "nao_informado"
                  else f"vaga {work_mode}")
        log.info("Job %s dropped by work_mode=%s (was %s)", job.uid, work_mode, category)
        analise["category"] = "irrelevant"
        analise["reason"] = f"{motivo} — só entram vagas 100% remotas"
        analise["motivo_corte"] = "sem_remoto"
        return analise

    if regra(cfg, job.source, "reject_english") and language == "en":
        log.info("Job %s dropped: anúncio em inglês", job.uid)
        analise["category"] = "irrelevant"
        analise["reason"] = "anúncio escrito em inglês"
        analise["motivo_corte"] = "ingles"
        return analise

    if regra(cfg, job.source, "reject_senior") and seniority == "senior":
        log.info("Job %s dropped: vaga sênior", job.uid)
        analise["category"] = "irrelevant"
        analise["reason"] = "vaga sênior"
        analise["motivo_corte"] = "senior"
        return analise

    if not categoria_ligada(cfg, job.source, categoria):
        log.info("Job %s dropped: categoria %s desligada em %s",
                 job.uid, categoria, job.source)
        analise["category"] = "irrelevant"
        analise["reason"] = f"categoria '{categoria}' desligada para esta fonte"
        analise["motivo_corte"] = "categoria"
        return analise

    return analise


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
        "score": analysis.get("score", 0),
        "url": job.url,
    }
    try:
        with SKIPPED_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("Failed to log skipped job %s: %s", job.uid, exc)


def registrar(job: Job, status: str, analysis: dict[str, Any] | None = None) -> None:
    """Espelha o destino da vaga no Postgres, para o painel. No-op sem banco."""
    a = analysis or {}
    STORE.registrar_evento(
        uid=job.uid, source=job.source, status=status, local_day=hoje_local(),
        title=job.title, company=a.get("company") or job.company, url=job.url,
        category=a.get("category", ""), work_mode=a.get("work_mode", ""),
        role_type=a.get("role_type", ""), salary=a.get("salary") or job.salary,
        score=int(a.get("score") or 0), language=a.get("language", ""),
        seniority=a.get("seniority", ""), reason=a.get("reason", ""),
        published_at=job.published_at, categoria=a.get("categoria", ""),
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str, chat_id: str | int | None = None) -> int | None:
    """Publica a mensagem. Devolve o `message_id`, que é o que dá o link dela.

    O painel não linka para a vaga na plataforma de origem — linka para a
    mensagem no grupo. Guardar esse id é o que torna isso possível: sem ele,
    depois de publicada a mensagem não teria como ser reencontrada.
    """
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
    try:
        return int(resp.json()["result"]["message_id"])
    except (ValueError, KeyError, TypeError):
        # A mensagem foi entregue; só não deu para ler o id. Não é motivo para
        # tratar o envio como falho e reenviar a vaga.
        return None


def apagar_mensagem(message_id: int) -> bool:
    """Apaga uma mensagem do grupo. O bot é admin com `can_delete_messages`,
    então não vale o limite de 48h que existe para bot comum."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id},
            timeout=REQUEST_TIMEOUT,
        )
        if r.ok:
            return True
        # "message to delete not found" = alguém já apagou. Missão cumprida.
        if "not found" in r.text.lower():
            return True
        log.warning("deleteMessage %s falhou: %s", message_id, r.text[:150])
    except requests.RequestException as exc:
        log.warning("deleteMessage %s falhou: %s", message_id, exc)
    return False


def marcar_mensagem_encerrada(message_id: int, html_original: str) -> bool:
    """Reescreve a mensagem avisando que a vaga saiu do ar.

    Preferido ao apagar como padrão: quem já tinha visto a vaga entende o que
    aconteceu, em vez de achar que a mensagem sumiu do nada. O texto original
    fica riscado, então o histórico do grupo continua fazendo sentido.
    """
    aviso = "🔴 <b>VAGA ENCERRADA</b> — não está mais disponível na plataforma"
    corpo = f"{aviso}\n\n<s>{_sem_tags(html_original)}</s>"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id,
                  "text": corpo[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        if r.ok or "not modified" in r.text.lower():
            return True
        log.warning("editMessageText %s falhou: %s", message_id, r.text[:150])
    except requests.RequestException as exc:
        log.warning("editMessageText %s falhou: %s", message_id, exc)
    return False


_TAGS_RE = re.compile(r"</?(?:b|i|s|u|code|pre)>")


def _sem_tags(html_texto: str) -> str:
    """Tira a formatação interna antes de riscar tudo.

    O `<s>` não pode envolver `<b>` aninhado sem o Telegram reclamar de HTML
    inválido, e o link vira texto para não convidar ao clique numa vaga morta.
    """
    limpo = _TAGS_RE.sub("", html_texto)
    limpo = re.sub(r'<a href="[^"]*">([^<]*)</a>', lambda m: m.group(1), limpo)
    return limpo


def is_transient_send_error(exc: requests.RequestException) -> bool:
    """Diz se vale retentar o envio no próximo ciclo.

    Falha de rede, timeout, 429 e 5xx são passageiros — a vaga volta para a
    fila. Um 4xx (fora o 429) é problema da mensagem em si (HTML inválido, chat
    errado, bot removido do grupo): retentar todo ciclo só repetiria o mesmo
    erro para sempre e travaria as vagas seguintes.
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

def montar_relatorio(fontes: list[Any], cfg: dict[str, Any],
                     titulo: str = "Relatório do dia") -> str:
    """Monta o resumo do dia: quantas vagas foram ao grupo, e de onde vieram.

    O relatório é do Gabriel, não nosso. Ele pediu explicitamente **só** o que
    entrega valor pra ele: quantas vagas o grupo recebeu hoje e a quebra dessas
    vagas por fonte. Nada de recusadas, descartadas, repetidas, cortadas no
    pré-filtro ou saúde da cota de IA — isso é diagnóstico interno, e no meio do
    relatório dele só servia pra fazer o número bom parecer pequeno.

    Os contadores continuam todos existindo (`telemetry.py`) e continuam
    visíveis no painel, que é o lugar certo pra diagnóstico.
    """
    hoje = hoje_local()
    STATS.virar_se_preciso(hoje)
    totais = STATS.totais()

    agora = agora_local()
    linhas = [f"📊 <b>{html.escape(titulo)}</b> — {agora.strftime('%d/%m/%Y')}", ""]

    enviadas = totais.get("sent", 0)
    limite = int(cfg.get("daily_limit") or DAILY_LIMIT)
    if enviadas:
        linhas.append(f"✅ <b>{enviadas}</b> de {limite} vaga(s) publicadas no grupo")
    else:
        linhas.append("😴 Nenhuma vaga publicada hoje")
    linhas.append("")

    # Só fonte e quantidade publicada. Fonte ligada que não rendeu nada aparece
    # com zero de propósito: "Gupy: 0" responde uma pergunta; a ausência da
    # linha deixa a dúvida de se a fonte rodou.
    linhas.append("<b>Por fonte</b>")
    ligadas = [f for f in fontes if fonte_ligada(cfg, f.name)]
    if ligadas:
        for f in ligadas:
            env = STATS.da_fonte(f.name).get("sent", 0)
            label = html.escape(getattr(f, "label", f.name))
            linhas.append(f"• <b>{label}</b>: {env} {'vaga' if env == 1 else 'vagas'}")
    else:
        linhas.append("<i>nenhuma fonte ligada</i>")

    return "\n".join(linhas)


def enviar_relatorio(fontes: list[Any], cfg: dict[str, Any],
                     titulo: str = "Relatório do dia") -> None:
    texto = montar_relatorio(fontes, cfg, titulo)

    destinos: list[str | int]
    if REPORT_TO in ("privado", "private", "dm"):
        destinos = list(REPORT_CHAT_IDS)
        if not destinos:
            log.warning("REPORT_TO=privado mas REPORT_CHAT_IDS está vazio — "
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


def coletar(fontes: list[Any], cfg: dict[str, Any]) -> tuple[list[Job], set[str]]:
    """Busca em todas as fontes. Uma que falhe não derruba as outras.

    Retorna as vagas e o conjunto de fontes que responderam **sem erro** — uma
    fonte que só falhou não pode ser dada como inicializada, senão na próxima
    vez que ela responder o catálogo inteiro dela vira "novidade".
    """
    todas: list[Job] = []
    ok: set[str] = set()
    agora = time.monotonic()

    for fonte in fontes:
        # Desligada no painel: nem consulta.
        if not fonte_ligada(cfg, fonte.name):
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
# Despacho: tira da fila e publica
# ---------------------------------------------------------------------------

def despachar(cfg: dict[str, Any]) -> None:
    """Publica no máximo uma vaga por tick, se for hora e houver cota.

    Uma por tick basta: o laço roda a cada `CHECK_INTERVAL` e o espaçamento
    calculado pela fila costuma ser de horas.
    """
    agora = agora_local()
    hoje = hoje_local()

    item = FILA.proxima(
        agora=agora,
        hoje=hoje,
        limite_diario=int(cfg.get("daily_limit") or DAILY_LIMIT),
        janela_inicio=int(cfg.get("window_start") if cfg.get("window_start") is not None
                          else SEND_WINDOW_START),
        janela_fim=int(cfg.get("window_end") if cfg.get("window_end") is not None
                       else SEND_WINDOW_END),
    )
    if item is None:
        return

    try:
        message_id = send_telegram(item["html"])
    except requests.RequestException as exc:
        if is_transient_send_error(exc):
            log.error("Falha publicando %s: %s — volta pra fila", item["uid"], exc)
            FILA.devolver(item)
        else:
            log.error("Falha publicando %s: %s — erro permanente, vaga descartada",
                      item["uid"], exc)
            _bump("falhou", item.get("source"))
        return

    FILA.confirmar_envio(agora, hoje)
    _bump("sent", item.get("source"))

    # Guarda o suficiente para revisitar a vaga depois e, se ela tiver saído do
    # ar, alcançar esta mensagem no grupo.
    PUBLICADAS.registrar(
        uid=item["uid"], source=item.get("source", ""),
        source_id=item["uid"].split(":", 1)[-1], title=item.get("title", ""),
        message_id=message_id, agora=agora, html=item.get("html", ""),
    )
    log.info("Publicada %s (nota %s) — %s",
             item["uid"], item.get("score"), item.get("title", "")[:70])

    STORE.registrar_evento(
        uid=item["uid"], source=item.get("source", ""), status="sent",
        local_day=hoje, title=item.get("title", ""),
        score=int(item.get("score") or 0), category=item.get("category", ""),
        published_at=item.get("published_at", ""),
        categoria=item.get("categoria", ""),
        telegram_message_id=message_id,
    )


# ---------------------------------------------------------------------------
# Revisor: a vaga publicada ainda está aberta?
# ---------------------------------------------------------------------------

def revisar(fontes: list[Any], cfg: dict[str, Any]) -> None:
    """Reexamina algumas vagas já publicadas e trata as que saíram do ar.

    Roda a conta-gotas — algumas por ciclo, cada vaga no máximo uma vez por
    `recheck_horas` — para não transformar a verificação num segundo scraping.
    Com 8 publicações por dia e 30 dias de acompanhamento são ~240 vagas; a 12
    por ciclo de 10 min, cada uma é revista com folga dentro do intervalo.
    """
    agora = agora_local()
    acao = str(cfg.get("acao_vaga_encerrada") or ACAO_VAGA_ENCERRADA).lower()
    if acao == "nada":
        return

    pendentes = PUBLICADAS.a_checar(
        agora=agora,
        intervalo_horas=int(cfg.get("recheck_horas") or RECHECK_HORAS),
        limite=RECHECK_POR_CICLO,
    )
    if not pendentes:
        return

    # O ONM exige token; pega o da própria fonte, que já se reautentica sozinha.
    onm = next((f for f in fontes if f.name == "onm"), None)
    token_onm = getattr(onm, "_token", None) if onm else None

    # O Indeed aceita várias chaves por requisição; as do lote são resolvidas de
    # uma vez só, o que derruba o custo dessa fonte a quase nada.
    chaves_indeed = [i["source_id"] for i in pendentes if i["source"] == "indeed"]
    estados_indeed = vitality.verificar_indeed(chaves_indeed) if chaves_indeed else {}

    encerradas = 0
    for item in pendentes:
        if item["source"] == "indeed":
            estado = estados_indeed.get(item["source_id"], "desconhecida")
        else:
            estado = vitality.verificar(
                item["source"], item["source_id"], token_onm=token_onm,
            )
        if estado == "desconhecida":
            continue  # nem conta como falta: não sabemos de nada

        virou = PUBLICADAS.marcar_checada(item["uid"], agora, estado == "fechada")
        if not virou:
            continue

        # Passou pelas confirmações necessárias: a vaga acabou mesmo.
        encerradas += 1
        message_id = int(item["message_id"])
        if acao == "apagar":
            ok = apagar_mensagem(message_id)
            verbo = "apagada"
        else:
            ok = marcar_mensagem_encerrada(message_id, item.get("html") or item["title"])
            verbo = "marcada como encerrada"
        log.info("Vaga %s encerrada na fonte — mensagem %s (%s)",
                 item["uid"], verbo, "ok" if ok else "falhou")

        STORE.marcar_encerrada(item["uid"], agora.isoformat())

    if encerradas:
        _bump("encerrada", None, n=encerradas)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def check_new_jobs(fontes: list[Any], seen_uids: set[str], seen_keys: set[str],
                   inicializadas: set[str],
                   cfg: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Executa um ciclo. Retorna (uids vistos, chaves vistas, fontes inicializadas)."""
    labels = {f.name: getattr(f, "label", f.name) for f in fontes}
    jobs, fontes_ok = coletar(fontes, cfg)
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

    # Mais antigo → mais recente. A ordem de publicação não depende mais disto
    # (quem ordena é a nota, na fila), mas mantém o log legível.
    novos.sort(key=lambda j: j.published_at or "")

    agora = agora_local()
    min_score = int(cfg.get("min_score") or 0)

    for job in novos:
        # 1) mesma vaga em outra fonte (ou já enviada antes)?
        chave = dedup_key(job)
        if chave in seen_keys:
            log.info("Job %s duplicada (já vista como %s) — ignorando", job.uid, chave[:40])
            _bump("deduped", job.source)
            seen_uids.add(job.uid)
            continue

        # 2) filtros de graça, antes de gastar cota de IA. A ordem é a do custo
        #    crescente e da certeza decrescente.
        if regra(cfg, job.source, "reject_english") and \
                filters.parece_ingles(job.title, job.description):
            log.info("Job %s cortado: anúncio em inglês (pré-filtro)", job.uid)
            _bump("ingles", job.source)
            registrar(job, "ingles")
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        if regra(cfg, job.source, "reject_senior") and filters.parece_senior(job.title):
            log.info("Job %s cortado: título sênior (pré-filtro)", job.uid)
            _bump("senior", job.source)
            registrar(job, "senior")
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        fonte = next((f for f in fontes if f.name == job.source), None)
        if fonte is not None and getattr(fonte, "prefilter_remote", False):
            if not parece_remoto(job):
                _bump("prefiltered", job.source)
                seen_uids.add(job.uid)
                seen_keys.add(chave)
                continue

        # 3) classificador
        analysis = analyze_job(job, cfg)
        category = analysis["category"]
        log.info(
            "Job %s → %s (%s, nota %s) — %s",
            job.uid, category, analysis["work_mode"], analysis["score"],
            analysis["reason"],
        )

        if category == "irrelevant":
            log_skipped_job(job, analysis)
            # Corte por regra dura tem contador próprio: é o que responde
            # "por que o grupo esvaziou?" no relatório.
            motivo = analysis.get("motivo_corte")
            _bump(motivo or "skipped", job.source)
            registrar(job, motivo or "skipped", analysis)
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        if analysis["score"] < min_score:
            log.info("Job %s cortado por nota %s < %s", job.uid, analysis["score"], min_score)
            _bump("skipped", job.source)
            registrar(job, "skipped", analysis)
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        # 4) aprovada — entra na fila e espera a vez
        FILA.push(
            uid=job.uid,
            source=job.source,
            title=job.title,
            html=format_job(job, analysis, labels.get(job.source, "")),
            score=analysis["score"],
            category=category,
            categoria=analysis.get("categoria", ""),
            published_at=job.published_at,
            agora=agora,
        )
        _bump("queued", job.source)
        registrar(job, "queued", analysis)
        seen_uids.add(job.uid)
        seen_keys.add(chave)

    seen_uids |= uids_do_ciclo
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

    STORE.migrar()

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
        log.warning("Filter DISABLED — aprovando TUDO. Motivo(s): %s", "; ".join(reasons))

    STATS.virar_se_preciso(hoje_local())
    seen_uids, seen_keys, inicializadas = load_seen()
    pendentes = {f.name for f in fontes} - inicializadas
    if pendentes:
        log.info("Fontes ainda não inicializadas: %s", ", ".join(sorted(pendentes)))

    cfg = config_atual()
    log.info(
        "Volume: até %d vaga(s)/dia, publicadas entre %dh e %dh, validade da fila %d dias",
        int(cfg.get("daily_limit") or DAILY_LIMIT),
        int(cfg.get("window_start") or SEND_WINDOW_START),
        int(cfg.get("window_end") or SEND_WINDOW_END),
        QUEUE_TTL_DAYS,
    )
    log.info(
        "Regras: inglês=%s · sênior=%s · exige remoto declarado=%s",
        "recusa" if cfg.get("reject_english") else "aceita",
        "recusa" if cfg.get("reject_senior") else "aceita",
        "sim" if cfg.get("require_explicit_remote") else "não",
    )

    # O listener existe só para responder /start e /suporte a quem abrir o bot.
    # Não há mais nenhum comando que mexa no funcionamento — ver bot_control.py.
    CommandListener(
        token=TELEGRAM_TOKEN,
        site_url=SITE_URL,
        instagram_url=INSTAGRAM_URL,
        suporte_telegram=SUPORTE_TELEGRAM,
        chats_legados=CHATS_MENU_LEGADO,
    ).start()

    log.info("Relatório diário às %dh (%s), destino: %s",
             REPORT_HOUR, TIMEZONE_NAME, REPORT_TO)
    log.info("Vaga encerrada na fonte: ação=%s · rechecagem a cada %dh · "
             "acompanhando por %d dias (Indeed não é verificável)",
             ACAO_VAGA_ENCERRADA, RECHECK_HORAS, RECHECK_DIAS)

    while True:
        cfg = config_atual()

        try:
            seen_uids, seen_keys, inicializadas = check_new_jobs(
                fontes, seen_uids, seen_keys, inicializadas, cfg
            )
        except requests.RequestException as exc:
            log.error("Network error: %s — will retry next cycle", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error: %s — will retry next cycle", exc)

        # Publica da fila, se for hora.
        try:
            despachar(cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro no despacho: %s", exc)

        # Revê algumas vagas já publicadas: ainda estão abertas?
        try:
            revisar(fontes, cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro na revisão de vagas: %s", exc)

        # Relatório do dia. O dia é o LOCAL, não o UTC — era essa confusão que
        # fazia o relatório sair com números de uma hora só.
        try:
            agora = agora_local()
            dia = hoje_local()
            if agora.hour >= REPORT_HOUR and STATS.relatorio_pendente(dia):
                enviar_relatorio(fontes, cfg)
                STATS.marcar_relatorio_enviado(dia)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro no relatório diário: %s", exc)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
