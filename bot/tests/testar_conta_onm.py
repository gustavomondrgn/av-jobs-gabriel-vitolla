"""Responde UMA pergunta: esta conta do ONM devolve a descrição completa?

A conta gratuita do bot devolve a descrição truncada em 10 caracteres, o que
deixa o classificador cego justamente na fonte principal do contrato. A hipótese
é que a conta paga do Gabriel devolva o texto inteiro — mas isso é hipótese, e
trocar as credenciais em produção sem verificar seria prometer no escuro.

    python bot/tests/testar_conta_onm.py <email> <senha>

Não escreve nada, não altera nada: só loga, lê 15 vagas e mede.
Compara com a conta do `.env` quando ela existir, para a diferença ficar visível
lado a lado.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
from sources import ONMSource, strip_html  # noqa: E402

# Campos que a conta gratuita não enxerga. Se aparecerem preenchidos, é sinal
# forte de que a conta paga vê mais coisa.
CAMPOS_PAGOS = ("proposalExternalLink", "proposalsCount", "author")


def medir(email: str, senha: str, rotulo: str) -> dict:
    print(f"\n{'=' * 72}\n{rotulo}: {email}\n{'=' * 72}")
    fonte = ONMSource(email, senha)

    try:
        fonte.login()
    except requests.HTTPError as exc:
        print(f"  LOGIN FALHOU: {exc.response.status_code} — "
              f"{exc.response.text[:180]}")
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"  LOGIN FALHOU: {type(exc).__name__}: {exc}")
        return {}

    print("  login OK")

    bruto = fonte._get_projects()
    if not bruto:
        print("  a API não devolveu nenhuma vaga")
        return {}

    tamanhos = []
    print(f"\n  {'id':>8}  {'chars':>6}  prévia da descrição")
    print(f"  {'-' * 8}  {'-' * 6}  {'-' * 42}")
    for j in bruto[:15]:
        desc = strip_html(j.get("description"))
        tamanhos.append(len(desc))
        print(f"  {str(j.get('id')):>8}  {len(desc):>6}  {desc[:42]!r}")

    media = sum(tamanhos) / len(tamanhos)
    completas = sum(1 for t in tamanhos if t > 200)

    print(f"\n  média de caracteres : {media:.0f}")
    print(f"  maior               : {max(tamanhos)}")
    print(f"  com texto real (>200 chars): {completas}/{len(tamanhos)}")

    print("\n  campos que a conta gratuita não enxerga:")
    amostra = bruto[0]
    for campo in CAMPOS_PAGOS:
        valor = amostra.get(campo)
        estado = "VAZIO" if valor in (None, "", [], {}) else f"PREENCHIDO ({str(valor)[:50]})"
        print(f"    {campo:<24} {estado}")

    # O veredito é o que importa: descrição curta = classificador cego.
    if media > 200:
        print("\n  >>> VEREDITO: descrição COMPLETA. Esta conta resolve o problema.")
    elif media > 30:
        print("\n  >>> VEREDITO: descrição parcial. Melhor que a gratuita, mas conferir.")
    else:
        print("\n  >>> VEREDITO: descrição TRUNCADA. Esta conta NÃO resolve.")

    return {"media": media, "max": max(tamanhos), "completas": completas}


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    novo = medir(sys.argv[1], sys.argv[2], "CONTA INFORMADA (paga?)")

    # Comparação com a conta atual, se ela estiver no ambiente.
    atual_email = os.getenv("ONM_EMAIL", "").strip()
    atual_senha = os.getenv("ONM_PASSWORD", "").strip()
    if atual_email and atual_email.lower() != sys.argv[1].lower():
        antigo = medir(atual_email, atual_senha, "CONTA ATUAL DO BOT (gratuita)")
        if novo and antigo:
            print(f"\n{'=' * 72}\nCOMPARAÇÃO\n{'=' * 72}")
            print(f"  gratuita: {antigo['media']:.0f} chars em média")
            print(f"  informada: {novo['media']:.0f} chars em média")
            ganho = novo["media"] - antigo["media"]
            print(f"  diferença: {ganho:+.0f} chars")
            if ganho > 150:
                print("\n  >>> Vale trocar. Atualizar ONM_EMAIL e ONM_PASSWORD no Coolify.")
            else:
                print("\n  >>> Não vale: a conta paga entrega o mesmo. O problema é "
                      "da API, não do plano.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
