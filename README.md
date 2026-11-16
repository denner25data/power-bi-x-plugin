# power-bi-x-plugin

Plugin do [Claude Code](https://code.claude.com/docs) para trabalhar com relatórios Power BI em qualquer formato de arquivo: **PBIX, PBIT e PBIP**, inclusive os do **Power BI Report Server**, que só salva PBIX.

> **Em desenvolvimento.** Ainda não há versão para instalar.

## Por quê

O plugin oficial da Microsoft para Power BI ([`powerbi-authoring`](https://github.com/microsoft/skills-for-fabric)) trabalha com projetos PBIP e com o Fabric. Boa parte dos relatórios em produção, porém, ainda é PBIX, e quem usa o Power BI Report Server não tem PBIP nem ferramentas externas.

Este plugin cobre esse caso: entender e manter relatórios legados, escolhendo para cada formato o que dá para ler, o que dá para editar e por qual caminho, sem nunca gravar direto num `.pbix` ou `.pbit`.

## Instalação

Quando a primeira versão sair, dentro do Claude Code:

```
/plugin marketplace add denner25data/power-bi-x-plugin
/plugin install pbi@power-bi-x
```

## Licença

[MIT](LICENSE)
