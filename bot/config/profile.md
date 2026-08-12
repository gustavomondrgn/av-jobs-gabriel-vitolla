# Perfil do Filtro — AV Jobs · Gabriel Vitolla

> Este arquivo é lido pelo bot e injetado no prompt do classificador.
> Edite livremente em português. Quanto mais concreto e com exemplos, melhor.
> Não precisa de formato — texto corrido funciona. Salvar o arquivo já basta;
> não precisa rebuildar nada (rodando local).

## Para quem é o filtro

O público são **Assistentes Virtuais** — alunos do curso de AV / secretariado
remoto do Gabriel Vitolla. Na prática essa galera não faz só assistência
virtual: acumula papéis de atendimento, administrativo, financeiro e vendas.
Por isso o filtro é amplo dentro desse leque.

**A prioridade é sempre assistência virtual.** As outras categorias entram
porque vaga específica de "assistente virtual" é rara demais para sustentar o
volume do grupo sozinha.

## ⚠️ REGRA OBRIGATÓRIA 1 — trabalho remoto DECLARADO

A vaga precisa **dizer** que é remota, home office ou à distância.

- Vaga **presencial** → `irrelevant`, não importa o quanto a função encaixe.
- Vaga **híbrida** → `irrelevant` também.
- Vaga que **não diz nada sobre modalidade** → `work_mode: "nao_informado"`, e
  o bot descarta. **Mudou em 12/08:** antes a omissão era tolerada, agora não é
  mais. O Gabriel só quer anunciar o que ele consegue garantir que é remoto.
- Sinais de presencial: exige comparecer ao escritório, exige morar em cidade
  ou região específica, "presencial", "híbrido", "modelo híbrido", "X dias no
  escritório", endereço da empresa como local de trabalho, "atendimento no
  balcão", "recepção da clínica" (presencial).

Importante para o `work_mode`: continue devolvendo **exatamente o que o texto
diz**. Não force "remoto" para salvar uma vaga boa, e não force "nao_informado"
quando o texto declara remoto. Quem descarta é o código; seu trabalho é ler
direito.

PJ, freelancer, prestador de serviço, part-time e por demanda são modalidades
bem-vindas — mas **não** são obrigatórias. CLT remoto também serve.

## ⚠️ REGRA OBRIGATÓRIA 2 — nada em inglês

O **anúncio** precisa estar escrito em português. Vaga com a descrição em inglês
é descartada, ainda que a função encaixe perfeitamente.

Cuidado com a distinção: vaga escrita em português que **exige** inglês do
candidato continua sendo `language: "pt"`. Ela não é descartada por idioma — só
perde pontos na nota.

## ⚠️ REGRA OBRIGATÓRIA 3 — nada sênior

Vaga posicionada como **sênior** é descartada. Vale para o título ("Assistente
Executiva Sênior") e para o texto que exige 5+ anos na função ou liderança de
equipe.

Não confunda: vaga júnior/pleno que vai *dar apoio a* um executivo ou a uma
diretoria sênior **interessa** normalmente. O que importa é a senioridade
exigida da pessoa contratada.

## O que INTERESSA (RELEVANTE)

Todas as categorias abaixo, **desde que remotas**:

### 1. Secretariado e Assistência (prioridade máxima)

Assistente Virtual · Assistente Remoto · Secretária Remota · Secretária
Virtual · Assistente Administrativo · Assistente Executivo · Assistente
Pessoal.

### 2. Atendimento

Atendente Remoto · Assistente de Atendimento · Atendimento ao Cliente ·
Suporte ao Cliente · Atendimento via WhatsApp · Recepcionista Remota.

### 3. Comercial e Vendas

Assistente Comercial · Atendente Comercial · SDR · BDR · Closer · Inside
Sales · Assistente de Vendas.

### 4. Administrativo

Assistente Administrativo · Auxiliar Administrativo · Analista Administrativo ·
Backoffice · Assistente de Cadastro · Assistente de Processos.

### 5. Financeiro

Assistente Financeiro · Auxiliar Financeiro · Assistente Administrativo
Financeiro · Contas a Pagar · Contas a Receber · Assistente de Cobrança ·
Assistente de Faturamento.

### 6. Agenda e Clínicas

Secretária de Consultório · Assistente de Consultório · Assistente de
Agendamento · Atendimento de Clínica · Appointment Setter.

> Atenção: nesta categoria é comum a vaga ser presencial. Só entra se for
> remota (secretária de consultório trabalhando de casa, agendamento online).

### 7. Customer Success

Customer Success · Assistente de Customer Success · Assistente de Pós-venda ·
Assistente de Relacionamento · Customer Support.

## Variações de escrita

As descrições têm grafia inconsistente, abreviação e erro de digitação. Trate
como equivalentes:

- "assistente virtual" = "AV" = "assistente vitual" = "virtual assistant"
- "secretária remota" = "secretaria remota" (sem acento) = "secretariado
  remoto" = "secretária online"
- "SDR" = "S.D.R." = "pré-vendas" = "prospecção ativa" = "hunter"
- "closer" = "vendedor closer" = "fechamento de vendas"
- "backoffice" = "back office" = "back-office" = "retaguarda"
- "customer success" = "CS" = "sucesso do cliente"
- "home office" = "homeoffice" = "home-office" = "remoto" = "à distância" =
  "trabalho a distancia" (sem acento)
- "appointment setter" = "agendador" = "agendamento de reuniões"

Considere sempre o **título, a profissão/área, as skills E a descrição**. Às
vezes a descrição é vaga mas a categoria denuncia que é da área.

## Volume: o bot publica poucas vagas por dia

Desde 12/08 o bot **não publica tudo que aprova**. As vagas aprovadas entram numa
fila e só as de maior nota são publicadas — um punhado por dia. Isso muda o que
se espera de você:

**A nota (`score`) importa tanto quanto a classificação.** Aprovar uma vaga
mediana não é mais inofensivo: ela agora *compete* com uma vaga boa e pode tomar
o lugar dela. Continue mandando `borderline` quando houver dúvida — mas dê a ela
a nota baixa que ela merece, para que ela só saia num dia fraco.

## O que NÃO interessa (IRRELEVANTE)

- **Qualquer vaga presencial ou híbrida** — mesmo que a função seja perfeita.
- **Qualquer vaga que não declare ser remota.**
- **Qualquer vaga com o anúncio escrito em inglês.**
- **Qualquer vaga sênior.**
- **Vagas técnicas especializadas**: desenvolvimento de software, programação,
  DevOps, análise de dados, design gráfico, edição de vídeo, motion,
  ilustração, arquitetura, engenharia.
- **Profissões regulamentadas / de formação específica**: advocacia, medicina,
  enfermagem, contabilidade (contador registrado), psicologia, nutrição,
  fisioterapia. Atenção: a *secretária* de um escritório de advocacia ou de uma
  clínica **interessa** (se remota) — quem não interessa é o advogado, o
  médico etc.
- **Gestão de tráfego pago, SEO, copywriting, social media** como função
  principal da vaga.
- Aulas particulares, consultoria especializada, mentoria.
- Vagas operacionais que exigem presença física por natureza: motorista,
  estoquista, produção, obra, entrega, evento, fotografia.
- Cargos de gestão sênior que não são função de apoio (gerente, diretor, head,
  coordenador com equipe grande).

## Em caso de dúvida

Se não tiver certeza (descrição vaga, ambígua, pode encaixar ou não),
**classifique como BORDERLINE**. É melhor mandar uma notificação a mais e o
grupo descartar do que perder uma oportunidade.

Casos típicos de BORDERLINE:

- Função de apoio administrativo/atendimento que não está literalmente na lista
  acima, mas é claramente do mesmo leque.
- Descrição curta ou genérica ("procuro alguém para me ajudar com as tarefas",
  "preciso de alguém para organizar minha agenda") em que o título ou a
  categoria sugerem apoio remoto.
- Vaga que encaixa na função mas não deixa claro se é remota.
