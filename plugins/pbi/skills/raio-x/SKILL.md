---
name: raio-x
description: Explica um relatório do Power BI (PBIX, PBIT ou PBIP), inclusive do Power BI Report Server, de cada página e visual até as medidas, as colunas, as consultas M e as tabelas de origem no banco. Só lê o arquivo, sem abrir o Desktop. Use quando pedirem para entender, explicar ou mapear um relatório, dizer de onde vem um número ou um visual, ou descobrir o que um relatório antigo faz.
argument-hint: "[arquivo .pbix, .pbit ou .pbip] [página, visual ou medida, opcional]"
allowed-tools: Bash(uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *) Bash("${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *)
---

# Raio-x de um relatório

Esta skill **só lê**. Nunca grava no arquivo nem abre o Desktop.

## 1. Ler o arquivo

Rode o script do plugin. Com uv:

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" <subcomando> "<arquivo>"
```

Sem uv (instalado pela skill `configurar` com pip):

```bash
"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" <subcomando> "<arquivo>"
```

Se o `uv` não existir e o ambiente do pip também não, ou se o script disser que falta o PBIXRay, pare e chame a skill `configurar`.

Na ordem:

1. **`info`**: o tipo do arquivo, de qual Desktop veio (Report Server ou normal) e o formato do relatório.
2. **`linhagem`**: é a base da explicação. Para cada visual, traz o campo, as medidas que ele usa (inclusive as indiretas), as colunas e, no fim, cada medida com a fórmula, cada tabela com a origem no banco e os passos do M, as relações e o que nenhum visual usa.
3. Só se precisar de mais detalhe: **`modelo`** (todas as colunas e o M inteiro), **`relatorio`** (posição e título dos visuais) ou `--json` em qualquer um deles.

Se o arquivo não tiver o modelo (PBIX com conexão ao vivo a um modelo publicado), o script avisa. Explique o relatório e diga que o modelo está no servidor.

## 2. Explicar

Escreva em português, para um analista que vai manter o relatório. Se a pessoa perguntou por uma página, um visual ou uma medida, explique só aquele caminho, do visual até a tabela no banco. Senão, siga esta estrutura:

1. **Resumo**, em 3 a 5 linhas: o que o relatório mostra, quantas páginas e visuais, de qual Desktop veio, e de qual servidor e banco vêm os dados.
2. **Página por página.** Uma linha por visual, em termos de negócio: "colunas de Vendas por ano", "segmentação por país". Diga o tipo do visual e os campos. Não use o identificador hexadecimal do visual, que não diz nada a quem lê.
3. **Medidas.** Para cada medida usada: a fórmula DAX exatamente como está no arquivo, o que ela calcula em português simples e o formato.
4. **De onde vêm os dados.** Para cada tabela usada: o servidor, o banco e o objeto de origem (por exemplo `dbo.FactInternetSales`), e os passos do M explicados em uma linha cada. Se a tabela vem de uma consulta SQL escrita à mão, diga.
5. **Pontos de atenção**, no máximo 5 itens, só o que afeta a confiança nos números:
   - medida sem formato;
   - relação em ambas as direções (muda o que cada filtro alcança);
   - coluna calculada que agrega, e por isso não responde aos filtros;
   - campo de visual que não existe no modelo (visual quebrado);
   - medidas, colunas calculadas e tabelas que nenhum visual usa. Uma tabela marcada como "ponte" liga outras nas relações e não deve ser apagada.

   Para uma revisão completa, sugira o agente `revisor` do plugin.

## Regras

- Use os totais da linha "Totais" da linhagem. Não conte páginas, visuais ou medidas à mão.
- Não invente o significado de negócio. Quando um nome for obscuro, diga "o nome sugere" e pergunte se a pessoa sabe.
- Copie DAX e M sem reescrever. A explicação vem ao lado.
- Em relatório grande, comece pelo resumo e pergunte por qual página seguir, em vez de despejar tudo.
- Para conferir um número de verdade, ofereça rodar uma consulta DAX pelo MCP `powerbi`, com o arquivo aberto no Desktop.
- Se a pessoa quiser mudar algo, indique a skill certa: medidas na `medida`, visuais do Report Server na `visual`.
