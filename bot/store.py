"""Banco de dados — o que liga o bot ao painel.

Duas direções de tráfego:

- **Bot → banco:** cada vaga processada vira uma linha em `job_events`. É desse
  log que o painel tira todo gráfico e todo número.
- **Banco → bot:** o painel escreve em `bot_config`; o bot relê a cada ciclo. É
  assim que ligar/desligar uma fonte ou mudar o teto diário passa a valer sem
  redeploy.

**O banco é opcional em toda parte.** Sem `DATABASE_URL`, ou com o Postgres fora
do ar, cada método vira no-op e o bot segue publicando no Telegram normalmente —
só o painel fica desatualizado. Um bot de vagas que para porque o painel de
métricas caiu seria uma troca ruim.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("av-jobs-bot.store")

# O schema mora em `db/schema.sql`, na raiz do repositório, porque o painel
# precisa exatamente do mesmo. Manter uma cópia aqui e outra lá é garantir que
# um dia as duas divirjam — e a divergência só aparece em produção.
SCHEMA_PATH = Path(
    os.getenv("SCHEMA_FILE") or Path(__file__).resolve().parent.parent / "db" / "schema.sql"
)

try:
    import psycopg
    from psycopg.rows import dict_row
    PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    PSYCOPG_AVAILABLE = False

def _ler_schema() -> str:
    """Carrega db/schema.sql. Devolve "" se o arquivo não veio na imagem."""
    try:
        return SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("Não consegui ler o schema em %s: %s", SCHEMA_PATH, exc)
        return ""

# Quanto tempo a configuração vinda do painel vale antes de ser relida. O ciclo
# do bot é de 10 min; reler a cada ciclo é barato e faz a mudança no painel
# valer rápido, sem transformar o Postgres em dependência de caminho quente.
CONFIG_TTL_SEGUNDOS = 60


class Store:
    """Acesso ao Postgres. Silencioso e tolerante a falha por decisão de projeto."""

    def __init__(self, dsn: str = "") -> None:
        self.dsn = (dsn or os.getenv("DATABASE_URL", "")).strip()
        self._lock = threading.Lock()
        self._conn: Any = None
        self._config: dict[str, Any] = {}
        self._config_lido_em: float = 0.0
        self._avisou_indisponivel = False

    @property
    def habilitado(self) -> bool:
        return bool(self.dsn) and PSYCOPG_AVAILABLE

    # -- conexão ------------------------------------------------------------

    def _conectar(self) -> Any:
        """Conexão preguiçosa, reaberta se tiver caído."""
        if self._conn is not None and not self._conn.closed:
            return self._conn
        self._conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=10)
        return self._conn

    def _executar(self, sql: str, params: tuple = (), *, fetch: str = "") -> Any:
        """Roda uma query. Engole o erro e devolve None — o bot não pode cair aqui."""
        if not self.habilitado:
            return None
        with self._lock:
            for tentativa in (1, 2):  # 2ª tentativa cobre conexão derrubada ociosa
                try:
                    conn = self._conectar()
                    with conn.cursor(row_factory=dict_row) as cur:
                        cur.execute(sql, params)
                        if fetch == "one":
                            return cur.fetchone()
                        if fetch == "all":
                            return cur.fetchall()
                        return True
                except Exception as exc:  # noqa: BLE001
                    if self._conn is not None:
                        try:
                            self._conn.close()
                        except Exception:  # noqa: BLE001
                            pass
                        self._conn = None
                    if tentativa == 2:
                        if not self._avisou_indisponivel:
                            self._avisou_indisponivel = True
                            log.warning(
                                "Postgres indisponível (%s) — o bot segue normal, "
                                "só o painel fica sem dados novos", exc,
                            )
                        return None
            return None

    def migrar(self) -> bool:
        """Cria as tabelas se não existirem. Idempotente."""
        if not self.habilitado:
            if self.dsn and not PSYCOPG_AVAILABLE:
                log.warning("DATABASE_URL configurado mas psycopg não instalado — "
                            "painel não vai receber dados")
            return False
        schema = _ler_schema()
        if not schema.strip():
            return False
        ok = self._executar(schema) is not None
        if ok:
            self._avisou_indisponivel = False
            log.info("Postgres conectado e schema aplicado")
        return ok

    # -- bot → banco --------------------------------------------------------

    def registrar_evento(self, *, uid: str, source: str, status: str, local_day: str,
                         title: str = "", company: str = "", url: str = "",
                         category: str = "", work_mode: str = "", role_type: str = "",
                         salary: str = "", score: int = 0, language: str = "",
                         seniority: str = "", reason: str = "",
                         published_at: str = "", categoria: str = "",
                         telegram_message_id: int | None = None) -> None:
        """Anota o destino de uma vaga. Repetir o mesmo (uid, status) não duplica."""
        self._executar(
            """
            INSERT INTO job_events (
                uid, source, title, company, url, status, category, work_mode,
                role_type, salary, score, language, seniority, reason,
                published_at, local_day, categoria, telegram_message_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (uid, status) DO UPDATE SET
                score = EXCLUDED.score,
                reason = EXCLUDED.reason,
                categoria = EXCLUDED.categoria,
                -- COALESCE: um reenvio sem id não pode apagar o id que já
                -- estava lá e é o que dá o link da mensagem.
                telegram_message_id = COALESCE(EXCLUDED.telegram_message_id,
                                               job_events.telegram_message_id),
                created_at = now()
            """,
            (uid, source, title[:300], company[:200], url[:600], status,
             category, work_mode, role_type[:120], salary[:120], int(score),
             language, seniority, reason[:400], published_at[:60], local_day,
             categoria, telegram_message_id),
        )

    def marcar_encerrada(self, uid: str, quando_iso: str) -> None:
        """Anota que a vaga saiu do ar. É o que pinta a bolinha vermelha."""
        self._executar(
            "UPDATE job_events SET closed_at = %s::timestamptz "
            "WHERE uid = %s AND status = %s AND closed_at IS NULL",
            (quando_iso, uid, "sent"),
        )

    # -- banco → bot --------------------------------------------------------

    def config(self, *, forcar: bool = False) -> dict[str, Any]:
        """Configuração escrita pelo painel, em cache curto.

        Devolve `{}` quando não há banco — quem chama trata isso como "usa o que
        veio do ambiente".
        """
        if not self.habilitado:
            return {}
        agora = time.monotonic()
        if not forcar and (agora - self._config_lido_em) < CONFIG_TTL_SEGUNDOS:
            return self._config
        linha = self._executar("SELECT data FROM bot_config WHERE id = 1", fetch="one")
        self._config_lido_em = agora
        if linha and isinstance(linha.get("data"), dict):
            self._config = linha["data"]
        elif linha and isinstance(linha.get("data"), str):
            try:
                self._config = json.loads(linha["data"])
            except (json.JSONDecodeError, ValueError):
                pass
        return self._config
