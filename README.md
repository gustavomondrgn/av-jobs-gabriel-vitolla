# AV Jobs — Gabriel Vitolla

Bot que monitora vagas e projetos publicados na plataforma **O Mercado de Trabalho** (do O Novo Mercado), filtra o que faz sentido para o perfil de **Assistente Virtual** e envia as vagas aprovadas para um grupo no Telegram.

O grupo é um benefício oferecido pelo Gabriel Vitolla aos alunos do curso de Assistente Virtual / secretariado remoto.

## Fontes

| Fonte | Como é lida | Login? | Intervalo padrão |
| --- | --- | --- | --- |
| **ONM** (O Mercado de Trabalho) | API JSON | sim, conta do bot | 10 min |
| **Gupy** | API pública do portal | não | 1 h |
| **Indeed** | API do app via [`python-jobspy`](https://github.com/speedyapply/JobSpy) | não | 1 h |
| **LinkedIn** | guest API pública, em duas fases | não | 2 h |

Cada fonte tem seu **próprio ritmo**, porque cada uma custa um número diferente
de requisições:

- **ONM:** 1 requisição por ciclo. Barato, pode ser frequente.
- **Gupy e Indeed:** 1 requisição **por termo de busca** (são ~23). De 10 em 10
  minutos dariam mais de 3 mil requisições diárias em cada plataforma — pedir
  bloqueio sem ganhar quase nada.
- **LinkedIn:** o mais caro. A busca **não devolve a descrição** da vaga, então
  cada vaga custa uma requisição a mais. Por isso ele tem lista de termos
  própria e menor, e roda de 2 em 2 horas.

O custo de espaçar é baixo: uma vaga publicada às 14h05 chega ao grupo às 15h em
vez de às 14h10.

> **Nenhuma fonte usa conta logada do LinkedIn.** Existem bibliotecas populares
> que fazem isso, e todas avisam do mesmo risco: automação com sessão logada é
> o caminho conhecido para a conta ser restringida — e a conta seria a do
> Gabriel. A guest API resolve deslogado.

Ligar e desligar fonte é a variável `SOURCES`, sem tocar em código.

### Como o LinkedIn é lido (duas fases)

1. **Busca** — uma requisição por termo, traz título, empresa, local e data
2. **Pré-filtro barato** — vaga remota no LinkedIn costuma vir com local
   "Brasil" em vez de cidade; só as promissoras seguem
3. **Descrição** — uma requisição por vaga sobrevivente
4. O classificador decide, já com a descrição na mão

Medido em 11/08: de 58 vagas encontradas, 11 passaram no pré-filtro — **47
requisições economizadas (81%)**. Existe ainda um teto de 40 descrições por
ciclo, para o caso de a busca despejar muito mais do que o esperado.

Por que o classificador continua sendo necessário: apareceu uma vaga intitulada
*"Auxiliar Administrativo - Trabalhe de casa"*, com local "Brasil", dentro do
filtro de remoto do LinkedIn — e a descrição dizia *"Local de trabalho:
Guarulhos, SP"*. Título, local e filtro da plataforma mentiram juntos. Só a
descrição conta a verdade.

## O que entra no grupo

Definido com o Gabriel na reunião de 07/08/2026 e detalhado em [`profile.md`](profile.md):

- **Prioridade: assistência virtual.** Como vaga específica de AV é rara, o
  filtro abre para o leque que essa galera realmente ocupa — secretariado,
  atendimento, comercial (SDR/BDR/closer/inside sales), administrativo,
  financeiro, agenda/clínicas e customer success.
- **Regra dura: 100% remoto.** Vaga presencial ou híbrida é descartada, por
  melhor que a função encaixe. Vaga que não informa a modalidade não é
  descartada por isso — vai no máximo como `borderline`.
- PJ, freelancer e prestador de serviço são bem-vindos, mas não obrigatórios.

Cada mensagem traz título, empresa/autor, tipo e área da vaga, skills, valores
(quando houver), descrição resumida e o link para candidatura.

## Como funciona

A cada `CHECK_INTERVAL` segundos o bot acorda e roda um ciclo:

1. **Coleta** nas fontes que estão no horário delas (cada uma tem seu intervalo)
2. **Deduplica** — a mesma vaga anunciada em duas plataformas vira uma só,
   comparando título + empresa normalizados
3. **Pré-filtra** de graça, por texto, nas fontes que não filtram remoto no
   servidor — corta o grosso do volume antes de gastar uma chamada de IA
4. **Classifica** o que sobrou via Gemini Flash, usando [`profile.md`](profile.md)
5. **Roteia** conforme a categoria:
   - **`relevant`** → notifica no Telegram (mensagem normal)
   - **`borderline`** → notifica no Telegram com tag 🤔 e o motivo
   - **`irrelevant`** → não notifica, anexa em `skipped_jobs.jsonl` pra revisão

O estado fica em `seen_ids.json` (no volume), com o ID prefixado pela fonte
(`onm:123`, `gupy:456`) para IDs de plataformas diferentes não colidirem.

**Fonte nova nunca inunda o grupo.** Na primeira vez que uma fonte é consultada,
o acervo existente dela é registrado **sem notificar** — só o que aparecer
depois é enviado. Isso vale para a primeira execução do bot e também para
quando você liga uma fonte nova num bot que já estava rodando.

### Filtro inteligente

O filtro foi desenhado com viés de **falso positivo** (mandar a mais) em vez de
**falso negativo** (perder vaga). Se o classificador tem qualquer dúvida, a
categoria é `borderline` e a notificação chega marcada. Se o Gemini estiver
indisponível ou o `GEMINI_API_KEY` não estiver configurado, o bot **notifica
tudo** (fallback seguro) — nunca se perde vaga por falha do filtro.

Pra ajustar o que entra/sai, edite [`profile.md`](profile.md) — é ele que define
o perfil de Assistente Virtual usado pelo classificador. O `main.py` não tem
nenhuma regra de área hardcoded:

- **Rodando local com `python main.py`:** o arquivo é relido a cada checagem
  (cache invalidado por `mtime`), então **não precisa restart**.
- **Em produção via Docker/Coolify:** edite, commite e pushe — o redeploy
  pega a nova versão (o `profile.md` é embutido na imagem via `COPY`).

Cada mensagem no grupo mostra **de qual plataforma a vaga veio**, logo na
primeira linha: `🏢 VAGA · Indeed`, `📋 PROJETO · O Mercado de Trabalho`.

## Comandos do bot

O bot aceita comandos **no privado**, nunca no grupo — no grupo o comando
apareceria para todos os alunos, e daria pra ver que o Gabriel pausou alguma
coisa. Mensagem vinda de grupo é ignorada sem resposta.

| Comando | O que faz |
| --- | --- |
| `/status` | Fontes, última e próxima checagem, números do dia |
| `/fontes` | Lista as fontes e o estado de cada uma |
| `/pausar <fonte>` | Para de buscar naquela fonte (aceita `tudo`) |
| `/retomar <fonte>` | Volta a buscar (aceita `tudo`) |
| `/relatorio` | Manda o relatório do dia na hora |
| `/ajuda` | Lista os comandos |

Só os user IDs em `TELEGRAM_ADMIN_IDS` podem executar. **Sem essa variável os
comandos ficam desligados** — do contrário qualquer pessoa que achasse o bot
poderia pausar as fontes.

**Como descobrir seu user ID:** mande qualquer mensagem no privado do bot e
procure no log a linha `Comando ... recusado: user_id=NNNN`. Esse número é o seu.

O que você pausar **sobrevive a redeploy** (fica em `bot_state.json`, no
volume). Comandos enviados enquanto o bot estava fora do ar são descartados no
boot — senão um `/pausar` de ontem seria executado hoje, do nada.

## Relatório diário

Todo dia às `REPORT_HOUR` (padrão 22h, horário de Brasília) o bot manda um
resumo: quantas vagas cada fonte trouxe, quantas foram enviadas, descartadas,
cortadas no pré-filtro ou repetidas, e a saúde da cota de IA.

O destino é configurável em `REPORT_TO`: `grupo` (padrão) ou `privado`. Se o bot
reiniciar depois de já ter mandado o relatório do dia, ele **não repete** — o dia
do último envio também fica no `bot_state.json`.

> O container roda em UTC. O relatório usa o fuso de `TIMEZONE`
> (`America/Sao_Paulo` por padrão), senão sairia às 19h.

Pra revisar o que foi descartado:

```bash
# Local
tail -f skipped_jobs.jsonl

# Docker
docker exec av-jobs-bot tail -f /app/data/skipped_jobs.jsonl
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

| Variável | Descrição | Default |
| --- | --- | --- |
| `ONM_EMAIL` | E-mail da conta ONM usada por este bot | — |
| `ONM_PASSWORD` | Senha da conta ONM | — |
| `TELEGRAM_TOKEN` | Token do bot do Telegram deste projeto | — |
| `TELEGRAM_CHAT_ID` | ID do grupo de destino (grupo do Gabriel) | — |
| `SOURCES` | Fontes ativas, separadas por vírgula | `onm,gupy,indeed,linkedin` |
| `CHECK_INTERVAL` | De quanto em quanto o bot acorda (segundos) | `600` |
| `INTERVAL_ONM` | Intervalo da fonte ONM (segundos) | `600` |
| `INTERVAL_GUPY` | Intervalo da fonte Gupy (segundos) | `3600` |
| `INTERVAL_INDEED` | Intervalo da fonte Indeed (segundos) | `3600` |
| `INTERVAL_LINKEDIN` | Intervalo da fonte LinkedIn (segundos) | `7200` |
| `DATA_DIR` | Onde `seen_ids.json` e `skipped_jobs.jsonl` ficam | `.` |
| `GEMINI_API_KEY` | API key do Gemini — se vazio, filtro desativado | — |
| `GEMINI_MODEL` | Modelo do Gemini a usar | `gemini-3.1-flash-lite` |
| `PROFILE_FILE` | Caminho do `profile.md` | `profile.md` |
| `TERMS_FILE` | Caminho do `search_terms.txt` | `search_terms.txt` |
| `LINKEDIN_TERMS_FILE` | Caminho da lista curta do LinkedIn | `search_terms_linkedin.txt` |
| `TELEGRAM_ADMIN_IDS` | User IDs que podem mandar comando. Vazio = desligado | — |
| `TIMEZONE` | Fuso do relatório diário | `America/Sao_Paulo` |
| `REPORT_HOUR` | Hora do relatório diário | `22` |
| `REPORT_TO` | Destino do relatório: `grupo` ou `privado` | `grupo` |

> Para gerar `GEMINI_API_KEY`: <https://aistudio.google.com/apikey> → "Create API key".

Um `INTERVAL_*` nunca desce abaixo de 60 segundos, mesmo que você peça menos —
valor menor que isso só serve para tomar bloqueio.

### Termos de busca

O ONM entrega todas as vagas recentes e o `profile.md` decide o resto. As outras
fontes precisam de **termo de busca**, e há duas listas:

- [`search_terms.txt`](search_terms.txt) — usada por **Gupy e Indeed** (~23 termos)
- [`search_terms_linkedin.txt`](search_terms_linkedin.txt) — usada só pelo
  **LinkedIn**, curta de propósito, porque lá cada vaga custa duas requisições

Uma linha por termo, `#` é comentário. Igual ao `profile.md`: editar não exige
mexer em código, e rodando local o arquivo é relido sem restart.

Cada termo é uma requisição por ciclo em cada fonte. Adicionar 10 termos é
adicionar 10 requisições por ciclo, em cada plataforma — vale pesar antes.

> No Indeed, o filtro de remoto da própria plataforma **não funciona** (a busca
> volta igual com e sem ele). Por isso o bot anexa "home office" a cada termo:
> medido em 11/08, "assistente" trouxe 0 vagas remotas em 20 e "assistente home
> office" trouxe 19 em 20.

### ⚠️ Cotas do free tier do Gemini

O free tier tem **dois tetos**: requisições por minuto e **por dia** — e o teto
diário é o que morde. Medido em 11/08/2026:

| Modelo | Teto diário observado | Serve? |
| ------ | --------------------- | ------ |
| `gemini-3.6-flash` | **20/dia** | Não — estoura antes de um dia de vagas do ONM |
| `gemini-3.1-flash-lite` | não atingido (15 seguidas, sem rate limit) | Em teste |

Cada modelo tem cota própria, então trocar `GEMINI_MODEL` reseta o teto.

Quando a cota diária estoura, o bot **não para** — passa a notificar tudo sem
filtro (fallback seguro) e registra isso de forma explícita:

```text
COTA DIARIA DO GEMINI ESGOTADA (limite=20/dia, model=...)
FILTRO (hoje 2026-08-11): 42 classificadas, 3 rate-limits, 8 vagas notificadas SEM FILTRO
```

O contador de **vagas sem filtro** é o número a acompanhar: zero significa que a
cota está aguentando o volume. O resumo diário também é gravado em
`quota_log.jsonl` (junto do `seen_ids.json`, no volume), para consulta posterior:

```bash
docker exec av-jobs-bot cat /app/data/quota_log.jsonl
```

Se o volume passar do que o free tier aguenta, a saída é um modelo pago —
`claude-haiku-4-5` custa cerca de US$ 5,70/mês a 50 vagas/dia.

## Deploy via Docker

```bash
docker compose up -d --build
```

O `seen_ids.json` é persistido no volume `bot-data`, sobrevivendo a restarts.

## Deploy via Coolify (Hostinger)

1. Push do código para o repositório privado no GitHub
2. No Coolify: New Resource → Docker Compose → conectar ao repo
3. Adicionar as variáveis de ambiente: `ONM_EMAIL`, `ONM_PASSWORD`,
   `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `GEMINI_MODEL`,
   `CHECK_INTERVAL=600` e `LOG_LEVEL=INFO`. As de fonte (`SOURCES`,
   `INTERVAL_*`) têm default no compose e só precisam entrar se você quiser
   mudar o padrão
4. Deploy

> **Não cadastre `DATA_DIR` no painel.** Ele já está fixo no `docker-compose.yml`
> apontando para o volume. Sobrescrever faz o bot perder o `seen_ids.json` a cada
> redeploy e reenviar todas as vagas para o grupo.

### Acesso ao repositório e deploy automático

São duas coisas separadas, e é fácil confundir:

- **Deploy key** (GitHub → Settings → Deploy keys): permite ao Coolify **ler** o
  repositório na hora de clonar. Read-only basta.
- **Webhook** (GitHub → Settings → Webhooks): faz o GitHub **avisar** o Coolify
  que houve push, disparando o redeploy. Sem ele, todo deploy é manual.

O webhook aponta para `/webhooks/source/github/events/manual` na URL do Coolify,
com content type `application/json` e o segredo que o Coolify gera por
aplicação. Só o evento de *push* é necessário.

### O que sobrevive a um redeploy

O `seen_ids.json`, o `bot_state.json` (fontes pausadas e dia do último
relatório), o `skipped_jobs.jsonl` e o `quota_log.jsonl` — todos ficam no volume
`bot-data`, fora da imagem.

Já o `profile.md` e os arquivos de termos são **embutidos na imagem**. Ajustar o
filtro em produção é editar, commitar e pushar; o redeploy pega a nova versão.

## Stack

- Python 3.12 (a mesma da imagem Docker — use 3.12 local também)
- `requests` + `python-dotenv`
- `google-genai` (Gemini Flash para classificação das vagas)
- `python-jobspy` (Indeed; puxa `pandas` junto)
- Docker / docker-compose

Dois arquivos de código: [`sources.py`](sources.py) sabe falar com cada
plataforma e devolve vagas num formato único; [`main.py`](main.py) é o pipeline
e não sabe de onde a vaga veio. Sem frameworks de agentes e sem banco de dados.
