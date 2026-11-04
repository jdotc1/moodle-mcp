---
name: faltas
description: Frequência — quantas faltas restam, se pode faltar em certa data e a quais aulas precisa ir. Use para perguntas sobre faltas, presença e limite de frequência.
---

# Faltas e frequência

Disciplina/pergunta: $ARGUMENTS

1. Descubra a disciplina (`list_courses`). Pergunte ao usuário a **regra de frequência mínima e a fonte** (plano de ensino) e a **carga horária total** se não vierem da configuração. **Nunca assuma 75%** como regra geral.
2. `attendance_report(course_id, min_frequency, rule_source, total_hours, hours_per_session)`. Se o Moodle não fornecer dados (identidade não comprovada, sem token), ofereça `attendance_import_manual` com as aulas que o usuário informar.
3. Responda com: faltas registradas, **chamadas pendentes**, margem (e margem se as pendentes virarem falta), horas ainda necessárias, quantos encontros pode perder, encontros críticos, e o cenário de datas pedidas.
4. Deixe claro: estimativa a partir dos dados disponíveis, não o registro oficial; provas e atividades obrigatórias só entram se listadas.
