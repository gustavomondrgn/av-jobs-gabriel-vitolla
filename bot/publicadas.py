"""Registro do que já foi publicado no grupo, para poder revisitar depois.

Guarda o mínimo necessário para responder "esta vaga ainda está aberta?" e, se
não estiver, alcançar a mensagem correspondente no Telegram: o id na fonte e o
`message_id`.

Fica em arquivo, no volume, e não no Postgres, pelo mesmo motivo do resto do
bot: o banco é opcional: o Telegram é o produto. Se o Postgres cair, o revisor
tem de continuar apagando vaga encerrada normalmente.

Duas decisões que evitam apagar vaga boa por engano:

- **Só um 404 explícito conta**, e são necessários DOIS seguidos, em checagens
  diferentes, antes de a vaga ser dada como encerrada. Uma instabilidade
  momentânea da plataforma não derruba nada.
- **Cada vaga é checada no máximo uma vez por intervalo**, e o registro é podado
  depois de algumas semanas — vaga de mês passado não vale requisição.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("av-jobs-bot.publicadas")

# Quantos 404 seguidos são necessários para dar a vaga como encerrada.
CONFIRMACOES = 2


class RegistroPublicadas:
    def __init__(self, path: Path, dias_de_vida: int = 30) -> None:
        self.path = path
        self.dias_de_vida = dias_de_vida
        self._lock = threading.Lock()
        self._itens: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistência -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                self._itens = json.load(f) or {}
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("Falha lendo %s: %s — começando vazio", self.path, exc)
            self._itens = {}
        if self._itens:
            log.info("Registro de publicadas: %d vaga(s) sob acompanhamento",
                     len(self._itens))

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._itens, f, ensure_ascii=False)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Falha salvando %s: %s", self.path, exc)

    # -- escrita ------------------------------------------------------------

    def registrar(self, *, uid: str, source: str, source_id: str, title: str,
                  message_id: int | None, agora: datetime,
                  html: str = "") -> None:
        if message_id is None:
            # Sem o id da mensagem não há o que apagar depois; acompanhar não
            # serviria para nada.
            return
        with self._lock:
            self._itens[uid] = {
                "source": source,
                "source_id": source_id,
                "title": title,
                # Guardado para poder riscar a mensagem original no lugar de
                # apagá-la, se for essa a ação configurada.
                "html": html[:4000],
                "message_id": message_id,
                "publicada_em": agora.isoformat(),
                "checada_em": None,
                "faltas": 0,       # 404 seguidos
                "encerrada": False,
            }
            self._save_locked()

    def marcar_checada(self, uid: str, agora: datetime, achou_404: bool) -> bool:
        """Anota o resultado. Devolve True quando a vaga passa a ser encerrada."""
        with self._lock:
            item = self._itens.get(uid)
            if not item:
                return False
            item["checada_em"] = agora.isoformat()
            if achou_404:
                item["faltas"] = int(item.get("faltas") or 0) + 1
            else:
                # Voltou a responder: zera. Pode ter sido instabilidade.
                item["faltas"] = 0
            virou = bool(item["faltas"] >= CONFIRMACOES and not item["encerrada"])
            if virou:
                item["encerrada"] = True
            self._save_locked()
            return virou

    def esquecer(self, uid: str) -> None:
        with self._lock:
            self._itens.pop(uid, None)
            self._save_locked()

    # -- leitura ------------------------------------------------------------

    def a_checar(self, *, agora: datetime, intervalo_horas: int,
                 limite: int) -> list[dict[str, Any]]:
        """As próximas vagas a reexaminar, mais antigas primeiro."""
        with self._lock:
            self._podar_locked(agora)
            corte = agora - timedelta(hours=intervalo_horas)
            pendentes = []
            for uid, item in self._itens.items():
                if item.get("encerrada"):
                    continue
                quando = item.get("checada_em")
                if quando:
                    try:
                        if datetime.fromisoformat(quando) > corte:
                            continue
                    except ValueError:
                        pass
                pendentes.append({"uid": uid, **item})
            # Quem está há mais tempo sem checagem vai primeiro.
            pendentes.sort(key=lambda i: i.get("checada_em") or "")
            return pendentes[:limite]

    def _podar_locked(self, agora: datetime) -> None:
        limite = agora - timedelta(days=self.dias_de_vida)
        antes = len(self._itens)
        self._itens = {
            uid: item for uid, item in self._itens.items()
            if _quando(item.get("publicada_em")) is None
            or _quando(item.get("publicada_em")) > limite  # type: ignore[operator]
        }
        if len(self._itens) != antes:
            self._save_locked()

    def resumo(self) -> dict[str, int]:
        with self._lock:
            return {
                "acompanhadas": len(self._itens),
                "encerradas": sum(1 for i in self._itens.values() if i.get("encerrada")),
            }


def _quando(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None
