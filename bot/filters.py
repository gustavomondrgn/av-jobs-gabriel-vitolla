"""Filtros determinísticos, aplicados ANTES do classificador.

São de graça e não gastam cota de IA. Cada um responde uma pergunta que não
precisa de modelo de linguagem: o anúncio está escrito em inglês? o título diz
"sênior"? o texto fala em trabalho remoto?

Todos erram para o lado de **deixar passar**. Quando o sinal é fraco eles
devolvem "não sei" e a decisão fica com o classificador, que lê a vaga inteira.
Filtro barato que descarta por engano custa uma vaga perdida e ninguém fica
sabendo; filtro barato que deixa passar custa uma chamada de IA.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Idioma
# ---------------------------------------------------------------------------

# Palavras funcionais — aparecem em qualquer texto do idioma, independente do
# assunto. Nomes de tecnologia e cargos em inglês ("Customer Success", "home
# office", "SDR") são comuns em vaga brasileira e por isso ficam de fora das
# duas listas: eles não distinguem nada.
_PT_STOPWORDS = frozenset("""
    de da do das dos e em no na nos nas um uma uns umas para por com sem sobre
    que qual quais quando onde como porque se nao sim ja mais menos muito pouco
    ser estar ter haver fazer voce vocequer nos eles elas seu sua seus suas
    este esta esse essa aquele aquela isso aquilo entre ate apos antes tambem
    atraves sera serao deve devera precisa precisamos buscamos procuramos
    oferecemos requisitos atividades responsabilidades beneficios salario vaga
    empresa trabalho horario experiencia conhecimento desejavel obrigatorio
""".split())

_EN_STOPWORDS = frozenset("""
    the and of to in for with without about that which when where how because
    if not yes already more less very few be being been is are was were has
    have had do does did you your yours we our ours they them their this these
    those it its from into over under after before through will would shall
    should must can could may might there here what who whom whose
    requirements responsibilities benefits salary company position role
    candidate applicant looking seeking offer join team
""".split())

# Abaixo disso o texto é curto demais para uma contagem significar alguma coisa.
_MIN_TOKENS_IDIOMA = 25
# O inglês precisa ganhar com folga. Vaga brasileira usa muito termo em inglês;
# empatar ou ganhar por pouco não é evidência suficiente para descartar.
_RAZAO_INGLES = 2.5

_TOKEN_RE = re.compile(r"[a-z]+")


def _sem_acento(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def detectar_idioma(texto: str) -> str:
    """Devolve "pt", "en" ou "?" (indeciso — deixa o classificador resolver).

    Conta palavras funcionais de cada idioma. É grosseiro de propósito: o alvo
    é o anúncio inteiro escrito em inglês, não a vaga brasileira que salpica
    "home office" e "Customer Success" no meio do português.
    """
    tokens = _TOKEN_RE.findall(_sem_acento(texto))
    if len(tokens) < _MIN_TOKENS_IDIOMA:
        return "?"

    pt = sum(1 for t in tokens if t in _PT_STOPWORDS)
    en = sum(1 for t in tokens if t in _EN_STOPWORDS)

    # Nenhuma das duas listas pegou nada: texto atípico, não arrisca.
    if pt + en < 5:
        return "?"
    if en >= max(4, pt * _RAZAO_INGLES):
        return "en"
    if pt > en:
        return "pt"
    return "?"


def parece_ingles(titulo: str, descricao: str) -> bool:
    """A vaga está ESCRITA em inglês?

    Julga pela descrição, que é o texto longo. O título sozinho não decide: é
    normal a vaga brasileira ter título em inglês ("Customer Success Analyst")
    e corpo inteiro em português.
    """
    idioma_desc = detectar_idioma(descricao)
    if idioma_desc != "?":
        return idioma_desc == "en"
    # Sem descrição utilizável, o título é tudo que existe — e aí ele decide.
    return detectar_idioma(titulo) == "en"


# ---------------------------------------------------------------------------
# Senioridade
# ---------------------------------------------------------------------------

# Só no TÍTULO. Na descrição "sênior" aparece em contexto que não é o cargo
# ("você vai apoiar a diretoria sênior", "equipe de analistas sêniores") e
# derrubaria vaga boa.
_SENIOR_TITULO_RE = re.compile(
    r"\bs[eê]nior\b|\bsr\.\s|\bsr\.$|\(sr\)|\bespecialista\s+s[eê]nior\b|"
    r"\bsenior\b|\bpleno\s*/\s*s[eê]nior\b|\bplena?\s*e\s*s[eê]nior\b",
    re.I,
)


def parece_senior(titulo: str) -> bool:
    """O título anuncia a vaga como sênior?

    `sr` sem ponto fica de fora de propósito: em português é abreviação de
    "Senhor" e apareceria em vaga que não tem nada a ver com senioridade.
    """
    return bool(_SENIOR_TITULO_RE.search(titulo or ""))


# ---------------------------------------------------------------------------
# Remoto
# ---------------------------------------------------------------------------

REMOTE_RE = re.compile(
    r"home\s*-?\s*office|homeoffice|100\s*%\s*remot|trabalh\w*\s+remot|"
    r"\bremot[oa]s?\b|remotamente|[àa]\s+dist[âa]ncia|"
    r"trabalh\w*\s+(?:de|em)\s+casa|work\s+from\s+home|anywhere",
    re.I,
)


def texto_menciona_remoto(texto: str) -> bool:
    return bool(REMOTE_RE.search(texto or ""))
