# power-bi-x-plugin

Plugin do [Claude Code](https://code.claude.com/docs) para entender e manter relatórios Power BI em qualquer formato de arquivo: **PBIX, PBIT e PBIP**, inclusive os do **Power BI Report Server**.

O Claude lê o relatório e o modelo direto do arquivo, sem abrir o Desktop. Ele explica de onde vem cada número, revisa boas práticas, cria medidas, muda visuais no Report Server e mostra o que mudou entre versões. Tudo em português, e sem nunca gravar direto no seu `.pbix`.

## Por quê

O plugin da Microsoft para Power BI ([`powerbi-authoring`](https://github.com/microsoft/skills-for-fabric)) trabalha com projetos PBIP e com o Fabric. Boa parte dos relatórios em produção, porém, ainda é PBIX. E quem usa o Power BI Report Server não tem PBIP nem ferramentas externas com suporte oficial.

Este plugin cobre esse caso. Para cada formato, ele sabe o que dá para ler, o que dá para mudar e por qual caminho seguro.

## O que ele faz

| Peça | Para quê | Exemplo de pedido |
|---|---|---|
| `/pbi:configurar` | prepara a máquina na primeira vez | "configure o plugin pbi" |
| `/pbi:raio-x` | explica o relatório de cada visual até a tabela no banco | "de onde vem o número do cartão Ticket Médio em vendas.pbix?" |
| agente `revisor` | revisa modelo, DAX, relações e M; devolve os problemas por prioridade, com o que corrigir e o link da documentação da Microsoft | "revise o vendas.pbix" |
| `/pbi:medida` | cria ou ajusta medidas no padrão da equipe, com testes e uma consulta SQL para conferir; confere as funções DAX na documentação atual da Microsoft | "crie uma medida de margem percentual" |
| `/pbi:visual` | muda ou cria visuais e páginas num relatório do Report Server | "na página Produtos, troque a coluna Vendas Produto pela medida Vendas" |
| `/pbi:diff` | mostra o que mudou entre versões, em português | "o que mudou no vendas.pbix desde o último commit?" |
| `/pbi:documentar` | gera a documentação em markdown e propõe descrições para o modelo | "documente o vendas.pbit" |
| `padroes` | as regras da sua equipe: nomes, formatos, DAX, M, fontes e glossário | vem com um exemplo para você trocar |
| `formatos` | o que o Claude precisa saber sobre cada formato (usada automaticamente) | |

Não precisa decorar os comandos: pedir em português também funciona, e o Claude escolhe a peça certa. Quando é o Claude que escolhe, o Claude Code pode pedir sua permissão antes de usar a skill. Na primeira vez que um comando do plugin rodar, ele também pede, e dá para marcar para não perguntar de novo.

## Instalação

1. Instale os requisitos (próxima seção).
2. No Claude Code:

   ```
   /plugin marketplace add denner25data/power-bi-x-plugin
   /plugin install pbi@power-bi-x
   ```

   O instalador avisa que a opção `modo_mcp` ainda não foi configurada. Pode ignorar: o padrão (`readwrite`) já vale. Para mudar, veja "Opções".
3. Rode `/pbi:configurar`. Ele confere o que falta e prepara o ambiente do Python com o PBIXRay.
4. Na primeira vez que o Claude usar o MCP do Power BI, ele mostra a [EULA da Microsoft](https://go.microsoft.com/fwlink/?LinkId=2381247). O aceite é seu: o Claude nunca aceita sozinho.

## Requisitos

| Peça | Para quê |
|---|---|
| Claude Code | o próprio plugin |
| Power BI Desktop: o normal, o do Report Server, ou os dois | abrir os relatórios; o MCP conversa com ele |
| **[uv](https://docs.astral.sh/uv/)** (recomendado) ou Python 3.10+ com pip | o script do plugin e o [PBIXRay](https://github.com/Hugoberry/pbixray), que lê PBIX sem abrir o Desktop. Com uv, nem precisa de Python instalado |
| Node.js LTS | o MCP do Power BI (pelo `npx`) e a proteção que impede gravar direto nos arquivos |

O plugin é feito para **Windows**, como o Power BI Desktop. O MCP do Power BI não roda no macOS.

Para instalar o uv no Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Prefere o pip? O `/pbi:configurar` cria um ambiente isolado na pasta de dados do plugin, e funciona igual.

## O que dá para fazer em cada formato

| Tarefa | PBIX do Report Server | PBIX do Desktop normal | PBIT | PBIP |
|---|---|---|---|---|
| Entender o relatório e o modelo | sim, sem abrir o Desktop | sim | sim, sem dados | sim |
| Revisar e comparar versões | sim | sim | sim | sim |
| Conferir números com DAX | com o Desktop aberto | com o Desktop aberto | não tem dados | com o Desktop aberto |
| Criar e mudar medidas | pelo MCP com o Desktop aberto, ou colando no Desktop | pelo MCP ou colando | abrindo no Desktop e exportando o PBIT de novo | pelo MCP |
| Criar e mudar visuais | sim, pelo fluxo guiado | não nesta versão | sim, se for do Report Server | não nesta versão |

## Como o plugin protege seus arquivos

- **Nunca grava direto num `.pbix` ou `.pbit`.** Um hook bloqueia gravar, apagar, mover ou copiar esses arquivos, tanto pelas ferramentas de edição quanto pelo terminal. A única exceção é o script do próprio plugin.
- **O modelo muda pelo Desktop.** Pelo MCP do Power BI, o Claude Code pede sua permissão antes de cada gravação, e nada chega ao arquivo antes do seu Ctrl+S. Se preferir, o Claude entrega o código para você colar na exibição de consulta DAX ou na TMDL view.
- **Visuais do Report Server, pelo fluxo guiado:**
  1. o plugin gera uma cópia editada numa pasta temporária;
  2. abre a cópia no seu Desktop;
  3. você confere e salva;
  4. com o seu ok, a cópia salva toma o lugar do original.

  O script confere três coisas antes de trocar: que o Desktop salvou a cópia, que ela continua sendo do Report Server e que o original não mudou no meio do caminho.
- **Sem backup na troca.** O original é substituído de vez. Por isso o plugin avisa antes e recomenda usar o fluxo só com o arquivo num repositório Git.
- **Nada fica para trás.** A pasta de trabalho fica no `%TEMP%`, fora dos seus repositórios, e é apagada no fim.
- **Seus dados ficam na máquina.** O plugin lê os arquivos localmente. O que o Claude lê (nomes, fórmulas e, se ele pedir, amostras de dados) faz parte da conversa com o Claude, como qualquer arquivo que ele abre. O MCP do Microsoft Learn recebe só o texto das buscas de documentação.

## PBIT no Git: a recomendação para quem versiona

Um PBIT tem o modelo e o relatório, mas **não tem os dados**. Ele é muito menor que o PBIX (30 KB contra 2,7 MB num relatório de teste) e não leva dados do cliente para o repositório.

- **Ler** um PBIT funciona igual a ler um PBIX. Só não dá para ver o tamanho das colunas nem amostras de dados.
- **Mudar** pede abrir o PBIT no Desktop, que carrega os dados da fonte (é preciso ter acesso ao banco), mudar e exportar o PBIT de novo (Arquivo → Exportar → Modelo do Power BI). O plugin lembra desse último passo.
- **Ver o que mudou:** o Git vê PBIX e PBIT como binários. Use `/pbi:diff` para ver as diferenças em português.

O PBIX continua sendo o arquivo que se publica no Report Server.

## Opções

Em `/config`, no plugin `pbi`:

| Opção | Valores | Padrão |
|---|---|---|
| Modo do MCP do Power BI | `readwrite`: o Claude também grava no modelo aberto no Desktop. `readonly`: só lê | `readwrite` |

## Copiar e personalizar para a sua equipe

O plugin é genérico de propósito. Para ter a versão da sua equipe:

1. Copie este repositório para a sua organização (GitHub, Azure DevOps ou qualquer Git).
2. Em `.claude-plugin/marketplace.json`, troque o nome do marketplace e o dono.
3. **Reescreva `plugins/pbi/skills/padroes/SKILL.md`** com as regras da equipe: nomes de medidas e tabelas, formatos, regras de DAX e de M, fontes aprovadas e o glossário de indicadores. A skill `medida`, a `documentar` e o agente `revisor` passam a seguir essas regras.
4. Se quiser, ajuste o texto das skills ao jeito da equipe. Os nomes das pastas são os comandos (`/pbi:raio-x` vem de `skills/raio-x/`).
5. A cada mudança, suba o `version` em `plugins/pbi/.claude-plugin/plugin.json`. Quem já instalou só recebe a atualização quando a versão muda.
6. Cada pessoa instala com `/plugin marketplace add <organização>/<repositório>` e `/plugin install pbi@<seu marketplace>`. Repositórios privados usam o login do Git da máquina.

Para testar mudanças antes de publicar: `claude --plugin-dir plugins/pbi` e, depois de cada edição, `/reload-plugins`. Para conferir a estrutura: `claude plugin validate .`.

## Limitações conhecidas

- **MCP no Desktop do Report Server:** funciona, mas a Microsoft não dá suporte a ferramentas externas nessa versão do Desktop. Uma atualização pode quebrar. O caminho de colar o código no Desktop continua valendo.
- **Formato antigo do relatório** (`Report/Layout`, o do Report Server): a Microsoft não documenta esse formato. O fluxo de visuais foi testado no Desktop do Report Server de setembro de 2026.
- **Mudar visuais** só em PBIX e PBIT do Report Server. Para PBIP, use o plugin da Microsoft.
- **Formato das medidas num PBIX** vem de uma parte interna do PBIXRay, por isso a versão dele é fixa (0.15.5).
- **PBIT e PBIP** não têm dados: sem tamanho de colunas nem amostras.
- **PBIP com o relatório em `report.json`** (formato antigo do PBIP) não é suportado. Salve o projeto em PBIR.

## Por dentro

| Arquivo | O que é |
|---|---|
| `plugins/pbi/scripts/pbi.py` | um script em Python (biblioteca padrão mais o PBIXRay) que lê PBIX, PBIT e PBIP e conduz o fluxo de visuais. Roda com `uv run --script`, que instala o PBIXRay sozinho |
| `plugins/pbi/hooks/protege-pbix.mjs` | a proteção contra gravar direto em `.pbix` e `.pbit` |
| `plugins/pbi/.mcp.json` | o [Power BI Authoring MCP](https://github.com/microsoft/powerbi-modeling-mcp) da Microsoft (versão fixa 1.0.1) e o MCP do Microsoft Learn, que a `medida` e o `revisor` usam para conferir DAX e boas práticas na documentação atual |
| `plugins/pbi/skills/` e `plugins/pbi/agents/` | as skills e o agente |

## Licença e terceiros

[MIT](LICENSE).

O plugin usa, sem copiar código:
- **[PBIXRay](https://github.com/Hugoberry/pbixray)**, MIT, instalado no ambiente do plugin;
- **[Power BI Authoring MCP](https://github.com/microsoft/powerbi-modeling-mcp)**, da Microsoft, com EULA própria, aceita por quem usa;
- **[MCP do Microsoft Learn](https://learn.microsoft.com/api/mcp)**.

Power BI é marca da Microsoft. Este plugin não é um produto da Microsoft nem da Anthropic.
