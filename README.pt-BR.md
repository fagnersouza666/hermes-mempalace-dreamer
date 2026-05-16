# Hermes MemPalace Dreamer

[English](README.md) · **Português do Brasil**

[![tests](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml/badge.svg)](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml)

Pacote de "sonho" (dreaming) e higiene de memória, com o MemPalace em primeiro lugar, para o [Hermes Agent](https://github.com/NousResearch/hermes-agent).

**Bootstrap pronto para produção v1.0.** É uma camada de bootstrap e
orquestração honesta e segura para ambientes que **já possuem um provider
MemPalace do Hermes disponível**. Entrega uma superfície segura funcional
(planejamento de setup, apply explícito e opcional, criação de cron
explícita e opcional, verificação pós-apply explícita e opcional, `status`
e `verify-runtime` somente leitura) e uma engine de dreaming pura, sem
dependências. Ele **não** instala o pacote do provider MemPalace em si —
isso permanece externo/pré-existente e específico do ambiente. Nunca escreve
no Obsidian e nunca grava memória durante setup ou verificação. Todo efeito
colateral é explícito e injetado por dependência.

Sua função é fazer a consolidação de memória do Hermes usar o MemPalace como camada principal de memória semântica, em vez de inflar os arquivos internos `MEMORY.md` / `USER.md`.

## O que ele faz hoje

Partes já implementadas:

- Metadados do plugin Hermes em `plugin.yaml`.
- Ponto de entrada do plugin em `__init__.py`.
- Registra uma skill fornecida pelo plugin:
  - `skills/mempalace-dreaming/SKILL.md`
- Registra comandos de CLI:
  - `hermes mempalace-dreaming setup-plan` (sempre somente relatório)
  - `hermes mempalace-dreaming setup` (dry-run por padrão; `--apply`
    opcional; `--create-cron` e `--verify-after-apply` explícitos e opcionais)
  - `hermes mempalace-dreaming status` (JSON somente leitura: versão, módulos, flags de segurança)
  - `hermes mempalace-dreaming verify-runtime` (verificação ao vivo somente leitura do ambiente; sem efeitos colaterais)
  - `hermes mempalace-dreaming schedule-plan` (JSON somente relatório; nunca cria cron)
  - `hermes mempalace-dreaming lean-check` (JSON somente relatório; classifica material candidato local, sem gravações)
  - `hermes mempalace-dreaming doctor` (auditoria operacional somente leitura: presença do plugin, provider de memória, coerência de configuração, estado do cron, detecção de duplicatas e desvio de timezone; nunca muta nada)
- Fornece um planejador de setup em modo dry-run:
  - `build_setup_plan(...)`
- Fornece uma camada de aplicação explícita:
  - `mempalace_dreaming/setup.py` (`build_config_commands`, `apply_setup_plan`);
  - criação de diretórios e `hermes config set ...` só ocorrem com `--apply`;
  - efeitos colaterais são injetados (`mkdir_fn` / `run_fn` /
    `schedule_fn` / `verify_fn`) e testados unitariamente/integração;
  - comandos de config e de cron são listas argv, executadas via
    `subprocess` sem shell;
  - a criação de cron é **explícita e opcional** (`--apply --create-cron`):
    argv determinístico de `hermes cron create`, nome de job fixo, prompt
    conservador autocontido, skill empacotada anexada, `--deliver local`
    para nunca transmitir a chats; sem `--create-cron` o agendamento
    permanece somente relatório;
  - o agendamento é **ciente de timezone**: `--time` é um horário de parede
    interpretado em `--timezone` (nome IANA, ex.: `America/Sao_Paulo`) e
    convertido para um cron em UTC, pois o agendador roda cron em UTC. O
    timezone padrão é **UTC** — não "horário local"; passe `--timezone`
    explicitamente para agendar em horário local. A saída mostra o horário e
    timezone solicitados e o cron UTC resultante; timezone inválido vira um
    aviso JSON, nunca um traceback;
  - a verificação pós-apply é **explícita e opcional**
    (`--apply --verify-after-apply`): checagem somente leitura embutida no
    JSON; é pulada se o apply falhou cedo;
  - o apply nunca lança exceção: a primeira ação que falha é capturada, para
    as demais e é reportada na lista `errors` do resultado (também no JSON);
  - notas de rollback são incluídas no resultado.
- Entrega um MVP de engine de dreaming puro e sem dependências:
  - `mempalace_dreaming/engine.py` (minerar → pontuar → filtrar → deduplicar → memorizar);
  - testável sem o runtime do Hermes; `search_fn` / `remember_fn` são injetados;
  - rejeita conteúdo temporário/de progresso e segredos, mantém fatos duráveis;
  - `render_report(report)` → resumo markdown determinístico;
  - `audit_retrieval_noise(results)` → classificação pura útil/ruído (sem gravar memória);
  - `build_lean_check_report(candidates, search_fn=…)` → JSON somente relatório classificando
    material candidato em durável / ruído / segredo / duplicado (segredos redatados, sem gravações).
- Inclui testes para:
  - registro do plugin;
  - contrato da skill;
  - conteúdo do plano de setup;
  - saída JSON da CLI;
  - comportamento da engine de dreaming.

O `setup-plan` apenas imprime um plano em JSON. O `setup` usa por padrão o
mesmo JSON dry-run; com a flag explícita `--apply` ele cria os diretórios
planejados e executa os comandos `hermes config set ...`. Adicionar
`--create-cron` (somente com `--apply`) cria o cron diário de dreaming via
`schedule_fn` injetado, usando a expressão cron **em UTC** convertida a
partir de `--time`/`--timezone`; adicionar `--verify-after-apply` roda a
checagem somente leitura depois. Se uma ação falha sob `--apply`, o setup
para na primeira falha e a reporta no campo `errors` do JSON. Mesmo com
todas as flags, o setup intencionalmente **não** instala o pacote do
provider MemPalace, não escreve no Obsidian e não grava nenhuma memória.

## Direção pretendida

O componente final deve se tornar um plugin Hermes de instalação única para:

- dreaming de memória com MemPalace em primeiro lugar;
- rotinas de higiene de memória / verificação de enxugamento (lean-check);
- cron diário de dreaming opcional;
- configuração segura de `memory.provider: mempalace`;
- integração com um provider MemPalace do Hermes;
- adaptação clean-room de ideias de projetos existentes de memory-dreaming.

## Referências de design

Este projeto pega ideias emprestadas, não código, de:

- Pluton: pipeline de sonho `MINE -> CURATE -> COMPRESS -> CONTEXT`.
- `nexus9888/hermes-memory-skills`: estrutura Light/Deep/REM e disciplina de lean-check.
- Plugins de provider MemPalace do Hermes: conceitos de provider nativo, prefetch, diário e grafo de conhecimento.

Nenhum texto de skill de terceiros é incorporado aqui até que licença e atribuição estejam explícitas.

## Instalação

Quando publicado e suportado pela sua versão do Hermes:

```bash
hermes plugins install fagnersouza666/hermes-mempalace-dreamer --enable
hermes mempalace-dreaming setup-plan --schedule-dreaming
```

Para desenvolvimento local:

```bash
git clone https://github.com/fagnersouza666/hermes-mempalace-dreamer.git
cd hermes-mempalace-dreamer
python3 -m pytest tests -q
```

## Política de segurança atual

- Sem alteração de configuração sem a flag explícita `setup --apply` (padrão é dry-run).
- Sem criação de cron sem as flags explícitas `setup --apply --create-cron`;
  o cron criado usa a conversão UTC de `--time`/`--timezone` (timezone
  padrão é UTC, nunca silenciosamente "horário local").
- Sem verificação pós-apply sem a flag explícita `--verify-after-apply`,
  e ela é pulada se o apply falhou cedo.
- Sem escrita no Obsidian.
- Sem gravação de memória durante o setup ou a verificação.
- Sem fallback para a memória interna no caso de fatos duráveis normais.
- Fallback de backend desconhecido é somente para relatório (report-only).
- Exclusão/compactação de memória deve ser explícita e aprovada pelo usuário.
- Nenhum segredo é armazenado.

## Status

**Bootstrap pronto para produção v1.0.** Planejamento seguro de setup,
apply explícito e opcional, criação explícita e opcional de cron,
verificação pós-apply explícita e opcional, `status` / `verify-runtime`
somente leitura, `schedule-plan` somente relatório e uma engine de dreaming
pura estão implementados e cobertos por testes unitários + testes de
integração contra um Hermes Home isolado.

**Escopo de “pronto para produção”:** esta é uma camada de bootstrap e
orquestração pronta para produção. Ela pressupõe que um provider MemPalace do
Hermes **já exista** no ambiente — instalar esse pacote/provider continua
sendo externo e deliberadamente fora de escopo.

O agendamento agora é **ciente de timezone**: o cron criado usa a conversão
UTC de `--time`/`--timezone`. O timezone padrão é **UTC**; para horário local,
passe `--timezone` explicitamente e confira `cron_utc` na saída JSON.

Veja [`docs/USAGE.md`](docs/USAGE.md) para comandos e modelo de segurança,
[`CHANGELOG.md`](CHANGELOG.md) para a entrada v1.0.0, e
[`ROADMAP.md`](ROADMAP.md) para o que está e o que não está pronto.
