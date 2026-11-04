# moodle-academic-mcp

Servidor [MCP](https://modelcontextprotocol.io) **comunitário e independente** que conecta a sua conta Moodle ao Claude Code, Codex, ChatGPT e outros clientes. Você usa **a sua IA** e **os seus tokens**: o servidor expõe dados e ações; o modelo do cliente interpreta, explica, escreve e gera arquivos. Nenhuma API de LLM paga pelo mantenedor é necessária.

> Projeto sem vínculo oficial com a UFSC, o Moodle, a Anthropic ou a OpenAI. O primeiro ambiente-alvo é o Moodle Presencial da UFSC; a arquitetura aceita outras instituições ([guia](docs/add-institution.md)).

[English version](README.en.md) · [Ferramentas](docs/tools.md) · [Matriz de recursos](docs/feature-matrix.md) · [Segurança](SECURITY.md)

## O que dá para pedir

- “Quais trabalhos tenho para entregar na próxima semana? Separe os já enviados.” → `list_deadlines`
- “Leia o enunciado, consulte os materiais e produza o trabalho.” → `get_assignment`, `read_material`, `search_materials`
- “Revise estes arquivos e envie minha atividade.” → `prepare_submission`, `deliver_assignment`
- “Entre no grupo 3 do trabalho, se houver vaga.” → `group_activities`, `join_group`
- “Liste os questionários, abra esta tentativa e envie minhas respostas.” → `list_quizzes`, `start_quiz_attempt`, `save_quiz_answers`
- “Quais notas já saíram? Quanto preciso tirar para passar?” → `get_grades`, `required_score`
- “Quantas faltas tenho? Posso faltar na terça?” → `attendance_report`
- “Mostre os avisos novos desde a última consulta.” → `changes_since`

55 ferramentas, 7 prompts MCP (`prepare_assignment`, `deliver_assignment_workflow`, `weekly_overview`, …). Escritas são separadas das leituras e anotadas (`readOnlyHint`, `destructiveHint`, `idempotentHint`); quem decide a política de confirmação é o seu cliente e você (veja [autonomia](docs/clients.md#política-de-confirmação)).

## Instalação rápida (modo local, stdio)

Requer Python 3.11+ e [uv](https://docs.astral.sh/uv/). O pacote **ainda não foi publicado no PyPI**; instale do GitHub ou de um wheel:

```bash
uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp version
# ou, clonando:
git clone https://github.com/matheussilvano/moodle-mcp && cd moodle-mcp && uv sync
```

1. Crie um token em `https://presencial.moodle.ufsc.br/user/managetoken.php` (serviço *Moodle mobile web service*; opcionalmente *Attendance* e *VPL web service*). Veja [tokens](docs/tokens.md).
2. `moodle-academic-mcp configure --profile ufsc --service mobile` (entrada oculta; guarda no keyring ou em arquivo 0600).
3. `moodle-academic-mcp doctor` — testa rede, token, versão do Moodle e quais recursos o seu serviço expõe.
4. Conecte ao cliente:

```bash
# Claude Code
claude mcp add --transport stdio moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve
# Codex
codex mcp add moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve
```

ChatGPT e GPTs personalizados precisam do [modo hospedado](docs/auth.md) (HTTPS público). Detalhes em [clientes](docs/clients.md).

## Estado da validação (honesto)

| Nível | O que foi feito |
| --- | --- |
| Testes automatizados | 65+ testes contra um **Moodle falso** (mock) e contra PostgreSQL 16 real; stdio e Streamable HTTP com cliente MCP real |
| Moodle real da UFSC (4.1.5+) | **somente leituras** com um token pessoal: `core_webservice_get_site_info`, cursos, tarefas/estado, calendário, lista de fóruns |
| Escritas (entrega, quiz, grupos, fórum) | **não testadas em Moodle real** (apenas mock); exigem um sandbox — veja [sandbox](docs/sandbox.md) |
| Attendance, VPL, Group choice | adaptadores escritos a partir do código upstream; **não validados** na UFSC |

Detalhes por recurso em [feature-matrix.md](docs/feature-matrix.md).

## Segurança e privacidade em uma tela

- Tokens nunca vão para logs, URLs, resultados ou para o modelo; `userprivateaccesskey` é removido.
- Downloads só falam com o host Moodle do perfil (redirecionamentos revalidados); no modo hospedado IPs privados/metadados são bloqueados.
- Texto de PDFs, fóruns e páginas é marcado como **não confiável**; não autoriza ações.
- Dados enviados ao modelo são processados pelo provedor que **você** escolheu, mesmo com o MCP rodando local.
- Veja [privacy.md](docs/privacy.md) para retenção, exportação e exclusão (`moodle-academic-mcp data export|purge`).

## Desenvolvimento

```bash
uv sync --all-extras && uv run pytest -q && uv run ruff check src tests && uv run mypy src
```

[CONTRIBUTING](CONTRIBUTING.md) · [Roadmap](docs/roadmap.md) · [Boas primeiras issues](docs/good-first-issues.md) · Licença MIT.
