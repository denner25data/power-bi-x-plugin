---
name: visual
description: Muda ou cria visuais e páginas num relatório do Power BI Report Server (PBIX ou PBIT do Desktop do Report Server), por um fluxo guiado que nunca grava direto no arquivo da pessoa. Use quando pedirem para trocar campos de um visual, criar um cartão, gráfico, tabela ou segmentação, mover ou redimensionar visuais, renomear, criar ou apagar páginas num relatório do Report Server.
argument-hint: "[arquivo .pbix ou .pbit] [o que mudar]"
allowed-tools: Bash(uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *) Bash("${CLAUDE_PLUGIN_DATA}/venv/Scripts/python" "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" *)
---

# Mudar visuais de um relatório do Report Server

O Desktop do Report Server não salva PBIP, e nenhuma ferramenta da Microsoft mexe em visuais por fora. Este fluxo faz isso com segurança: **o arquivo da pessoa só muda no fim, por uma cópia que o próprio Desktop abriu e salvou, e só com o ok dela.** Quem grava é o script do plugin, nunca você direto.

Rode o script assim (com uv; sem uv, troque `uv run --script` por `"${CLAUDE_PLUGIN_DATA}/venv/Scripts/python"`):

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/pbi.py" <subcomando> ...
```

## Antes de começar

Avise a pessoa, com estas palavras ou parecidas: **"No fim, o arquivo original será substituído pela versão editada, sem backup. Recomendo usar isto só com o arquivo num repositório Git, para poder voltar à versão anterior."**

## Passo a passo

1. **Checar:** `checar "<arquivo>"`.
   - Se der "Não dá para seguir", explique e pare. Exemplos: o arquivo é do Desktop normal (fora desta versão), ou o Desktop está com ele aberto.
   - Se vier o aviso de Git (sem histórico ou com mudanças não commitadas), repita o aviso acima e só siga com a confirmação explícita da pessoa.
2. **Entender o relatório:** `relatorio "<arquivo>"`. Use os ids entre colchetes para achar páginas e visuais. Para saber em que tabela está uma medida ou coluna que você vai pôr num visual, rode `modelo "<arquivo>"`; não adivinhe.
3. **Combinar a mudança:** descreva em português o que vai mudar ("na página Produtos, trocar a coluna Vendas Produto pela medida Vendas") e espere o ok.
4. **Extrair:** `extrair "<arquivo>"`. Ele cria a pasta de trabalho no `%TEMP%\pbi-x\` e mostra o caminho do `layout.json`.
5. **Editar:** siga a seção "O formato antigo", abaixo, e edite o `layout.json` com a ferramenta de edição. Mude só o que foi combinado.
6. **Montar:** `montar "<pasta de trabalho>"`.
   - Se houver erros (campo que não existe, `queryRef` sem `Select`, id repetido), corrija o `layout.json` e rode de novo.
   - Quando der certo, mostre à pessoa a lista "O que muda no relatório".
7. **Abrir:** `abrir "<pasta de trabalho>"` abre a cópia no Desktop. Passe o passo a passo:
   - **PBIX:** conferir as páginas, salvar com Ctrl+S e fechar o Desktop.
   - **PBIT:** o Desktop carrega os dados do banco (precisa de acesso à fonte). Conferir, usar Arquivo → Exportar → Modelo do Power BI, salvar **por cima da cópia**, no caminho que o script mostrou, e fechar sem salvar o PBIX.
   - Se a máquina tiver os dois Desktops, a cópia precisa abrir no do Report Server.
8. **Trocar:** só depois que a pessoa disser que conferiu e quer substituir o original, rode `trocar "<pasta de trabalho>" --confirmo`. O script recusa e explica se:
   - a cópia não foi salva pelo Desktop (falta o carimbo);
   - ela virou arquivo do Desktop normal;
   - o original mudou no meio do caminho;
   - o Desktop ainda está aberto.
9. **Fechar:** diga que o original foi substituído. Se estiver no Git, sugira conferir com a skill `diff` e fazer o commit.

Se a pessoa desistir em qualquer ponto, rode `limpar "<pasta de trabalho>"`. Nada no arquivo dela terá mudado.

## Regras

- Nunca grave, copie, mova ou apague um `.pbix` ou `.pbit` por conta própria. O hook do plugin bloqueia, e o único caminho é o `trocar`.
- Nunca rode `trocar --confirmo` sem o ok da pessoa nesta conversa.
- Uma mudança de cada vez é mais fácil de conferir. Mudanças grandes podem ser feitas em rodadas: editar, montar e abrir quantas vezes for preciso antes de trocar.
- Mudar medidas não é com esta skill: use a `medida`.

## O formato antigo do relatório (`Report/Layout`)

É o formato que o Desktop do Report Server grava dentro do PBIX e do PBIT. A Microsoft não documenta esse formato: siga os exemplos daqui e copie a estrutura de um visual que já existe no arquivo sempre que der.

No `layout.json` da pasta de trabalho, os textos JSON internos (`config`, `filters`, `query`, `dataTransforms`) já vêm abertos em objetos. O `montar` fecha de novo.

### Estrutura

```text
layout
├── config, filters, resourcePackages, layoutOptimization   (do relatório: não mexa sem motivo)
└── sections[]                                              (as páginas, na ordem de "ordinal")
    ├── name             id da página: 20 caracteres hexadecimais minúsculos, único
    ├── displayName      o nome que aparece na aba
    ├── ordinal          a ordem (0, 1, 2…), sem repetir
    ├── width, height    tamanho da página (ex.: 1280 × 720 ou 1920 × 1080)
    ├── displayOption    1 = ajustar à página
    ├── config, filters  da página ("{}" e [] numa página nova)
    └── visualContainers[]
        ├── x, y, z, width, height   posição e tamanho (z: ordem de empilhar)
        ├── filters                  filtros do visual ([] se não houver)
        └── config
            ├── name        id do visual: 20 caracteres hexadecimais minúsculos, único no relatório
            ├── layouts     [{"id": 0, "position": {x, y, z, width, height, tabOrder}}]
            └── singleVisual
                ├── visualType     ex.: "card", "tableEx"
                ├── projections    {papel: [{"queryRef": "..."}]}
                ├── prototypeQuery {"Version": 2, "From": [...], "Select": [...], "OrderBy": [...]}
                ├── drillFilterOtherVisuals: true
                ├── objects        formatação do conteúdo (opcional)
                └── vcObjects      formatação do contêiner, como o título (opcional)
```

O `montar` alinha sozinho a posição de `layouts[0]` com a do contêiner e apaga `query` e `dataTransforms` (o cache das consultas, que o Desktop refaz ao abrir). Mude a posição no contêiner.

### Um visual completo

Um cartão com a medida `Vendas` da tabela `FactInternetSales`:

```json
{
  "x": 20, "y": 20, "z": 0, "width": 300, "height": 150,
  "filters": [],
  "config": {
    "name": "a1b2c3d4e5f6a7b8c9d0",
    "layouts": [{"id": 0, "position": {"x": 20, "y": 20, "z": 0, "width": 300, "height": 150, "tabOrder": 0}}],
    "singleVisual": {
      "visualType": "card",
      "projections": {"Values": [{"queryRef": "FactInternetSales.Vendas"}]},
      "prototypeQuery": {
        "Version": 2,
        "From": [{"Name": "f", "Entity": "FactInternetSales", "Type": 0}],
        "Select": [{
          "Measure": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": "Vendas"},
          "Name": "FactInternetSales.Vendas",
          "NativeReferenceName": "Vendas"
        }]
      },
      "drillFilterOtherVisuals": true
    }
  }
}
```

Regras do `prototypeQuery`:
- `From`: uma entrada por tabela usada, com um apelido curto (`Name`). O Desktop usa a primeira letra minúscula e acrescenta um número se repetir (`d`, `d1`).
- `Select`: um item por campo. O `Name` é o `queryRef` que as projeções usam, por convenção `Tabela.Campo`.
- Cada `queryRef` de `projections` precisa existir como `Name` no `Select`. O `montar` confere isso e confere se o campo existe no modelo.

### Campos

| Campo | No `Select` | `queryRef` |
|---|---|---|
| medida | `{"Measure": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": "Vendas"}}` | `FactInternetSales.Vendas` |
| coluna | `{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "CalendarYear"}}` | `DimDate.CalendarYear` |
| coluna agregada | `{"Aggregation": {"Expression": {"Column": {...}}, "Function": 0}}` | `Sum(DimProduct.ListPrice)` |

`Function` da agregação: 0 soma, 1 média, 2 contagem distinta, 3 mínimo, 4 máximo, 5 contagem, 6 mediana, 7 desvio padrão, 8 variância.

### Papéis por tipo de visual

| `visualType` | Visual | Papéis |
|---|---|---|
| `card` | cartão | `Values` |
| `multiRowCard` | cartão de várias linhas | `Values` |
| `slicer` | segmentação | `Values` (marque `"active": true` na projeção) |
| `tableEx` | tabela | `Values` |
| `pivotTable` | matriz | `Rows`, `Columns`, `Values` |
| `clusteredColumnChart` / `clusteredBarChart` | colunas / barras agrupadas | `Category`, `Y`, `Series` |
| `stackedColumnChart` / `stackedBarChart` | colunas / barras empilhadas | `Category`, `Y`, `Series` |
| `lineChart` / `areaChart` | linhas / área | `Category`, `Y`, `Series` |
| `lineClusteredColumnComboChart` | colunas e linha | `Category`, `Y` (colunas), `Y2` (linha) |
| `pieChart` / `donutChart` | pizza / rosca | `Category`, `Y` |
| `gauge` | medidor | `Y`, `MinValue`, `MaxValue`, `TargetValue` |

Nos gráficos, o Desktop marca `"active": true` na projeção de `Category`. Faça igual.

### Ordenação e título

- **Ordenar** (no `prototypeQuery`): `"OrderBy": [{"Direction": 1, "Expression": {"Column": {...}}}]`. `Direction` 1 é crescente e 2, decrescente.
- **Título** (em `singleVisual`):

  ```json
  "vcObjects": {"title": [{"properties": {
    "show": {"expr": {"Literal": {"Value": "true"}}},
    "text": {"expr": {"Literal": {"Value": "'Vendas por ano'"}}}
  }}]}
  ```

  O texto vai entre aspas simples dentro do `Value`.

### Página nova

Copie uma página existente e troque: `name` (id novo), `displayName`, `ordinal` (o próximo número), e os `visualContainers` (cada um com `name` novo). Mantenha `width`, `height` e `displayOption` das outras páginas.

### Cuidados

- Gere ids novos com 20 caracteres hexadecimais minúsculos. Nunca repita o id de outro visual ou página.
- Mantenha os visuais dentro da página: `x + width ≤ width da página`, e o mesmo para `y` e `height`.
- Para apagar um visual, tire o contêiner inteiro da lista. Para apagar uma página, tire a seção e renumere os `ordinal`.
- Não mexa no `config` do relatório nem no `resourcePackages` sem necessidade: eles guardam o tema.
