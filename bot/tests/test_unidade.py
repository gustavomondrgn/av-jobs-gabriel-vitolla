"""Testes das partes novas: filtros baratos, fila de envio e virada de dia."""
import sys, json, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import filters
from dispatch import SendQueue
from telemetry import DailyStats

BRT = timezone(timedelta(hours=-3))
falhas = []

def check(nome, got, esperado):
    ok = got == esperado
    print(f"{'  OK ' if ok else 'FALHA'} | {nome}: got={got!r} esperado={esperado!r}")
    if not ok:
        falhas.append(nome)

print("=" * 70)
print("IDIOMA")
print("=" * 70)

PT_REAL = """Estamos em busca de uma Assistente Virtual para atuar de forma
100% remota. As principais atividades serao o atendimento aos clientes via
WhatsApp, a organizacao da agenda do diretor, o controle de planilhas e o apoio
administrativo em geral. E necessario ter experiencia com pacote Office e boa
comunicacao escrita. Oferecemos salario compativel com o mercado e horario
flexivel. A vaga e para trabalho em casa, home office integral."""

EN_REAL = """We are looking for a Virtual Assistant to join our remote team.
You will be responsible for managing calendars, handling email correspondence,
and supporting the executive team with administrative tasks. The ideal candidate
has excellent written communication skills and is comfortable working with
spreadsheets. This is a fully remote position and we offer a competitive salary
with flexible working hours for the right person who wants to join us."""

PT_COM_TERMOS_EN = """Vaga para Customer Success Analyst em regime de home
office. Voce vai cuidar do onboarding dos clientes, fazer follow up das contas e
garantir o sucesso do cliente no uso da plataforma. Buscamos alguem com
experiencia em atendimento e conhecimento de CRM. O trabalho e 100% remoto,
com reunioes semanais online. Salario a combinar conforme experiencia."""

check("PT puro", filters.detectar_idioma(PT_REAL), "pt")
check("EN puro", filters.detectar_idioma(EN_REAL), "en")
check("PT com jargao EN", filters.detectar_idioma(PT_COM_TERMOS_EN), "pt")
check("texto curto -> indeciso", filters.detectar_idioma("Assistente Virtual"), "?")

check("parece_ingles(PT)", filters.parece_ingles("Assistente Virtual", PT_REAL), False)
check("parece_ingles(EN)", filters.parece_ingles("Virtual Assistant", EN_REAL), True)
check("titulo EN + corpo PT -> nao e ingles",
      filters.parece_ingles("Customer Success Analyst", PT_COM_TERMOS_EN), False)
check("sem descricao, titulo PT",
      filters.parece_ingles("Assistente administrativo home office", ""), False)

print()
print("=" * 70)
print("SENIORIDADE")
print("=" * 70)
check("Assistente Senior", filters.parece_senior("Assistente Executiva Sênior"), True)
check("senior sem acento", filters.parece_senior("Analista Senior de Atendimento"), True)
check("Sr. com ponto", filters.parece_senior("Assistente Sr. de Vendas"), True)
check("pleno/senior", filters.parece_senior("Analista Pleno/Sênior"), True)
check("junior nao e senior", filters.parece_senior("Assistente Junior"), False)
check("'Sr' de Senhor nao dispara", filters.parece_senior("Assistente do Sr Diretor"), False)
check("titulo comum", filters.parece_senior("Assistente Virtual Home Office"), False)

print()
print("=" * 70)
print("FILA: janela de horario, teto diario e gotejamento")
print("=" * 70)

tmp = Path(tempfile.mkdtemp())
fila = SendQueue(tmp / "q.json", validade_dias=3)

def vaga(uid, score, dia="2026-08-12"):
    return dict(uid=uid, source="gupy", title=f"Vaga {uid}", html=f"<b>{uid}</b>",
                score=score, category="relevant", published_at=dia)

base = datetime(2026, 8, 12, 10, 0, tzinfo=BRT)
for uid, score in [("a", 40), ("b", 95), ("c", 70)]:
    fila.push(**vaga(uid, score), agora=base)

# Fora da janela (5h da manha)
madrugada = datetime(2026, 8, 12, 5, 0, tzinfo=BRT)
r = fila.proxima(agora=madrugada, hoje="2026-08-12", limite_diario=8,
                 janela_inicio=6, janela_fim=23)
check("5h da manha nao publica", r, None)

# Dentro da janela: sai a de maior nota
r = fila.proxima(agora=base, hoje="2026-08-12", limite_diario=8,
                 janela_inicio=6, janela_fim=23)
check("publica a de maior nota", r["uid"] if r else None, "b")
fila.confirmar_envio(base, "2026-08-12")

# Logo em seguida: o gap ainda nao passou
r = fila.proxima(agora=base + timedelta(minutes=1), hoje="2026-08-12",
                 limite_diario=8, janela_inicio=6, janela_fim=23)
check("gotejamento segura o proximo", r, None)

# Duas horas depois: libera, e sai a segunda melhor
r = fila.proxima(agora=base + timedelta(hours=3), hoje="2026-08-12",
                 limite_diario=8, janela_inicio=6, janela_fim=23)
check("3h depois publica a 2a melhor", r["uid"] if r else None, "c")

print()
print("--- teto diario ---")
fila2 = SendQueue(tmp / "q2.json")
for i in range(10):
    fila2.push(**vaga(f"x{i}", 50 + i), agora=base)

enviadas = []
t = datetime(2026, 8, 12, 6, 0, tzinfo=BRT)
# Varre o dia inteiro de 10 em 10 min, como o laco real faz
while t.hour < 23:
    r = fila2.proxima(agora=t, hoje="2026-08-12", limite_diario=8,
                      janela_inicio=6, janela_fim=23)
    if r:
        enviadas.append((t.strftime("%H:%M"), r["uid"], r["score"]))
        fila2.confirmar_envio(t, "2026-08-12")
    t += timedelta(minutes=10)

check("respeitou o teto de 8/dia", len(enviadas), 8)
check("publicou em ordem de nota", [e[2] for e in enviadas],
      sorted([e[2] for e in enviadas], reverse=True))
print("     horarios:", ", ".join(f"{h}(n{s})" for h, _, s in enviadas))

# Dia seguinte: a cota reabre e as 2 que sobraram saem
t2 = datetime(2026, 8, 13, 6, 0, tzinfo=BRT)
r = fila2.proxima(agora=t2, hoje="2026-08-13", limite_diario=8,
                  janela_inicio=6, janela_fim=23)
check("sobra concorre no dia seguinte", r is not None, True)

print()
print("--- validade de 3 dias ---")
fila3 = SendQueue(tmp / "q3.json", validade_dias=3)
fila3.push(**vaga("velha", 90), agora=datetime(2026, 8, 1, 10, 0, tzinfo=BRT))
fila3.push(**vaga("nova", 10), agora=base)
r = fila3.proxima(agora=base, hoje="2026-08-12", limite_diario=8,
                  janela_inicio=6, janela_fim=23)
check("vaga de 11 dias foi descartada", r["uid"] if r else None, "nova")

print()
print("--- persistencia (simula redeploy) ---")
p = tmp / "q4.json"
f4 = SendQueue(p)
f4.push(**vaga("p1", 80), agora=base)
f4.proxima(agora=base, hoje="2026-08-12", limite_diario=8, janela_inicio=6, janela_fim=23)
f4.confirmar_envio(base, "2026-08-12")
f4.push(**vaga("p2", 60), agora=base)
del f4
f4b = SendQueue(p)   # "redeploy"
check("cota do dia sobreviveu ao restart", f4b.resumo("2026-08-12")["enviadas_hoje"], 1)
check("fila sobreviveu ao restart", f4b.resumo("2026-08-12")["na_fila"], 1)

print()
print("=" * 70)
print("TELEMETRIA: a virada do dia (o bug do relatorio)")
print("=" * 70)

st = DailyStats(tmp / "stats.json", historico=tmp / "hist.jsonl")
st.bump("2026-08-12", "sent", "gupy")
st.bump("2026-08-12", "sent", "indeed")
st.bump("2026-08-12", "queued", "gupy")
check("contou 2 enviadas no dia", st.total("sent"), 2)

# O cenario do bug: 21h local = meia-noite UTC. Com o dia LOCAL, nada zera.
st.virar_se_preciso("2026-08-12")
check("21h local nao zera o dia", st.total("sent"), 2)

st2 = DailyStats(tmp / "stats.json", historico=tmp / "hist.jsonl")  # "redeploy"
check("contadores sobreviveram ao redeploy", st2.total("sent"), 2)

st2.bump("2026-08-13", "sent", "gupy")   # virada real
check("dia novo zera de verdade", st2.total("sent"), 1)
hist = (tmp / "hist.jsonl").read_text(encoding="utf-8").strip().splitlines()
check("dia anterior foi arquivado", json.loads(hist[-1])["sent"], 2)

print()
print("--- relatorio nao repete apos restart ---")
st3 = DailyStats(tmp / "stats2.json")
check("relatorio pendente no comeco", st3.relatorio_pendente("2026-08-12"), True)
st3.marcar_relatorio_enviado("2026-08-12")
st4 = DailyStats(tmp / "stats2.json")
check("apos restart continua marcado", st4.relatorio_pendente("2026-08-12"), False)
check("no dia seguinte volta a pendente", st4.relatorio_pendente("2026-08-13"), True)

print()
print("=" * 70)
if falhas:
    print(f"FALHARAM {len(falhas)}: {falhas}")
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")

print()
print("=" * 70)
print("CATEGORIAS POR FONTE (filtro do painel)")
print("=" * 70)
import os, tempfile as _tf
os.environ.setdefault("DATA_DIR", _tf.mkdtemp())
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100")
import main

cfg_vazio = {"sources": {}}
check("sem config, tudo ligado", main.categoria_ligada(cfg_vazio, "gupy", "financeiro"), True)

cfg = {"sources": {"gupy": {"categorias": {"financeiro": False, "comercial": True}}}}
check("financeiro desligado na gupy", main.categoria_ligada(cfg, "gupy", "financeiro"), False)
check("comercial ligado na gupy   ", main.categoria_ligada(cfg, "gupy", "comercial"), True)
check("categoria nova nasce ligada", main.categoria_ligada(cfg, "gupy", "secretariado"), True)
check("outra fonte nao e afetada  ", main.categoria_ligada(cfg, "indeed", "financeiro"), True)

# A lista precisa bater com a do painel, senao o Gabriel desliga algo que o bot ignora.
esperadas = {"secretariado", "atendimento", "comercial", "administrativo",
             "financeiro", "agenda_clinicas", "customer_success", "outro"}
check("lista de categorias do bot ", set(main.CATEGORIAS), esperadas)

print()
print("=" * 70)
print("PREFERENCIA POR VAGA NAO-CLT (bonus de regime)")
print("=" * 70)

# O bonus so vale para quem NAO e CLT. "nao_informado" ficar de fora e o ponto
# central: e um terco do acervo, e premiar o silencio do anunciante premiaria
# quase todo mundo.
cfg_bonus = {"bonus_regime_pj": 100}
check("nao_clt ganha o bonus      ", main.bonus_regime(cfg_bonus, "nao_clt"), 100)
check("ambos (CLT ou PJ) ganha    ", main.bonus_regime(cfg_bonus, "ambos"), 100)
check("clt nao ganha              ", main.bonus_regime(cfg_bonus, "clt"), 0)
check("nao_informado nao ganha    ", main.bonus_regime(cfg_bonus, "nao_informado"), 0)

# O caminho de volta: zerar no painel desliga a regra inteira.
check("bonus 0 desliga a regra    ", main.bonus_regime({"bonus_regime_pj": 0}, "nao_clt"), 0)
check("config ausente nao quebra  ", main.bonus_regime({}, "nao_clt"), 0)
check("lixo no campo nao quebra   ", main.bonus_regime({"bonus_regime_pj": "abc"}, "nao_clt"), 0)
check("valor absurdo e limitado   ", main.bonus_regime({"bonus_regime_pj": 9999}, "nao_clt"), 200)

# A lista de regimes precisa bater com a do painel e com a do schema.
check("regimes conhecidos         ", set(main.REGIMES),
      {"nao_clt", "clt", "ambos", "nao_informado"})

# Sem classificador nao ha regime, e vaga nao lida nao pode ganhar prioridade.
check("fallback nao ganha bonus   ",
      main.bonus_regime(cfg_bonus, main._fallback_analysis("x")["regime"]), 0)

print()
print("=" * 70)
print("A FILA ORDENA PELO BONUS, SEM MEXER NA NOTA")
print("=" * 70)

fila_b = SendQueue(Path(tempfile.mkdtemp()) / "fila.json")
agora_b = datetime(2026, 8, 20, 9, 0, tzinfo=BRT)

def _push(uid, score, bonus, regime):
    fila_b.push(uid=uid, source="indeed", title=uid, html="x", score=score,
                category="relevant", published_at="2026-08-20", agora=agora_b,
                regime=regime, bonus=bonus)

_push("clt-otima", 95, 0, "clt")
_push("pj-mediana", 55, 100, "nao_clt")
_push("clt-boa", 80, 0, "clt")

primeira = fila_b.proxima(agora=agora_b, hoje="2026-08-20", limite_diario=8,
                          janela_inicio=6, janela_fim=23)
check("PJ mediana passa na frente ", primeira["uid"], "pj-mediana")
check("a nota de qualidade nao mudou", primeira["score"], 55)
check("o bonus fica em campo proprio", primeira["bonus"], 100)

# Item gravado antes deste deploy nao tem o campo `bonus`. A fila em disco
# sobrevive ao redeploy, entao isso acontece de verdade na primeira subida.
antigo = {"uid": "velho", "score": 90, "published_at": "2026-08-19"}
check("item sem bonus vale zero   ", SendQueue._chave_prioridade(antigo), (90, "2026-08-19"))

print()
print("=" * 70)
if falhas:
    print(f"FALHARAM {len(falhas)}: {falhas}")
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
