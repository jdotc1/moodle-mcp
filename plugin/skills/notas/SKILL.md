---
name: notas
description: Notas e simulação — o que já saiu, quanto precisa tirar para passar, cenários. Use para perguntas sobre média, aprovação, recuperação.
---

# Notas e simulação

Disciplina/pergunta: $ARGUMENTS

1. `get_grades(course_id)`: nota **não lançada ≠ zero**.
2. `grade_scheme_draft(course_id)` e ajuste a fórmula, o arredondamento, a nota mínima e a **fonte** conforme o plano de ensino (peça o trecho ao usuário; não invente a regra). Registre `source`.
3. `simulate_grades` e `required_score`; se houver itens ainda sem nota, o resultado é condicional — reporte os limites (pior/melhor caso) e use `fill_others` só com a hipótese declarada.
4. Mostre fórmula, hipóteses e fonte junto com o resultado. Não prometa aprovação.
