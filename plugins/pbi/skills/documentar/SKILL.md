---
name: documentar
description: Gera a documentação de um relatório do Power BI (PBIX, PBIT ou PBIP, inclusive do Report Server) em markdown, com fontes, tabelas, medidas explicadas, relações, páginas e visuais, e propõe descrições para as medidas e tabelas do modelo. Use quando pedirem para documentar um relatório ou modelo, criar um dicionário de dados ou de medidas, ou preencher descrições.
argument-hint: "[arquivo] [onde salvar, opcional]"
allowed-tools: Bash(uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *) Bash("${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *)
---

# Documentar um relatório

## 1. Ler

Com uv (sem uv, troque `uv run --script` por `"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python"`):

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" info "<arquivo>"
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" linhagem "<arquivo>"
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" modelo "<arquivo>"
```

## 2. Escrever o documento

Pergunte onde salvar. A sugestão é um arquivo `<nome do relatório>.md` ao lado do relatório, para ir junto no Git. Use esta estrutura, em português:

1. **Visão geral:** o que o relatório mostra, para quem, de qual Desktop veio e a data da documentação.
2. **Fontes de dados:** servidor, banco e objeto de origem de cada tabela (do M), e os passos de transformação em uma linha cada.
3. **Modelo:** as tabelas, cada uma com o papel (fato, dimensão, ponte), as relações (cardinalidade, direção, se está ativa) e um diagrama em Mermaid (`erDiagram`) se ajudar.
4. **Medidas:** uma linha por medida, com nome, o que calcula em português, a fórmula DAX, o formato e em quais páginas é usada.
5. **Páginas:** para cada página, os visuais e o que cada um mostra.
6. **Pontos de atenção:** o que a `linhagem` aponta como sem uso, e qualquer coisa que um leitor precise saber para confiar nos números. Para uma revisão completa, indique o agente `revisor`.

Não invente o significado de negócio. Quando o nome não deixar claro, escreva "o nome sugere…" e marque para a pessoa confirmar.

## 3. Descrições no modelo (opcional)

Proponha uma descrição de uma linha para cada medida e tabela sem descrição, e mostre a lista para a pessoa aprovar. Depois:
- **com o Desktop aberto e o MCP `powerbi`:** `measure_operations` e `table_operations` com `Update` e o campo `description`. Depois, Ctrl+S (num PBIT, exportar de novo);
- **num PBIP com o projeto fechado no Desktop:** escreva as linhas `/// descrição` direto nos `.tmdl` das tabelas, acima de cada medida e de cada tabela, como no passo de gravar da skill `medida`;
- **sem MCP:** entregue as linhas `/// descrição` para a pessoa pôr acima de cada medida ou tabela na TMDL view (arrastar a tabela, editar, Visualizar e Aplicar).

As descrições aparecem para quem passa o mouse sobre o campo no Desktop e ajudam o próximo raio-x.
