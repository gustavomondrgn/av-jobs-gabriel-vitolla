# ONM Jobs Bot

Script Python que monitora novos jobs publicados na plataforma **O Mercado de Trabalho** (do O Novo Mercado) e envia notificações em tempo real para um grupo no Telegram.

A plataforma não possui sistema de notificações nativo — este bot resolve isso consultando a API interna a cada N minutos e enviando mensagens formatadas via Telegram Bot API.

## Como funciona

1. Faz login na API do ONM e guarda o JWT em memória
2. A cada `CHECK_INTERVAL` segundos consulta os jobs mais recentes
3. Compara os IDs retornados com `seen_ids.json` (persistido em disco)
4. Para cada job novo, **classifica via Gemini Flash 2.5** usando o perfil em [`profile.md`](profile.md)
5. Roteia o job conforme a categoria:
   - **`relevant`** → notifica no Telegram (mensagem normal)
   - **`borderline`** → notifica no Telegram com tag 🤔 e o motivo
   - **`irrelevant`** → não notifica, anexa em `skipped_jobs.jsonl` pra revisão
6. Re-autentica automaticamente se receber 401

Na **primeira execução** apenas salva os IDs existentes (sem notificar) para evitar flood.

### Filtro inteligente

O filtro foi desenhado com viés de **falso positivo** (mandar a mais) em vez de
**falso negativo** (perder job). Se o classificador tem qualquer dúvida, a
categoria é `borderline` e a notificação chega marcada. Se o Gemini estiver
indisponível ou o `GEMINI_API_KEY` não estiver configurado, o bot **notifica
tudo** (fallback seguro) — você nunca perde job por falha do filtro.

Pra ajustar o que entra/sai, edite [`profile.md`](profile.md):

- **Rodando local com `python main.py`:** o arquivo é relido a cada checagem
  (cache invalidado por `mtime`), então **não precisa restart**.
- **Em produção via Docker/Coolify:** edite, commite e pushe — o redeploy
  pega a nova versão (o `profile.md` é embutido na imagem via `COPY`).

Pra revisar o que foi descartado:

```bash
# Local
tail -f skipped_jobs.jsonl

# Docker
docker exec onm-jobs-bot tail -f /app/data/skipped_jobs.jsonl
```

## Uso local

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

cp .env.example .env
# editar .env com as credenciais

python main.py
```

Para testar imediatamente, defina `CHECK_INTERVAL=30` no `.env`.

## Variáveis de ambiente

| Variável           | Descrição                                                  | Default            |
| ------------------ | ---------------------------------------------------------- | ------------------ |
| `ONM_EMAIL`        | E-mail da conta ONM                                        | —                  |
| `ONM_PASSWORD`     | Senha da conta ONM                                         | —                  |
| `TELEGRAM_TOKEN`   | Token do bot do Telegram                                   | —                  |
| `TELEGRAM_CHAT_ID` | ID do grupo/chat de destino                                | —                  |
| `CHECK_INTERVAL`   | Intervalo entre checagens (segundos)                       | `600`              |
| `DATA_DIR`         | Onde `seen_ids.json` e `skipped_jobs.jsonl` ficam          | `.`                |
| `GEMINI_API_KEY`   | API key do Gemini — se vazio, filtro desativado            | —                  |
| `GEMINI_MODEL`     | Modelo do Gemini a usar                                    | `gemini-2.5-flash` |
| `PROFILE_FILE`     | Caminho do `profile.md`                                    | `profile.md`       |

> Para gerar `GEMINI_API_KEY`: <https://aistudio.google.com/apikey> → "Create API key".
> Free tier do `gemini-2.5-flash` é generoso (centenas de req/dia, suficiente
> pra esse volume de jobs).

## Deploy via Docker

```bash
docker compose up -d --build
```

O `seen_ids.json` é persistido no volume `bot-data`, sobrevivendo a restarts.

## Deploy via Coolify (Hetzner)

1. Push do código para um repositório privado no GitHub
2. No Coolify: New Resource → Docker Compose → conectar ao repo
3. Adicionar as variáveis de ambiente
4. Deploy

## Stack

- Python 3.12
- `requests` + `python-dotenv`
- `google-genai` (Gemini Flash 2.5 para classificação dos jobs)
- Docker / docker-compose

Sem frameworks de agentes, sem banco de dados — apenas um script enxuto que faz uma coisa bem feita.
