"""Ponta a ponta: coleta -> filtros -> classificador -> fila -> publicacao -> relatorio.
Sem rede: o classificador e o Telegram sao substituidos."""
import os, sys, shutil, tempfile
from pathlib import Path

RAIZ = str(Path(__file__).resolve().parents[1])
DADOS = Path(tempfile.mkdtemp())
os.environ["DATA_DIR"] = str(DADOS)
os.environ["TELEGRAM_TOKEN"] = "x"
os.environ["TELEGRAM_CHAT_ID"] = "-100"
sys.path.insert(0, RAIZ)

import main
from sources import Job

# --- dublês -----------------------------------------------------------------
PUBLICADAS = []
main.send_telegram = lambda texto, chat_id=None: PUBLICADAS.append(texto)

# O "classificador": devolve o que estiver no dicionario, por titulo.
RESPOSTAS = {}
def fake_analyze(job, cfg):
    base = main._fallback_analysis("")
    base.update({"category": "relevant", "score": 50, "work_mode": "remoto",
                 "language": "pt", "seniority": "pleno", "reason": "ok"})
    base.update(RESPOSTAS.get(job.title, {}))
    main._bump("classified", job.source)
    # Reaplica as regras duras exatamente como o analyze_job real faz.
    recusados = main.modos_recusados(cfg, job.source)
    if base["work_mode"] in recusados:
        base.update(category="irrelevant", motivo_corte="sem_remoto",
                    reason="nao declara remoto")
    elif main.regra(cfg, job.source, "reject_english") and base["language"] == "en":
        base.update(category="irrelevant", motivo_corte="ingles", reason="em ingles")
    elif main.regra(cfg, job.source, "reject_senior") and base["seniority"] == "senior":
        base.update(category="irrelevant", motivo_corte="senior", reason="senior")
    return base
main.analyze_job = fake_analyze

class FakeFonte:
    name = "gupy"; label = "Gupy"; prefilter_remote = False
    interval_seconds = 3600
    def __init__(self, jobs): self.jobs = jobs; self.last_count = None; self.last_fetch_at = None
    def is_due(self, agora): return True
    def mark_fetched(self, agora): pass
    def fetch(self): return self.jobs

def vaga(id_, titulo, desc="Descricao em portugues com detalhes suficientes."):
    return Job(source="gupy", source_id=id_, title=titulo, url=f"http://x/{id_}",
               description=desc, company="Empresa X", published_at="2026-08-12")

PT = ("Buscamos uma assistente para atuar de forma remota, cuidando da agenda, "
      "do atendimento aos clientes e do apoio administrativo da diretoria. "
      "E necessario ter boa comunicacao e experiencia com planilhas. O trabalho "
      "e em home office integral, com horario flexivel e salario a combinar.")
EN = ("We are looking for a virtual assistant to support our executive team with "
      "calendar management and email correspondence. The ideal candidate has "
      "strong written communication skills and is comfortable with spreadsheets. "
      "This is a fully remote position with a competitive salary for the person.")

JOBS = [
    vaga("1", "Assistente Virtual Home Office", PT),
    vaga("2", "Virtual Assistant Remote", EN),                    # corte: ingles (barato)
    vaga("3", "Assistente Executiva Senior", PT),                 # corte: senior (barato)
    vaga("4", "Assistente Administrativo", PT),                   # corte: sem remoto (IA)
    vaga("5", "Assistente Financeiro Remoto", PT),
    vaga("6", "Secretaria Remota", PT),
]
RESPOSTAS = {
    "Assistente Virtual Home Office": {"score": 92, "role_type": "Assistente Virtual"},
    "Assistente Administrativo":      {"work_mode": "nao_informado"},
    "Assistente Financeiro Remoto":   {"score": 60},
    "Secretaria Remota":              {"score": 78},
}

fonte = FakeFonte(JOBS)
cfg = main.config_atual()

print("=" * 72)
print("CICLO 1 — primeira coleta (registra em silencio, nao publica)")
print("=" * 72)
seen, keys, ini = set(), set(), set()
seen, keys, ini = main.check_new_jobs([fonte], seen, keys, ini, cfg)
print(f"  vagas registradas: {len(seen)} | publicadas: {len(PUBLICADAS)}")
assert len(PUBLICADAS) == 0, "primeira coleta nao pode publicar"
assert len(seen) == 6

print()
print("=" * 72)
print("CICLO 2 — vagas novas de verdade")
print("=" * 72)
seen, keys, ini = set(), set(), {"gupy"}   # ja inicializada, tudo e novidade
main.STATS.virar_se_preciso(main.hoje_local())
seen, keys, ini = main.check_new_jobs([fonte], seen, keys, ini, cfg)

t = main.STATS.totais()
print(f"  classificadas : {t['classified']}")
print(f"  cortadas ingles: {t['ingles']}   (esperado 1)")
print(f"  cortadas senior: {t['senior']}   (esperado 1)")
print(f"  sem remoto     : {t['sem_remoto']}   (esperado 1)")
print(f"  ENFILEIRADAS   : {t['queued']}   (esperado 3)")
print(f"  publicadas     : {t['sent']}   (esperado 0 — a fila segura)")
assert t["ingles"] == 1, t
assert t["senior"] == 1, t
assert t["sem_remoto"] == 1, t
assert t["queued"] == 3, t
assert len(PUBLICADAS) == 0, "nada pode sair sem passar pelo despachante"
# O corte de ingles e de senior sao baratos: nao gastaram IA.
print(f"  -> economia: {6 - t['classified']} vagas cortadas SEM gastar cota de IA")

print()
print("=" * 72)
print("CICLO 3 — despacho dentro e fora da janela")
print("=" * 72)
import datetime as dt
BRT = dt.timezone(dt.timedelta(hours=-3))

def com_hora(h, m=0, dia=12):
    main.agora_local = lambda: dt.datetime(2026, 8, dia, h, m, tzinfo=BRT)
    main.hoje_local = lambda: f"2026-08-{dia:02d}"

com_hora(4)          # madrugada
main.despachar(cfg)
print(f"  04:00 -> publicadas: {len(PUBLICADAS)}  (esperado 0)")
assert len(PUBLICADAS) == 0

com_hora(23, 30)     # depois da janela
main.despachar(cfg)
print(f"  23:30 -> publicadas: {len(PUBLICADAS)}  (esperado 0)")
assert len(PUBLICADAS) == 0

com_hora(9)          # dentro da janela
main.despachar(cfg)
print(f"  09:00 -> publicadas: {len(PUBLICADAS)}  (esperado 1)")
assert len(PUBLICADAS) == 1
assert "Assistente Virtual Home Office" in PUBLICADAS[0], "devia sair a de maior nota"
print("  -> saiu a de nota 92, como esperado")

com_hora(9, 10)      # logo depois
main.despachar(cfg)
print(f"  09:10 -> publicadas: {len(PUBLICADAS)}  (esperado 1, gotejamento segura)")
assert len(PUBLICADAS) == 1

com_hora(16)
main.despachar(cfg)
print(f"  16:00 -> publicadas: {len(PUBLICADAS)}  (esperado 2)")
assert len(PUBLICADAS) == 2
assert "Secretaria Remota" in PUBLICADAS[1], "devia sair a nota 78"
print("  -> saiu a de nota 78, na ordem certa")

print()
print("=" * 72)
print("RELATORIO DAS 22h (o que o Gabriel vai receber)")
print("=" * 72)
com_hora(22)
print(main.montar_relatorio([fonte], cfg).replace("<b>","").replace("</b>","")
      .replace("<i>","").replace("</i>",""))

print()
print("=" * 72)
print("MENSAGEM DE VAGA (o que vai pro grupo)")
print("=" * 72)
print(PUBLICADAS[0].replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>",""))

shutil.rmtree(DADOS, ignore_errors=True)
print()
print("=" * 72)
print("E2E OK")
