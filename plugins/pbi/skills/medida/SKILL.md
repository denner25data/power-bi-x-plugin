---
name: medida
description: Cria ou ajusta uma medida DAX num modelo do Power BI (PBIX, PBIT ou PBIP, inclusive do Report Server), no padrão da equipe, com formato, descrição, casos de teste e uma consulta SQL para conferir o número na fonte. Grava pelo MCP do Power BI com o Desktop aberto, ou entrega o código para colar no Desktop. Use quando pedirem para criar, corrigir, otimizar ou explicar uma medida, ou trocar uma coluna calculada por medida.
argument-hint: "[o que a medida deve calcular] [arquivo, opcional]"
allowed-tools: Bash(uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *) Bash("${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *)
---

# Criar ou ajustar uma medida

## 1. Entender o modelo

Leia o modelo antes de escrever qualquer DAX (com uv; sem uv, troque `uv run --script` por `"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python"`):

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" modelo "<arquivo>"
```

Veja as tabelas, as colunas, as relações e as **medidas que já existem**. Reaproveite as medidas base (`[Vendas]`, `[Custo]`) em vez de repetir a fórmula, e não crie uma medida que já existe com outro nome.

Se houver uma skill de padrões da equipe (a `padroes` do plugin ou uma do projeto), siga os nomes, formatos, pastas e regras dela.

## 2. Escrever a medida

- Use `DIVIDE` em toda divisão, `VAR` para as partes repetidas, e nada de valor fixo no código (ano, código, meta), a menos que a pessoa peça.
- Defina o **formato** (moeda, porcentagem, inteiro com separador de milhar) e uma **descrição** de uma linha.
- Se for trocar uma coluna calculada que agrega por medida, mantenha a coluna até a pessoa trocar os visuais (skill `visual` no Report Server), e só então sugira apagar.

Mostre a medida e espere o ok antes de gravar.

## 3. Testar

Monte casos de teste em DAX:
- o total;
- por mês, incluindo um mês sem dado;
- um caso em que o total não é a soma das linhas (média, porcentagem, contagem distinta);
- com um filtro aplicado.

Com o Desktop aberto, rode-os pelo MCP (`dax_query_operations`, operação `Execute`). Sem MCP, entregue a consulta para colar na exibição de consulta DAX:

```dax
EVALUATE
SUMMARIZECOLUMNS ( 'DimDate'[CalendarYear], "Medida", [Nome da Medida] )
```

Escreva também uma **consulta SQL** que recalcula o número direto na fonte. A tabela e o banco estão no M (`modelo` mostra o `Sql.Database` e o `Schema.Item`). A pessoa roda essa consulta no banco e compara com o resultado do DAX.

## 4. Gravar

**Com o Desktop aberto e o MCP `powerbi` disponível** (o caminho padrão; funciona também no Desktop do Report Server, mas sem suporte oficial da Microsoft):

1. `connection_operations` com `ListLocalInstances`. Ache o Desktop pelo título da janela, que tem o nome do arquivo, e conecte com `Connect` e `connectionString` igual a `data source=localhost:<porta>`.
2. `measure_operations` com `Create` ou `Update`, passando `tableName`, `name`, `expression`, `formatString`, `description` e `displayFolder`.
3. Rode os casos de teste com `dax_query_operations`.
4. **Peça para a pessoa salvar no Desktop (Ctrl+S).** Nada chega ao arquivo antes disso. Num PBIT, ela precisa exportar o PBIT de novo (Arquivo → Exportar → Modelo do Power BI).

Se o MCP pedir a EULA, ou se não estiver configurado, mande para a skill `configurar`. Nunca aceite a EULA sem a pessoa pedir.

**Sem MCP, ou com o MCP em modo de leitura** (opção `modo_mcp`): entregue o código para colar.
- **Exibição de consulta DAX**, o mais simples:
  1. A pessoa cola o bloco abaixo e roda.
  2. Clica no link "Atualizar modelo: adicionar nova medida" (ou "Atualizar modelo com alterações").
  3. Ajusta o formato em Ferramentas de medida, porque o `DEFINE MEASURE` não leva o formato.

  ```dax
  DEFINE
      MEASURE 'FactInternetSales'[Ticket Médio] = DIVIDE ( [Vendas], [Qtd Pedidos] )
  EVALUATE
      ROW ( "Ticket Médio", [Ticket Médio] )
  ```
- **TMDL view**, que leva formato e descrição juntos:
  1. A pessoa arrasta a tabela para o editor.
  2. Você diz exatamente quais linhas acrescentar ou trocar no script dela: a linha `measure 'Nome' = …`, com `formatString:` e `displayFolder:` indentados abaixo, e a descrição em `///` na linha de cima.
  3. Ela clica em Visualizar e depois em Aplicar.

Os dois caminhos funcionam no Desktop do Report Server. Depois, Ctrl+S (ou exportar de novo, num PBIT).

## Regras

- Não grave sem mostrar a medida antes.
- Num PBIX, nunca grave no arquivo por fora: o modelo só muda pelo Desktop (MCP ou colando).
- Explique a medida em português simples, para o analista que vai mantê-la.
