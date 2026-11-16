---
name: configurar
description: Prepara a máquina para o plugin pbi (Power BI). Confere o uv (ou o Python com pip) e o Node, prepara o ambiente com o PBIXRay, que lê PBIX sem abrir o Desktop, e explica a EULA do MCP do Power BI. Use na primeira vez que o plugin for usado, quando a pessoa pedir para configurar o plugin, ou quando outra skill do pbi disser que falta algo.
---

# Configurar o plugin pbi

Fale com a pessoa em português e passo a passo. Não instale nada sem o ok dela.

## O que o plugin precisa

| Peça | Para quê |
|---|---|
| **uv** (recomendado) ou Python 3 com pip | rodar o script do plugin (`pbi.py`) e o PBIXRay, que lê o modelo de um PBIX sem abrir o Desktop |
| **Node** (LTS) | o MCP do Power BI (`npx`) e a proteção que impede gravar direto em `.pbix` e `.pbit` |
| **Power BI Desktop** | o normal, o do Report Server, ou os dois |

## 1. Conferir o que já existe

Rode e anote o resultado:

```bash
uv --version
node --version
```

Se o `uv` não existir, rode também `python --version` (no Windows, `py --version` também vale).

## 2. Preparar o Python

### Com uv (o caminho recomendado)

O script declara as dependências no próprio cabeçalho, numa versão fixa. O uv cria e guarda o ambiente sozinho, e baixa até o Python se faltar. Teste:

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" ambiente
```

Na primeira vez ele baixa o PBIXRay e o pandas (uns 10 segundos). Deve terminar com "Tudo pronto".

### Sem uv

Recomende instalar o uv, com estes argumentos: é um programa só, mantém o ambiente do plugin isolado e não exige Python instalado. O comando oficial no Windows, para a pessoa rodar ou autorizar:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Depois de instalar, o terminal e o Claude Code precisam ser reabertos para o `uv` entrar no PATH.

**Se a pessoa preferir o pip**, siga por ele: funciona igual. Precisa de Python 3.10 ou mais novo. Crie o ambiente na pasta de dados do plugin, que dura entre atualizações e some quando o plugin é desinstalado:

```bash
python -m venv "${CLAUDE_PLUGIN_DATA}/venv"
"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" -m pip install pbixray==0.15.5
"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" ambiente
```

Em máquina de empresa, o `pip` e o uv podem esbarrar num proxy. Se der erro de rede, mostre a mensagem e peça para a pessoa falar com o suporte de TI.

## 3. Node

Se o `node` não existir, explique que o MCP do Power BI e a proteção dos arquivos `.pbix` dependem dele. Recomende o Node LTS de nodejs.org. Sem ele, o plugin ainda lê arquivos, mas não conversa com o Desktop e não bloqueia gravações diretas.

## 4. MCP do Power BI e a EULA

O MCP do Power BI (da Microsoft) conversa com o Desktop aberto: roda consultas DAX para conferir números e grava medidas, colunas calculadas e relações. Ele vem com escrita ligada. O Claude Code pede permissão antes de cada gravação, e nada chega ao arquivo antes do Ctrl+S. Quem preferir só leitura muda a opção **Modo do MCP do Power BI** em `/config`.

Na primeira vez, o MCP exige aceitar a EULA da Microsoft: https://go.microsoft.com/fwlink/?LinkId=2381247

- **Nunca aceite por conta própria.** Mostre o link, peça para a pessoa ler, e só chame a ferramenta `accept_eula` do MCP (com essa URL) depois que ela disser, com as palavras dela, que aceita.
- O aceite fica gravado na máquina e não é pedido de novo.

## 5. Fechar

Resuma numa tabela curta o que está pronto e o que falta. Daqui em diante, as skills do plugin rodam o script assim:

- **com uv:** `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" <subcomando> <arquivo>`
- **com pip:** `"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" <subcomando> <arquivo>`

Subcomandos de leitura: `ambiente`, `info`, `relatorio`, `modelo`, `linhagem`, `revisar` e `comparar`. Do fluxo de visuais: `checar`, `extrair`, `montar`, `abrir`, `trocar` e `limpar`. O `--json` vale em quase todos.

O agente `revisor` do plugin roda o script num contexto próprio. Na primeira vez, o Claude Code pede permissão para o comando; a pessoa pode escolher não ser perguntada de novo.
