"""Comandos do bot pelo Telegram.

O bot passa a **ouvir** além de falar. Dois cuidados que definem o desenho:

1. **Só no privado.** Comando dado no grupo apareceria para todos os alunos —
   dava pra ver que o Gabriel pausou alguma coisa. Mensagem que não venha de um
   chat privado é ignorada sem resposta.
2. **Só de quem está autorizado.** `TELEGRAM_ADMIN_IDS` lista os user IDs que
   podem mandar comando. Sem ela, o listener nem sobe.

O listener roda numa thread separada com long polling. Se ficasse no laço
principal, um `/status` esperaria até o fim do `CHECK_INTERVAL` para responder.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

import requests

log = logging.getLogger("av-jobs-bot.control")

API = "https://api.telegram.org/bot{token}/{metodo}"
POLL_TIMEOUT = 30          # long polling: o Telegram segura a resposta
HTTP_TIMEOUT = POLL_TIMEOUT + 15
ERRO_BACKOFF = 5.0

Handler = Callable[[list[str]], str]

# Comandos que qualquer um pode usar, mesmo sem estar autorizado. Só o /id, e de
# propósito: ele devolve à pessoa o ID dela mesma, que não dá acesso a nada e é
# o que destrava o cadastro de um admin novo sem precisar ler log de servidor.
COMANDOS_PUBLICOS = {"id", "meuid", "meu_id"}


# ---------------------------------------------------------------------------
# Estado compartilhado entre a thread de comandos e o laço principal
# ---------------------------------------------------------------------------

class ControlState:
    """Quais fontes estão pausadas, persistido no volume.

    Sem persistir, qualquer redeploy religaria sozinho o que o Gabriel pausou —
    e ele não teria como saber.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._paused: set[str] = set()
        self._offset: int = 0
        self._last_report_day: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("Falha lendo %s: %s — começando sem pausas", self.path, exc)
            return
        self._paused = {str(x) for x in (data.get("paused") or [])}
        self._offset = int(data.get("update_offset") or 0)
        self._last_report_day = str(data.get("last_report_day") or "")
        if self._paused:
            log.info("Fontes pausadas (do estado salvo): %s", ", ".join(sorted(self._paused)))

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({
                    "paused": sorted(self._paused),
                    "update_offset": self._offset,
                    "last_report_day": self._last_report_day,
                }, f)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Falha salvando %s: %s", self.path, exc)

    def is_paused(self, nome: str) -> bool:
        with self._lock:
            return nome in self._paused

    def paused(self) -> set[str]:
        with self._lock:
            return set(self._paused)

    def pause(self, nome: str) -> bool:
        """Pausa a fonte. Retorna True se mudou alguma coisa."""
        with self._lock:
            if nome in self._paused:
                return False
            self._paused.add(nome)
            self._save_locked()
            return True

    def resume(self, nome: str) -> bool:
        with self._lock:
            if nome not in self._paused:
                return False
            self._paused.discard(nome)
            self._save_locked()
            return True

    def ja_relatou(self, dia: str) -> bool:
        """O relatório desse dia já saiu? Persistido para o restart não repetir."""
        with self._lock:
            return self._last_report_day == dia

    def marcar_relatado(self, dia: str) -> None:
        with self._lock:
            self._last_report_day = dia
            self._save_locked()

    @property
    def offset(self) -> int:
        with self._lock:
            return self._offset

    def set_offset(self, valor: int) -> None:
        with self._lock:
            if valor > self._offset:
                self._offset = valor
                self._save_locked()


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _post(token: str, metodo: str, payload: dict) -> dict | None:
    try:
        resp = requests.post(
            API.format(token=token, metodo=metodo),
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.warning("Telegram %s falhou: %s", metodo, exc)
        return None
    if not resp.ok:
        log.warning("Telegram %s devolveu %s: %s", metodo, resp.status_code, resp.text[:200])
        return None
    return resp.json()


def responder(token: str, chat_id: int, texto: str) -> None:
    _post(token, "sendMessage", {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


# Lista mostrada no menu do Telegram (botão "Menu" e autocomplete do "/").
# Sem registrar isto, os comandos funcionam mas ficam invisíveis — o usuário
# precisaria decorar. Nome só com [a-z0-9_], então nada de acento.
COMANDOS_MENU = [
    ("status", "Fontes, próxima checagem e números do dia"),
    ("fontes", "Lista as fontes e o estado de cada uma"),
    ("pausar", "Para de buscar numa fonte (ou 'tudo')"),
    ("retomar", "Volta a buscar numa fonte (ou 'tudo')"),
    ("relatorio", "Relatório do dia agora"),
    ("id", "Mostra seu ID do Telegram"),
    ("ajuda", "Lista os comandos"),
]


def registrar_menu(token: str, admin_ids: set[int]) -> bool:
    """Publica a lista de comandos **só para os admins**.

    Qualquer pessoa consegue abrir conversa com um bot do Telegram — isso é da
    plataforma e não tem como impedir. O que dá para evitar é que ela **veja** um
    menu de administração ao abrir: o menu é registrado por escopo, um por chat
    de admin, e o escopo padrão fica vazio. Quem não é admin abre o bot e não vê
    comando nenhum.

    Isso é cosmético, não é a segurança: quem manda comando sem estar em
    TELEGRAM_ADMIN_IDS é ignorado de qualquer jeito. Mas evita a impressão de
    painel exposto e o convite a ficar cutucando.
    """
    lista = [{"command": c, "description": d} for c, d in COMANDOS_MENU]

    # 1) Escopo padrão: nada. Se ainda não há admin, deixa só o /id para o
    #    cadastro do primeiro conseguir descobrir o próprio ID.
    if admin_ids:
        padrao: list[dict] = []
    else:
        padrao = [{"command": "id", "description": "Mostra seu ID do Telegram"}]
    _post(token, "setMyCommands", {"commands": padrao, "scope": {"type": "default"}})

    # 2) Menu completo, um escopo por conversa de admin.
    ok = True
    for uid in sorted(admin_ids):
        resp = _post(token, "setMyCommands", {
            "commands": lista,
            "scope": {"type": "chat", "chat_id": uid},
        })
        if not (resp and resp.get("ok")):
            ok = False
            log.warning("Não consegui registrar o menu para o admin %s: %s", uid, str(resp)[:150])

    if ok:
        log.info("Menu de comandos registrado para %d admin(s); escondido dos demais",
                 len(admin_ids))
    return ok


def parse_admin_ids(bruto: str) -> set[int]:
    ids: set[int] = set()
    for pedaco in (bruto or "").replace(";", ",").split(","):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        try:
            ids.add(int(pedaco))
        except ValueError:
            log.warning("TELEGRAM_ADMIN_IDS: %r não é um ID numérico — ignorando", pedaco)
    return ids


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

class CommandListener:
    """Escuta comandos no privado e despacha para os handlers."""

    def __init__(self, token: str, admin_ids: set[int], state: ControlState,
                 handlers: dict[str, Handler]) -> None:
        self.token = token
        self.admin_ids = admin_ids
        self.state = state
        self.handlers = handlers
        self._parar = threading.Event()

    # -- ciclo de vida ------------------------------------------------------

    def start(self) -> threading.Thread:
        # daemon: se o laço principal morrer, o processo não fica preso aqui.
        t = threading.Thread(target=self._run, name="telegram-commands", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._parar.set()

    # -- laço ---------------------------------------------------------------

    def _descartar_backlog(self) -> None:
        """Na primeira vez, pula comandos antigos.

        Se o bot ficou fora do ar, o Telegram guardou o que mandaram. Executar
        isso tudo de uma vez ao subir seria surpresa ruim — pausaria fontes por
        causa de um comando de ontem.
        """
        resp = _post(self.token, "getUpdates", {"offset": -1, "timeout": 0})
        if not resp or not resp.get("ok"):
            return
        resultados = resp.get("result") or []
        if resultados:
            ultimo = resultados[-1]["update_id"]
            self.state.set_offset(ultimo + 1)
            log.info("Comandos anteriores ao boot descartados (offset=%d)", ultimo + 1)

    def _run(self) -> None:
        registrar_menu(self.token, self.admin_ids)
        if self.state.offset == 0:
            self._descartar_backlog()

        log.info(
            "Escutando comandos no privado (autorizados: %s)",
            ", ".join(str(i) for i in sorted(self.admin_ids)) or "ninguém ainda — use /id",
        )

        while not self._parar.is_set():
            resp = _post(self.token, "getUpdates", {
                "offset": self.state.offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message"],
            })
            if resp is None:
                time.sleep(ERRO_BACKOFF)
                continue
            if not resp.get("ok"):
                # 409 acontece se houver outra instância consumindo os updates.
                log.warning("getUpdates não-ok: %s", str(resp)[:200])
                time.sleep(ERRO_BACKOFF)
                continue

            for update in resp.get("result") or []:
                self.state.set_offset(int(update["update_id"]) + 1)
                try:
                    self._tratar(update)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Erro tratando comando: %s", exc)

    # -- tratamento ---------------------------------------------------------

    def _tratar(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return

        chat = msg.get("chat") or {}
        # Só no privado: no grupo o comando ficaria à vista de todos os alunos.
        if chat.get("type") != "private":
            return

        texto = (msg.get("text") or "").strip()
        if not texto.startswith("/"):
            return

        autor = msg.get("from") or {}
        user_id = autor.get("id")

        partes = texto.split()
        # "/pausar@meubot" vira "pausar"
        comando = partes[0].lstrip("/").split("@")[0].lower()
        args = partes[1:]

        if comando in COMANDOS_PUBLICOS:
            autorizado = user_id in self.admin_ids
            marca = "já autorizado ✅" if autorizado else "ainda não autorizado"
            responder(self.token, chat["id"], (
                f"Seu ID do Telegram é <code>{user_id}</code> ({marca}).\n\n"
                f"Para virar admin do bot, esse número precisa entrar na variável "
                f"<code>TELEGRAM_ADMIN_IDS</code>."
            ))
            log.info("/%s respondido para user_id=%s (%s)", comando, user_id,
                     autor.get("username") or autor.get("first_name"))
            return

        if user_id not in self.admin_ids:
            # O log continua sendo a rede de segurança se o /id não for usado.
            log.warning(
                "Comando %r recusado: user_id=%s (%s) não está em TELEGRAM_ADMIN_IDS",
                texto[:40], user_id, autor.get("username") or autor.get("first_name"),
            )
            return

        handler = self.handlers.get(comando)
        if handler is None:
            responder(self.token, chat["id"],
                      f"Comando desconhecido: <code>/{comando}</code>\n"
                      f"Use /ajuda para ver a lista.")
            return

        log.info("Comando /%s %s (de %s)", comando, " ".join(args), user_id)
        try:
            resposta = handler(args)
        except Exception as exc:  # noqa: BLE001
            log.exception("Handler de /%s quebrou: %s", comando, exc)
            resposta = f"Deu erro executando /{comando}: {type(exc).__name__}"
        if resposta:
            responder(self.token, chat["id"], resposta)
