"""Fila de envio: separa "aprovar uma vaga" de "publicar uma vaga".

Antes, aprovar era publicar — a vaga saía no grupo no instante em que passava no
filtro. Isso dava três problemas de uma vez: vaga caindo às 4h da manhã, rajada
de dez mensagens seguidas quando a Gupy roda, e volume diário incontrolável.

Agora a vaga aprovada entra numa fila com nota. Um despachante publica, no
máximo, `limite_diario` por dia, espaçadas ao longo da janela de horário, sempre
pegando a de maior nota que está esperando. O excedente **não se perde**: fica
na fila e concorre no dia seguinte, até vencer a validade.

Efeito colateral desejado: como só as melhores saem, a qualidade média do grupo
sobe quando o volume cai. É o oposto de simplesmente cortar o filtro.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("av-jobs-bot.dispatch")

# Piso entre dois envios. Protege contra o caso em que a janela está quase
# acabando e ainda há cota sobrando: sem isso o bot despejaria o resto de uma vez
# às 22h50, que é exatamente a rajada que a fila existe para evitar.
GAP_MINIMO_SEGUNDOS = 300


class SendQueue:
    """Fila persistida de vagas aprovadas esperando a vez de ir ao grupo.

    Persiste no volume: um redeploy no meio da tarde não pode reabrir a cota do
    dia (o grupo receberia o dobro) nem descartar o que estava esperando.
    """

    def __init__(self, path: Path, validade_dias: int = 3,
                 tamanho_maximo: int = 300) -> None:
        self.path = path
        self.validade_dias = validade_dias
        self.tamanho_maximo = tamanho_maximo
        self._lock = threading.Lock()
        self._itens: list[dict[str, Any]] = []
        self._dia: str = ""
        self._enviadas_hoje: int = 0
        self._ultimo_envio: str = ""
        self._load()

    # -- persistência -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                dados = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("Falha lendo %s: %s — começando com fila vazia", self.path, exc)
            return
        self._itens = list(dados.get("itens") or [])
        self._dia = str(dados.get("dia") or "")
        self._enviadas_hoje = int(dados.get("enviadas_hoje") or 0)
        self._ultimo_envio = str(dados.get("ultimo_envio") or "")
        if self._itens:
            log.info("Fila de envio carregada: %d vaga(s) esperando", len(self._itens))

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({
                    "itens": self._itens,
                    "dia": self._dia,
                    "enviadas_hoje": self._enviadas_hoje,
                    "ultimo_envio": self._ultimo_envio,
                }, f, ensure_ascii=False)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Falha salvando %s: %s", self.path, exc)

    # -- entrada ------------------------------------------------------------

    def push(self, *, uid: str, source: str, title: str, html: str,
             score: int, category: str, published_at: str, agora: datetime,
             categoria: str = "") -> None:
        """Coloca uma vaga aprovada na fila.

        O HTML já vem renderizado. Renderizar agora e não na hora do envio deixa
        a mensagem coerente com a vaga como ela era quando foi analisada, e evita
        ter que carregar o `Job` inteiro na fila.
        """
        with self._lock:
            if any(i.get("uid") == uid for i in self._itens):
                return
            self._itens.append({
                "uid": uid,
                "source": source,
                "title": title,
                "html": html,
                "score": int(score),
                "category": category,
                "categoria": categoria,
                "published_at": published_at,
                "enfileirada_em": agora.isoformat(),
            })
            self._podar_locked(agora)
            self._save_locked()

    def _podar_locked(self, agora: datetime) -> list[dict[str, Any]]:
        """Tira o que venceu e o que não cabe. Devolve o que saiu."""
        limite = agora - timedelta(days=self.validade_dias)
        vencidas: list[dict[str, Any]] = []
        vivas: list[dict[str, Any]] = []
        for item in self._itens:
            try:
                quando = datetime.fromisoformat(item["enfileirada_em"])
            except (KeyError, ValueError):
                vivas.append(item)
                continue
            (vencidas if quando < limite else vivas).append(item)

        # Estouro de tamanho: sai a de menor nota. Uma fila que só cresce é fila
        # que nunca vai ser esvaziada — melhor assumir a perda com critério.
        if len(vivas) > self.tamanho_maximo:
            vivas.sort(key=self._chave_prioridade, reverse=True)
            excedente = vivas[self.tamanho_maximo:]
            vivas = vivas[:self.tamanho_maximo]
            vencidas.extend(excedente)

        if vencidas:
            log.info("Fila: %d vaga(s) descartadas (validade de %d dias ou fila cheia)",
                     len(vencidas), self.validade_dias)
        self._itens = vivas
        return vencidas

    # -- saída --------------------------------------------------------------

    @staticmethod
    def _chave_prioridade(item: dict[str, Any]) -> tuple:
        """Maior nota primeiro; empate desempata pela vaga mais recente.

        Publicação recente ganha do empate porque vaga velha tem mais chance de
        já estar preenchida quando o aluno se candidatar.
        """
        return (int(item.get("score") or 0), str(item.get("published_at") or ""))

    def _virar_o_dia_locked(self, hoje: str) -> None:
        if self._dia != hoje:
            self._dia = hoje
            self._enviadas_hoje = 0

    def proxima(self, *, agora: datetime, hoje: str, limite_diario: int,
                janela_inicio: int, janela_fim: int) -> dict[str, Any] | None:
        """A vaga que deve ir ao grupo agora, ou None se não é hora.

        Devolve no máximo uma por chamada: o laço principal roda a cada
        `CHECK_INTERVAL`, então uma por vez já produz o gotejamento.
        """
        with self._lock:
            self._virar_o_dia_locked(hoje)
            self._podar_locked(agora)

            if not self._itens:
                self._save_locked()
                return None

            if not (janela_inicio <= agora.hour < janela_fim):
                self._save_locked()
                return None

            restantes = limite_diario - self._enviadas_hoje
            if restantes <= 0:
                self._save_locked()
                return None

            if self._ultimo_envio:
                try:
                    desde = (agora - datetime.fromisoformat(self._ultimo_envio)).total_seconds()
                except ValueError:
                    desde = float("inf")
                if desde < self._gap(agora, restantes, janela_fim):
                    self._save_locked()
                    return None

            self._itens.sort(key=self._chave_prioridade, reverse=True)
            item = self._itens.pop(0)
            self._save_locked()
            return item

    @staticmethod
    def _gap(agora: datetime, restantes: int, janela_fim: int) -> float:
        """Espaçamento até o próximo envio, recalculado a cada vez.

        Divide o que sobra da janela pelo que sobra da cota. Distribui sozinho:
        se o dia começou devagar, o intervalo aperta no fim da tarde; se a fila
        secou e voltou a encher, ele reabre. Nada de agenda fixa.
        """
        fim = agora.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=janela_fim)
        faltam = (fim - agora).total_seconds()
        if faltam <= 0 or restantes <= 0:
            return float("inf")
        return max(GAP_MINIMO_SEGUNDOS, faltam / restantes)

    def confirmar_envio(self, agora: datetime, hoje: str) -> None:
        """Registra que a vaga foi mesmo publicada. Só depois do Telegram aceitar."""
        with self._lock:
            self._virar_o_dia_locked(hoje)
            self._enviadas_hoje += 1
            self._ultimo_envio = agora.isoformat()
            self._save_locked()

    def devolver(self, item: dict[str, Any]) -> None:
        """Recoloca na fila uma vaga cujo envio falhou por motivo passageiro."""
        with self._lock:
            if not any(i.get("uid") == item.get("uid") for i in self._itens):
                self._itens.append(item)
                self._save_locked()

    # -- leitura ------------------------------------------------------------

    def resumo(self, hoje: str) -> dict[str, Any]:
        with self._lock:
            self._virar_o_dia_locked(hoje)
            notas = sorted((int(i.get("score") or 0) for i in self._itens), reverse=True)
            return {
                "na_fila": len(self._itens),
                "enviadas_hoje": self._enviadas_hoje,
                "melhores_notas": notas[:5],
                "ultimo_envio": self._ultimo_envio,
            }
