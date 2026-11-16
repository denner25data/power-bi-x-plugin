---
name: padroes
description: Padrões da equipe para relatórios Power BI. Traz nomes de medidas e tabelas, formatos, regras de DAX e de M, fontes aprovadas e o glossário de indicadores. As skills medida e documentar e o agente revisor seguem estes padrões. Use ao criar, revisar ou documentar medidas, tabelas e consultas. Este é um EXEMPLO genérico; cada equipe troca pelo seu.
---

# Padrões da equipe (exemplo)

> **Este arquivo é um exemplo.** Copie o plugin e troque o conteúdo pelas regras da sua equipe (veja "Copiar e personalizar" no README). As regras daqui valem mais que as boas práticas gerais quando as duas se contradizem.

## Nomes

- **Medidas:** em português, com espaço e sem prefixo (`Vendas`, `Ticket Médio`, `Margem %`). Porcentagem termina em `%`. Contagem começa com `Qtd`.
- **Tabelas:** fatos começam com `f` (`fVendas`) e dimensões com `d` (`dCliente`, `dCalendario`), sem espaço.
- **Colunas:** como vêm do banco. Renomeie só as que aparecem para o usuário final.
- **Pastas de medidas** (`displayFolder`): `Vendas`, `Custos`, `Clientes`, `Tempo`.

## Formatos

| Tipo | Formato |
|---|---|
| moeda | `"R$" #,0.00` |
| porcentagem | `0.0%` |
| quantidade | `#,0` |
| data | `dd/mm/yyyy` |

Toda medida tem formato e uma descrição de uma linha.

## DAX

- `DIVIDE` em toda divisão, nunca `/`.
- `VAR` para repetir partes da fórmula.
- Nada de valor fixo (ano, meta, código) na fórmula. Use uma tabela de parâmetros ou de metas.
- Agregação é medida, não coluna calculada.
- Medidas de tempo usam a `dCalendario`, marcada como tabela de datas, e não a data/hora automática, que fica desligada.

## Modelo e M

- Relações de muitos para um, em direção única. Bidirecional só com justificativa escrita na descrição da relação.
- As consultas devem mandar o trabalho para o banco (folding). Passos que quebram o folding vão para o fim ou para uma view no banco.
- No SQL Server, `Sql.Database(servidor, banco, [CreateNavigationProperties=false])`.
- Colunas que nenhum visual usa saem no Power Query.

## Fontes aprovadas (exemplo)

| Servidor | Banco | Para quê |
|---|---|---|
| `sql-dw.exemplo.local` | `DW` | dados oficiais (fatos e dimensões) |
| `sql-stage.exemplo.local` | `Stage` | só para testes; não publicar relatórios apontando para cá |

## Glossário de indicadores (exemplo)

| Indicador | Definição | Medida |
|---|---|---|
| Vendas | Valor vendido, sem impostos e sem frete | `SUM ( fVendas[ValorVenda] )` |
| Ticket médio | Vendas divididas pela quantidade de pedidos distintos | `DIVIDE ( [Vendas], [Qtd Pedidos] )` |
