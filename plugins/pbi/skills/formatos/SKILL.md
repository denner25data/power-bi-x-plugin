---
name: formatos
description: Conhecimento sobre os arquivos do Power BI (PBIX, PBIT e PBIP) e sobre o Desktop normal e o do Report Server. Diz como reconhecer cada um, o que dá para ler e mudar em cada caso, por qual caminho, e o que nunca fazer. Use antes de decidir como ler ou mudar um arquivo do Power BI, ou quando perguntarem a diferença entre os formatos.
user-invocable: false
---

# Os arquivos do Power BI

## Reconhecer

O `pbi.py info` faz isso sozinho. As regras por trás:

| Sinal | O que é |
|---|---|
| `.pbip`, ou pastas `.Report` e `.SemanticModel` | PBIP: relatório em PBIR (pastas de JSON) e modelo em TMDL (texto). Só o Desktop normal salva |
| `.pbit` | modelo sem dados (`DataModelSchema`, JSON em UTF-16 LE) e o relatório |
| `.pbix` com `Report/Layout` e `Metadata` `"CreatedFrom":"OnPrem"` | PBIX do Desktop do Report Server, relatório no formato antigo |
| `.pbix` com `Report/definition/` e `Metadata` `"CreatedFrom":"Cloud"` | PBIX do Desktop normal, relatório em PBIR dentro do zip |

Abrir um PBIX antigo no Desktop normal e salvar converte o arquivo, e o Desktop do Report Server deixa de abri-lo.

## O que dá para fazer, e por onde

| Tarefa | PBIX do Report Server | PBIX do Desktop normal | PBIT | PBIP |
|---|---|---|---|---|
| Ler o relatório | `pbi.py` | `pbi.py` | `pbi.py` | `pbi.py` |
| Ler o modelo | `pbi.py` (PBIXRay), sem Desktop | igual | `pbi.py`, sem dados | `pbi.py` (TMDL) |
| Rodar DAX | MCP com o Desktop aberto | igual | não tem dados | MCP com o Desktop aberto |
| Mudar o modelo | MCP (não documentado no RS; funcionou nos testes) ou colar na consulta DAX ou na TMDL view | MCP ou colar | abrir no Desktop, mudar e exportar o PBIT de novo | aberto no Desktop: MCP. Fechado: editar os `.tmdl` |
| Mudar o M | MCP, e a pessoa atualiza a tabela; ou colar no Editor avançado | igual | abrir no Desktop, mudar e exportar o PBIT de novo | aberto no Desktop: igual ao PBIX. Fechado: editar a partição no `.tmdl` |
| Mudar visuais | skill `visual` (fluxo guiado) | fora desta versão | skill `visual`, se for do Report Server | fora desta versão |

## Nunca

- **Gravar direto num `.pbix` ou `.pbit`**, nem com `Write`, `Edit`, `cp`, `mv`, `zip`, redirecionamento ou script próprio. O hook do plugin bloqueia. O modelo muda pelo Desktop (MCP ou colando) e o relatório do Report Server, pelo fluxo da skill `visual`.
- **Editar um PBIT pensando que ele muda sozinho.** A Microsoft diz que editar um PBIT com ferramentas externas não é suportado. Abre-se o PBIT no Desktop, muda-se e exporta-se de novo.
- **Aceitar a EULA do MCP sem a pessoa pedir.**
- **Atualizar ou recalcular o modelo do Desktop pelo MCP** (`RefreshWithXMLA` ou `RefreshWithAPI`, em qualquer ferramenta), mesmo quando o MCP diz que precisa. A Microsoft não suporta comandos de processamento no modelo do Desktop. Quem atualiza é a pessoa, no Desktop.
- **Editar os arquivos de um PBIP aberto no Desktop.** Segundo a Microsoft, o Desktop e quem edita por fora podem gravar um por cima do outro. Recarregar a mudança feita por fora ainda está em preview, atrás de uma opção. Com o projeto aberto, use o MCP conectado ao Desktop.

## Detalhes que pesam

- **Salvar:** o MCP grava no modelo em memória do Desktop; o arquivo só muda com o Ctrl+S (ou com o export, num PBIT).
- **Atualizar uma tabela:** a pessoa clica com o botão direito na tabela, no painel Dados, e escolhe Atualizar dados. A tabela é recarregada da fonte, e é preciso ter acesso a ela.
- **Coluna calculada nova** pelo MCP fica vazia, e a consulta que usa a coluna dá erro, até a pessoa atualizar a tabela.
- **M pelo MCP:** `partition_operations` com `Update` e a `expression` nova. Os dados só mudam quando a pessoa atualiza a tabela. Se o M novo cria, renomeia ou muda o tipo de colunas, as colunas do modelo não acompanham sozinhas: entregue o M para colar no Editor avançado, onde o Desktop ajusta as colunas ao aplicar.
- **`SecurityBindings`:** um blob cifrado pelo Windows (DPAPI) dentro do zip. Se o conteúdo muda e ele fica, o Desktop diz que o arquivo está corrompido. Por isso a cópia editada vai sem ele, e o Desktop o recria ao salvar.
- **PBIP editado por fora:** só com o projeto fechado no Desktop. UTF-8 sem BOM, fim de linha CRLF e indentação com tab, no padrão do arquivo. Não mexer em `report.json`, `definition.pbir`, `mobileState.json` e nos layouts de diagrama.
- **Conferir o TMDL editado:** `connection_operations` com `ConnectFolder` na pasta `.SemanticModel`. Se houver erro de sintaxe, o MCP diz o arquivo e a linha. Isso confere o TMDL, não o DAX: o DAX se testa com o projeto aberto no Desktop.
- **Desktop do Report Server:** não salva PBIP e oficialmente não aceita ferramentas externas. A Microsoft não cita o MCP nesse Desktop, mas diz que o MCP segue as regras das ferramentas externas. Nos testes (Desktop do RS de setembro de 2026), o MCP funcionou. A exibição de consulta DAX está documentada desde setembro de 2024. Exportar PBIT e a TMDL view funcionaram nos testes, mas não estão documentados para o RS.
- **Dois Desktops na mesma máquina:** o último instalado abre o `.pbix` com duplo clique. Abra os arquivos de dentro do Desktop certo.
- **PBIT no Git:** bem menor que o PBIX e sem dados. É a recomendação para quem versiona. O PBIX continua sendo o arquivo publicado.
