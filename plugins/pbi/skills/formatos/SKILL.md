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
| Mudar o modelo | MCP (sem suporte oficial no RS) ou colar na consulta DAX ou na TMDL view | MCP ou colar | abrir no Desktop, mudar e exportar o PBIT de novo | MCP com o Desktop aberto |
| Mudar visuais | skill `visual` (fluxo guiado) | fora desta versão | skill `visual`, se for do Report Server | fora desta versão |

## Nunca

- **Gravar direto num `.pbix` ou `.pbit`**, nem com `Write`, `Edit`, `cp`, `mv`, `zip`, redirecionamento ou script próprio. O hook do plugin bloqueia. O modelo muda pelo Desktop (MCP ou colando) e o relatório do Report Server, pelo fluxo da skill `visual`.
- **Editar um PBIT pensando que ele muda sozinho.** A Microsoft não suporta. Abre-se o PBIT no Desktop, muda-se e exporta-se de novo.
- **Aceitar a EULA do MCP sem a pessoa pedir.**

## Detalhes que pesam

- **Salvar:** o MCP grava no modelo em memória do Desktop; o arquivo só muda com o Ctrl+S (ou com o export, num PBIT).
- **Coluna calculada nova** pelo MCP fica sem valor até um recálculo (`model_operations`, `RefreshWithXMLA`, tipo `Calculate`).
- **`SecurityBindings`:** um blob cifrado pelo Windows (DPAPI) dentro do zip. Se o conteúdo muda e ele fica, o Desktop diz que o arquivo está corrompido. Por isso a cópia editada vai sem ele, e o Desktop o recria ao salvar.
- **PBIP editado por fora:** UTF-8 sem BOM e fim de linha CRLF. Não mexer em `report.json`, `definition.pbir`, `mobileState.json` e nos layouts de diagrama.
- **Desktop do Report Server:** não salva PBIP e oficialmente não aceita ferramentas externas, mas o MCP do Power BI funciona nele. Exporta PBIT e tem a exibição de consulta DAX e a TMDL view.
- **Dois Desktops na mesma máquina:** o último instalado abre o `.pbix` com duplo clique. Abra os arquivos de dentro do Desktop certo.
- **PBIT no Git:** bem menor que o PBIX e sem dados. É a recomendação para quem versiona. O PBIX continua sendo o arquivo publicado.
