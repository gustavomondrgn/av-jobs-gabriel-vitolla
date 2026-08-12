"""Testes do revisor de vagas encerradas."""
import sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vitality
from publicadas import RegistroPublicadas

BRT = timezone(timedelta(hours=-3))
falhas = []
def check(nome, got, esp):
    ok = got == esp
    print(f"{'  OK ' if ok else 'FALHA'} | {nome}: got={got!r} esperado={esp!r}")
    if not ok: falhas.append(nome)

class Resp:
    def __init__(self, code): self.status_code = code; self.ok = 200 <= code < 300; self.text = ""

print("=" * 70); print("CLASSIFICACAO DA RESPOSTA HTTP"); print("=" * 70)
check("200 -> aberta     ", vitality._classificar(Resp(200)), "aberta")
check("404 -> fechada    ", vitality._classificar(Resp(404)), "fechada")
# Os tres perigosos: bloqueio nao pode virar "fechada", senao apaga vaga boa.
check("403 -> desconhecida", vitality._classificar(Resp(403)), "desconhecida")
check("429 -> desconhecida", vitality._classificar(Resp(429)), "desconhecida")
check("500 -> desconhecida", vitality._classificar(Resp(500)), "desconhecida")
# O Indeed agora e verificavel pela API do app; o que nao pode e um erro de
# rede virar "fechada" e apagar doze mensagens boas de uma vez.
check("indeed: lote vazio     ", vitality.verificar_indeed([]), {})
check("onm sem token       ", vitality.verificar("onm", "1"), "desconhecida")

print(); print("=" * 70); print("REGISTRO: dupla confirmacao antes de encerrar"); print("=" * 70)
tmp = Path(tempfile.mkdtemp())
r = RegistroPublicadas(tmp / "p.json", dias_de_vida=30)
t0 = datetime(2026, 8, 12, 10, 0, tzinfo=BRT)
r.registrar(uid="gupy:1", source="gupy", source_id="1", title="Vaga A",
            message_id=555, agora=t0, html="<b>Vaga A</b>")

check("1o 404 nao encerra   ", r.marcar_checada("gupy:1", t0 + timedelta(hours=12), True), False)
check("2o 404 encerra       ", r.marcar_checada("gupy:1", t0 + timedelta(hours=24), True), True)
check("nao encerra duas vezes", r.marcar_checada("gupy:1", t0 + timedelta(hours=36), True), False)

print()
r.registrar(uid="gupy:2", source="gupy", source_id="2", title="Vaga B",
            message_id=556, agora=t0, html="")
check("404 e depois volta   ", r.marcar_checada("gupy:2", t0 + timedelta(hours=12), True), False)
r.marcar_checada("gupy:2", t0 + timedelta(hours=24), False)   # voltou a responder
check("contador zerou       ", r.marcar_checada("gupy:2", t0 + timedelta(hours=36), True), False)

print(); print("--- ritmo das checagens ---")
r2 = RegistroPublicadas(tmp / "p2.json")
for i in range(20):
    r2.registrar(uid=f"gupy:{i}", source="gupy", source_id=str(i), title=f"V{i}",
                 message_id=1000+i, agora=t0)
lote = r2.a_checar(agora=t0, intervalo_horas=12, limite=12)
check("respeita o limite por ciclo", len(lote), 12)
for it in lote: r2.marcar_checada(it["uid"], t0, False)
check("nao recheca antes do intervalo",
      len(r2.a_checar(agora=t0 + timedelta(hours=1), intervalo_horas=12, limite=12)), 8)
check("recheca depois do intervalo",
      len(r2.a_checar(agora=t0 + timedelta(hours=13), intervalo_horas=12, limite=99)), 20)

print(); print("--- poda e persistencia ---")
r3 = RegistroPublicadas(tmp / "p3.json", dias_de_vida=30)
r3.registrar(uid="v:velha", source="gupy", source_id="9", title="velha",
             message_id=1, agora=t0 - timedelta(days=45))
r3.registrar(uid="v:nova", source="gupy", source_id="8", title="nova",
             message_id=2, agora=t0)
check("vaga de 45 dias foi podada",
      [i["uid"] for i in r3.a_checar(agora=t0, intervalo_horas=1, limite=9)], ["v:nova"])
del r3
check("sobreviveu ao restart", RegistroPublicadas(tmp / "p3.json").resumo()["acompanhadas"], 1)

print(); print("=" * 70)
if falhas:
    print(f"FALHARAM {len(falhas)}: {falhas}"); sys.exit(1)
print("TODOS OS TESTES DO REVISOR PASSARAM")
