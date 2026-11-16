---
name: diff
description: Mostra o que mudou num relatório do Power BI (PBIX, PBIT ou PBIP), em português e pelo significado. Compara medidas e fórmulas, colunas, relações, consultas M, páginas, visuais e filtros, entre a versão atual e uma versão do Git, ou entre dois arquivos. Use quando perguntarem o que mudou num relatório, antes de um commit, numa revisão de alteração, ou para comparar duas versões ou dois arquivos.
argument-hint: "[arquivo] [outro arquivo ou versão do Git, opcional]"
allowed-tools: Bash(uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *) Bash("${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *)
---

# O que mudou num relatório

O Git vê PBIX e PBIT como arquivos binários, então o `git diff` não mostra nada útil. O script do plugin abre as duas versões e compara o conteúdo. Só lê; não grava nada.

Rode assim (com uv; sem uv, troque `uv run --script` por `"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python"`):

| Pedido | Comando |
|---|---|
| o arquivo atual contra o último commit | `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" comparar "<arquivo>"` |
| contra outra versão do Git | o mesmo, com `--rev <commit, branch ou tag>`, por exemplo `--rev HEAD~3` ou `--rev main` |
| dois arquivos | `... comparar "<antes>" "<depois>"` |

Funciona com PBIX, PBIT e PBIP, e até entre formatos: um PBIX e o PBIT exportado dele comparam como iguais. Para um PBIP, passe o arquivo `.pbip`.

Se a pessoa não disser qual arquivo, procure os `.pbix`, `.pbit` e `.pbip` modificados no `git status` e pergunte qual.

## Como explicar

1. **Resumo**, numa frase: quanto mudou e onde ("2 medidas alteradas e 1 visual novo na página Produtos").
2. **Mudanças que afetam números**, primeiro:
   - fórmula de medida ou de coluna calculada: mostre antes e depois e explique a diferença em português;
   - relação que mudou de direção, ficou inativa, sumiu ou apareceu;
   - consulta M alterada, como filtro novo ou coluna removida.
3. **Mudanças de relatório:** páginas e visuais novos, removidos ou com campos trocados, e filtros.
4. **Mudanças de acabamento:** formato, pasta, descrição, posição de visual.

Se houver risco, diga. Exemplos: "a medida X mudou e é usada em 4 visuais"; "a coluna Y saiu do M, mas um visual ainda a usa". Para ver onde um campo é usado, rode `linhagem` no arquivo atual.

Se o script disser que não há diferença, diga isso; o arquivo pode ter mudado só nos dados.
