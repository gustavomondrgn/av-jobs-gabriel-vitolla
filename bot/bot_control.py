"""O que o bot responde no privado.

Até 11/08 aqui morava um painel de administração por comando: `/pausar`,
`/retomar`, `/status`, `/relatorio`. Isso foi **removido por inteiro** em 12/08,
a pedido do Gabriel, e a razão é de segurança e não de estética.

Qualquer pessoa consegue abrir conversa com um bot do Telegram — isso é da
plataforma e não tem como impedir. Enquanto existisse um `/pausar`, existia
também uma superfície de ataque protegida só por uma lista de IDs numa variável
de ambiente: bastava um erro de configuração, um redeploy sem a variável, ou o
vazamento de um ID, para um aluno conseguir desligar as vagas do grupo. Não vale
o risco para uma comodidade que agora tem lugar melhor.

O controle do bot passou a ser o painel em `admin.encontreseuav.com.br`, atrás
de login e senha. Aqui sobrou o que é seguro por construção: **texto**. O bot
responde a quem abrir a conversa dizendo o que ele é e para onde ir, e ignora
todo o resto. Não existe mensagem, de ninguém, que mude o comportamento do bot.
"""

from __future__ import annotations

import html
import logging
import threading
import time

import requests

log = logging.getLogger("av-jobs-bot.control")

API = "https://api.telegram.org/bot{token}/{metodo}"
POLL_TIMEOUT = 30          # long polling: o Telegram segura a resposta
HTTP_TIMEOUT = POLL_TIMEOUT + 15
ERRO_BACKOFF = 5.0


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


def limpar_menu(token: str, chats_legados: set[int] | None = None) -> None:
    """Apaga a lista de comandos em todos os escopos.

    O Telegram guarda os comandos registrados no servidor dele: se o bot já
    publicou um menu com `/pausar`, ele continua aparecendo no autocomplete
    mesmo depois de o código parar de tratá-lo. O usuário veria comandos que não
    existem mais e concluiria que o bot está quebrado. Limpar é obrigatório, não
    é cosmético.

    **Os escopos por chat também precisam ser apagados, um a um.** A versão
    anterior registrava o menu de administração com escopo
    `{"type": "chat", "chat_id": <admin>}`, e apagar os escopos genéricos não
    toca nesses. Foi o que aconteceu no deploy de 12/08: os chats privados já
    mostravam só `/start` e `/suporte`, mas os dois admins continuavam vendo o
    menu antigo inteiro. Quem eram esses chats só o histórico sabe — daí a
    lista vir do ambiente.
    """
    for escopo in ({"type": "default"},
                   {"type": "all_private_chats"},
                   {"type": "all_group_chats"},
                   {"type": "all_chat_administrators"}):
        _post(token, "deleteMyCommands", {"scope": escopo})

    for chat_id in sorted(chats_legados or ()):
        _post(token, "deleteMyCommands",
              {"scope": {"type": "chat", "chat_id": chat_id}})
        log.info("Menu antigo removido do chat %s", chat_id)
    # Republica só o que sobrou, para o botão "Menu" não ficar vazio e estranho.
    _post(token, "setMyCommands", {
        "commands": [
            {"command": "start", "description": "O que é este bot"},
            {"command": "suporte", "description": "Falar com o Gabriel"},
        ],
        "scope": {"type": "all_private_chats"},
    })
    log.info("Menu de comandos administrativos removido do Telegram")


class CommandListener:
    """Responde à apresentação no privado. Não executa nada.

    Continua sendo uma thread com long polling por um motivo simples: o bot
    precisa consumir os updates de qualquer jeito. Se ninguém chamar
    `getUpdates`, o Telegram acumula as mensagens e as reentrega por 24h — e o
    dia em que alguém quiser ligar um webhook, a fila estaria cheia de lixo.
    """

    def __init__(self, token: str, site_url: str = "", instagram_url: str = "",
                 suporte_telegram: str = "",
                 chats_legados: set[int] | None = None) -> None:
        self.token = token
        self.site_url = site_url
        self.instagram_url = instagram_url
        self.suporte_telegram = suporte_telegram
        # Chats que um dia tiveram menu de administração registrado.
        self.chats_legados = chats_legados or set()
        self._parar = threading.Event()
        self._offset = 0

    # -- ciclo de vida ------------------------------------------------------

    def start(self) -> threading.Thread:
        # daemon: se o laço principal morrer, o processo não fica preso aqui.
        t = threading.Thread(target=self._run, name="telegram-listener", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._parar.set()

    # -- textos -------------------------------------------------------------

    def apresentacao(self) -> str:
        linhas = [
            "👋 Olá! Eu sou o <b>AV Jobs</b>, o robô de vagas do "
            "<b>Gabriel Vitolla</b>.",
            "",
            "Eu vasculho o dia inteiro os principais portais de emprego do Brasil "
            "e publico no grupo apenas as vagas <b>100% remotas</b> que combinam "
            "com quem trabalha como Assistente Virtual.",
            "",
            "Cada vaga passa por uma triagem antes de ser publicada — você recebe "
            "poucas oportunidades por dia, mas só as que valem a sua candidatura.",
        ]

        links = []
        if self.site_url:
            links.append(f'🌐 <a href="{html.escape(self.site_url)}">Site do Gabriel</a>')
        if self.instagram_url:
            links.append(f'📸 <a href="{html.escape(self.instagram_url)}">Instagram</a>')
        if links:
            linhas.extend(["", *links])

        linhas.extend(["", self.texto_suporte()])
        return "\n".join(linhas)

    def texto_suporte(self) -> str:
        if self.suporte_telegram:
            alvo = self.suporte_telegram.lstrip("@")
            return (f'💬 Precisa de ajuda? Fale com o Gabriel: '
                    f'<a href="https://t.me/{html.escape(alvo)}">@{html.escape(alvo)}</a>')
        return "💬 Precisa de ajuda? Fale diretamente com o Gabriel."

    # -- laço ---------------------------------------------------------------

    def _descartar_backlog(self) -> None:
        """Pula o que chegou enquanto o bot estava fora do ar."""
        resp = _post(self.token, "getUpdates", {"offset": -1, "timeout": 0})
        if not resp or not resp.get("ok"):
            return
        resultados = resp.get("result") or []
        if resultados:
            self._offset = resultados[-1]["update_id"] + 1

    def _run(self) -> None:
        limpar_menu(self.token, self.chats_legados)
        self._descartar_backlog()
        log.info("Listener ativo (só apresentação — nenhum comando com efeito)")

        while not self._parar.is_set():
            resp = _post(self.token, "getUpdates", {
                "offset": self._offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message"],
            })
            if resp is None or not resp.get("ok"):
                time.sleep(ERRO_BACKOFF)
                continue

            for update in resp.get("result") or []:
                self._offset = int(update["update_id"]) + 1
                try:
                    self._tratar(update)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Erro tratando mensagem: %s", exc)

    # -- tratamento ---------------------------------------------------------

    def _tratar(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return

        chat = msg.get("chat") or {}
        # Só no privado. No grupo o bot publica vagas e mais nada — responder lá
        # encheria o feed que é o produto.
        if chat.get("type") != "private":
            return

        texto = (msg.get("text") or "").strip()
        comando = texto.split()[0].lstrip("/").split("@")[0].lower() if texto else ""

        if comando in ("suporte", "ajuda", "help", "contato"):
            responder(self.token, chat["id"], self.texto_suporte())
            return

        # Qualquer outra coisa — inclusive /start, texto solto, ou um comando
        # antigo que a pessoa decorou — recebe a apresentação. Nada de "comando
        # desconhecido": não existe comando algum a descobrir.
        responder(self.token, chat["id"], self.apresentacao())
