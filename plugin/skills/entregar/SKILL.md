---
name: entregar
description: Enviar uma atividade no Moodle (upload, rascunho, envio e evidência). Use quando o usuário pedir explicitamente para entregar/enviar um trabalho.
---

# Entregar uma atividade

Atividade/arquivos: $ARGUMENTS

1. Identifique a atividade (`list_deadlines` ou `list_assignments`); se houver ambiguidade, pergunte só o que falta. Nunca invente atividade, grupo ou arquivos.
2. `prepare_submission(assignment_id, files)`; se `problems` não estiver vazio, corrija e repita.
3. Se a atividade exige declaração de autoria (`requires_statement`), mostre-a e peça confirmação **do usuário**; só então use `accept_statement=true`. Nunca marque por inferência.
4. Pedido explícito de envio ⇒ `deliver_assignment` (uma vez). Se o resultado for ambíguo (`write_outcome_unknown`), **não reenvie**: use `submission_status` e `list_operations` para reconciliar.
5. Relate a evidência lida de volta (estado, arquivos, horário). "Rascunho salvo" não é "entregue".
Observação: escritas ainda não foram validadas num Moodle real; diga isso se o usuário perguntar sobre confiabilidade.
