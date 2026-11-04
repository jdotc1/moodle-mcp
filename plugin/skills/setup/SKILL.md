---
name: setup
description: Configurar ou diagnosticar a conexão com o Moodle (token, URL, rede/VPN). Use quando o usuário disser que o Moodle não conecta, pedir para configurar, ou quando uma ferramenta falhar com not_configured, token_invalid ou network_restriction.
---

# Configurar e diagnosticar o Moodle

1. Chame a ferramenta `moodle_status` e leia o resultado.
2. Interprete:
   - `not_configured`: o usuário ainda não preencheu a configuração do plugin. Explique: abrir `/plugin`, escolher este plugin e preencher **Moodle URL**, **Timezone** e **Moodle token**. O token é criado em `<URL do Moodle>/user/managetoken.php` (serviço *Moodle mobile web service*). Ele é guardado no armazenamento seguro do sistema.
   - `token_invalid`: o token foi revogado ou expirou; criar outro e atualizar a configuração do plugin.
   - `network_restriction`: a instituição pode exigir VPN ou restringir acesso externo. Nunca tente contornar; peça para conectar na VPN oficial e tentar de novo.
   - serviço Attendance/VPL com identidade desconhecida: explique que o serviço não revela o usuário; se o token é do próprio usuário, ele pode declarar isso no campo "Services whose token is yours" (ex.: `attendance`). Registre que é uma declaração dele, não uma prova.
3. **Nunca peça nem aceite o token no chat.** Se o usuário colar um token, avise que ele foi exposto e deve ser trocado em `managetoken.php`.
4. Termine dizendo quais recursos estão disponíveis (`capabilities_matrix`) e o que ainda não foi validado.
