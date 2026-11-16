// Hook PreToolUse do plugin pbi: protege arquivos .pbix e .pbit.
//
// - Write, Edit, MultiEdit e NotebookEdit num .pbix ou .pbit: nega sempre.
// - Comando do Bash ou do PowerShell que cita .pbix ou .pbit:
//     * pelo script do plugin (pbi.py): libera;
//     * apagar, mover, copiar, renomear, compactar ou redirecionar para o arquivo: nega;
//     * outro interpretador (python, node...) mexendo no arquivo: pede confirmação;
//     * o resto (git, listar, ler): libera.
// Lê o JSON do evento no stdin e responde no stdout. Sem dependências.

const ARQUIVO_PBI = /\.pbi[xt]\b/i;
const SCRIPT_PLUGIN = /pbi\.py\b/i;
const GRAVA = new RegExp(
  [
    String.raw`(?:^|[\s;&|(])(?:rm|del|erase|mv|move|ren|rename|cp|copy|xcopy|robocopy|zip|7z|tar)(?=\s)`,
    String.raw`\b(?:Remove-Item|Move-Item|Copy-Item|Rename-Item|Set-Content|Add-Content|Out-File|New-Item|Compress-Archive|Clear-Content)\b`,
    String.raw`>{1,2}\s*["']?[^\s"'|;&]*\.pbi[xt]\b`,
    String.raw`\[(?:System\.)?IO\.(?:File|Compression)`,
  ].join('|'),
  'i',
);
const INTERPRETADOR = /(?:^|[\s;&|(])(?:python3?|py|node|uv|pwsh|powershell|cscript|wscript)(?:\.exe)?\s/i;

function responde(decisao, motivo) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: decisao, permissionDecisionReason: motivo },
  }));
}

let entrada = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { entrada += d; });
process.stdin.on('end', () => {
  let evento;
  try { evento = JSON.parse(entrada); } catch { process.exit(0); }
  const ferramenta = evento?.tool_name || '';
  const dados = evento?.tool_input || {};

  if (/^(Write|Edit|MultiEdit|NotebookEdit)$/.test(ferramenta)) {
    const arquivo = dados.file_path || dados.notebook_path || '';
    if (ARQUIVO_PBI.test(arquivo)) {
      responde('deny', `O plugin pbi não deixa gravar direto em ${arquivo}. PBIX e PBIT só mudam pelo Desktop, `
        + 'pelo MCP com o Desktop aberto, ou pelo fluxo guiado do script do plugin (pbi.py).');
    }
    process.exit(0);
  }

  if (ferramenta === 'Bash' || ferramenta === 'PowerShell') {
    const comando = String(dados.command || '');
    if (!ARQUIVO_PBI.test(comando)) process.exit(0);
    // Subcomandos do git (git rm, git mv...) são versionamento: não contam como gravação direta.
    const tiraGit = (t) => t.replace(/\bgit(?:\.exe)?\s+[\w-]+/gi, 'git');
    let citaFora = false;
    // Cada trecho (separado por ;, &&, ||, | ou quebra de linha) é avaliado sozinho.
    for (const trecho of comando.split(/&&|\|\||[;|\n]/)) {
      if (!ARQUIVO_PBI.test(trecho) || SCRIPT_PLUGIN.test(trecho)) continue;
      citaFora = true;
      if (GRAVA.test(tiraGit(trecho))) {
        responde('deny', 'Este comando apagaria, moveria, copiaria ou gravaria um .pbix ou .pbit por fora. '
          + 'No plugin pbi, esses arquivos só mudam pelo Desktop ou pelo fluxo guiado do pbi.py.');
        process.exit(0);
      }
    }
    // O interpretador é procurado no comando inteiro: o código de um "python -c" pode ter ";".
    if (citaFora && INTERPRETADOR.test(` ${tiraGit(comando)}`)) {
      responde('ask', 'Este comando roda um programa que mexe num .pbix ou .pbit fora do script do plugin. '
        + 'Confirme só se ele apenas lê o arquivo.');
    }
  }
  process.exit(0);
});
