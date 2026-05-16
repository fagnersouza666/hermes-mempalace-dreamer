# Hermes MemPalace Dreamer

[English](README.md) · **Português do Brasil**

[![tests](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml/badge.svg)](https://github.com/fagnersouza666/hermes-mempalace-dreamer/actions/workflows/test.yml)

Pacote de "sonho" (dreaming) e higiene de memória, com o MemPalace em primeiro lugar, para o [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Este repositório é um MVP público em fase inicial. Ele **não** instala nem altera uma configuração real do Hermes ainda. Ele entrega o primeiro scaffold seguro de um plugin cuja função é fazer a consolidação de memória do Hermes usar o MemPalace como camada principal de memória semântica, em vez de inflar os arquivos internos `MEMORY.md` / `USER.md`.

## O que ele faz hoje

Partes já implementadas:

- Metadados do plugin Hermes em `plugin.yaml`.
- Ponto de entrada do plugin em `__init__.py`.
- Registra uma skill fornecida pelo plugin:
  - `skills/mempalace-dreaming/SKILL.md`
- Registra comandos de CLI:
  - `hermes mempalace-dreaming setup-plan` (sempre somente relatório)
  - `hermes mempalace-dreaming setup` (dry-run por padrão, `--apply` opcional)
- Fornece um planejador de setup em modo dry-run:
  - `build_setup_plan(...)`
- Fornece uma camada de aplicação explícita:
  - `mempalace_dreaming/setup.py` (`build_config_commands`, `apply_setup_plan`);
  - criação de diretórios e `hermes config set ...` só ocorrem com `--apply`;
  - efeitos colaterais são injetados (`mkdir_fn` / `run_fn`) e testados unitariamente;
  - comandos de config são listas argv, executadas via `subprocess` sem shell;
  - o agendamento permanece planejado/somente relatório — **nenhum cron real é criado ainda**;
  - notas de rollback são incluídas no resultado.
- Entrega um MVP de engine de dreaming puro e sem dependências:
  - `mempalace_dreaming/engine.py` (minerar → pontuar → filtrar → deduplicar → memorizar);
  - testável sem o runtime do Hermes; `search_fn` / `remember_fn` são injetados;
  - rejeita conteúdo temporário/de progresso e segredos, mantém fatos duráveis.
- Inclui testes para:
  - registro do plugin;
  - contrato da skill;
  - conteúdo do plano de setup;
  - saída JSON da CLI;
  - comportamento da engine de dreaming.

O `setup-plan` apenas imprime um plano em JSON. O `setup` usa por padrão o
mesmo JSON dry-run; com a flag explícita `--apply` ele cria os diretórios
planejados e executa os comandos `hermes config set ...`. Mesmo com `--apply`,
o setup intencionalmente **não** cria cron jobs, não instala o MemPalace, não
escreve no Obsidian e não grava nenhuma memória.

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
- Sem criação automática de cron (o modo apply ainda não cria cron).
- Sem escrita no Obsidian.
- Sem gravação de memória durante o setup.
- Sem fallback para a memória interna no caso de fatos duráveis normais.
- Fallback de backend desconhecido é somente para relatório (report-only).
- Exclusão/compactação de memória deve ser explícita e aprovada pelo usuário.
- Nenhum segredo é armazenado.

## Status

Scaffold de MVP: utilizável como base de design/teste, ainda **não** pronto para produção. O setup/apply ainda não está pronto para produção.

Veja o [`ROADMAP.md`](ROADMAP.md) para os próximos passos de implementação.
