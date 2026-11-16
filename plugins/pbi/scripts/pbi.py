#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pbixray==0.15.5"]
# ///
"""pbi.py: lê arquivos do Power BI (PBIX, PBIT e PBIP) para o plugin pbi.

Leitura (nunca grava):
  ambiente                      confere o Python e o PBIXRay (usado pela skill configurar)
  info <arquivo>                que tipo de arquivo é e de qual Desktop veio
  relatorio <arquivo>           páginas, visuais, campos e filtros
  modelo <arquivo>              tabelas, medidas, colunas calculadas, relações, M e fontes
  linhagem <arquivo>            de cada visual até a fonte, e o que não é usado
  revisar <arquivo>             regras de boas práticas, por severidade
  comparar <arquivo> [outro]    o que mudou: entre dois arquivos, ou do arquivo para uma versão do Git (--rev)

Fluxo guiado de visuais (PBIX e PBIT do Report Server):
  checar <arquivo>              confere se dá para editar (Report Server, Desktop fechado, Git)
  extrair <arquivo>             cria a pasta de trabalho no %TEMP% com o layout.json editável
  montar <pasta>                valida o layout.json e gera a cópia editada na pasta de trabalho
  abrir <pasta>                 abre a cópia no Desktop, para conferir e salvar (ou exportar, no PBIT)
  trocar <pasta> --confirmo     põe a cópia salva pelo Desktop no lugar do original (o original some)
  limpar [<pasta>] [--tudo]     apaga a pasta de trabalho (ou as sobras com mais de um dia)

Só o trocar grava no arquivo da pessoa, e só depois de conferir o carimbo do Desktop
(o SecurityBindings), a origem (Report Server) e que o original não mudou.
O modelo de um PBIX é lido pelo PBIXRay; o resto usa só a biblioteca padrão.
"""
from __future__ import annotations

import argparse
import copy
import csv
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

AGREGACOES = {0: "Soma", 1: "Média", 2: "Contagem distinta", 3: "Mín", 4: "Máx",
              5: "Contagem", 6: "Mediana", 7: "Desvio padrão", 8: "Variância"}
TIPOS_DADO = {1: "automático", 2: "texto", 6: "inteiro", 8: "decimal", 9: "data/hora",
              10: "decimal fixo", 11: "booleano", 17: "binário", 19: "desconhecido", 20: "variante"}
AUTOMATICAS = re.compile(r"^(LocalDateTable_|DateTableTemplate_)")
FONTE_M = re.compile(
    r"\b((?:Sql|Oracle|PostgreSQL|MySQL|Odbc|OleDb|Teradata|Snowflake|GoogleBigQuery|AmazonRedshift|"
    r"Databricks|AnalysisServices|SapHana|SapBusinessWarehouse|Sybase|Informix|DB2|Access|Excel|Csv|"
    r"Json|Xml|Web|SharePoint|OData|Folder|File|PowerBI|PowerPlatform|Salesforce|CommonDataService|"
    r"Dataverse|AzureStorage|AzureDataLakeStorage|Lakehouse|Fabric)\.[A-Za-z]+)\s*\(([^()]*)\)")


class Erro(Exception):
    pass


def numero(valor: float, casas: int = 0) -> str:
    """Número no formato brasileiro: 1.142,2."""
    return f"{valor:,.{casas}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def plural(n: int, singular: str, plural_: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_ or singular + 's')}"


# ---------------------------------------------------------------- leitura bruta

def texto(dados: bytes) -> str:
    """Decodifica as partes do Power BI: UTF-16 LE (com ou sem BOM) ou UTF-8."""
    if dados[:2] == b"\xff\xfe":
        return dados[2:].decode("utf-16-le")
    if len(dados) > 1 and dados[1] == 0:
        return dados.decode("utf-16-le")
    return dados.decode("utf-8-sig")


class Fonte:
    """Lê partes de um zip (PBIX/PBIT) ou de uma pasta (PBIP) pelo mesmo caminho relativo."""

    def __init__(self, zip_: zipfile.ZipFile | None = None, pasta: Path | None = None):
        self.zip, self.pasta = zip_, pasta

    def nomes(self) -> list[str]:
        if self.zip:
            return self.zip.namelist()
        return [p.relative_to(self.pasta).as_posix() for p in self.pasta.rglob("*") if p.is_file()]

    def existe(self, nome: str) -> bool:
        if self.zip:
            return nome in self.zip.namelist()
        return (self.pasta / nome).is_file()

    def ler(self, nome: str) -> str:
        if self.zip:
            return texto(self.zip.read(nome))
        return texto((self.pasta / nome).read_bytes())

    def json(self, nome: str):
        return json.loads(self.ler(nome))


def situacao_git(caminho: Path) -> dict:
    """Diz se o arquivo está num repositório Git e se está versionado (o que importa antes de uma troca)."""
    pasta = caminho if caminho.is_dir() else caminho.parent
    raiz = next((p for p in [pasta.resolve(), *pasta.resolve().parents] if (p / ".git").exists()), None)
    if raiz is None:
        return {"repositorio": None, "situacao": "fora de repositório Git"}
    try:
        def git(*args):
            return subprocess.run(["git", "-C", str(pasta), *args], capture_output=True, text=True, timeout=15)
        if git("ls-files", "--error-unmatch", "--", str(caminho)).returncode == 0:
            mudou = git("status", "--porcelain", "--", str(caminho)).stdout.strip()
            situacao = "versionado, com mudanças não commitadas" if mudou else "versionado, sem mudanças"
        elif git("check-ignore", "-q", "--", str(caminho)).returncode == 0:
            situacao = "ignorado pelo .gitignore (sem histórico)"
        else:
            situacao = "ainda não versionado (sem histórico)"
    except (OSError, subprocess.TimeoutExpired):
        situacao = "dentro de um repositório, mas o git não respondeu"
    return {"repositorio": str(raiz), "situacao": situacao}


def abrir(caminho_txt: str) -> dict:
    """Identifica o alvo e devolve o que for preciso para lê-lo."""
    caminho = Path(caminho_txt)
    if not caminho.exists():
        raise Erro(f"Não achei: {caminho}")
    alvo = {"caminho": caminho, "tipo": None, "fonte_relatorio": None, "fonte_modelo": None,
            "prefixo_relatorio": "", "zip": None}

    if caminho.is_dir() or caminho.suffix.lower() == ".pbip":
        relatorio, modelo = pastas_pbip(caminho)
        alvo["tipo"] = "pbip"
        alvo["fonte_relatorio"] = Fonte(pasta=relatorio) if relatorio else None
        alvo["fonte_modelo"] = modelo
        return alvo

    ext = caminho.suffix.lower()
    if ext not in (".pbix", ".pbit"):
        raise Erro(f"Tipo de arquivo não suportado: {caminho.suffix}")
    try:
        z = zipfile.ZipFile(caminho)
    except zipfile.BadZipFile:
        raise Erro(f"O arquivo não é um zip válido do Power BI: {caminho}")
    alvo.update(tipo=ext[1:], zip=z, fonte_relatorio=Fonte(zip_=z), prefixo_relatorio="Report/")
    return alvo


def pastas_pbip(caminho: Path) -> tuple[Path | None, Path | None]:
    """Acha as pastas .Report e .SemanticModel a partir de um .pbip ou de uma pasta."""
    if caminho.suffix.lower() == ".pbip":
        dados = json.loads(texto(caminho.read_bytes()))
        rel = next((a["report"]["path"] for a in dados.get("artifacts", []) if "report" in a), None)
        relatorio = (caminho.parent / rel) if rel else None
    elif caminho.name.endswith(".Report"):
        relatorio = caminho
    elif caminho.name.endswith(".SemanticModel"):
        return None, caminho
    else:
        achados = sorted(caminho.glob("*.Report"))
        relatorio = achados[0] if achados else None
    modelo = None
    if relatorio and (relatorio / "definition.pbir").is_file():
        ref = json.loads(texto((relatorio / "definition.pbir").read_bytes())).get("datasetReference", {})
        if "byPath" in ref:
            modelo = (relatorio / ref["byPath"]["path"]).resolve()
    if modelo is None and relatorio:
        irmao = relatorio.parent / relatorio.name.replace(".Report", ".SemanticModel")
        modelo = irmao if irmao.is_dir() else None
    return relatorio, modelo


# ---------------------------------------------------------------- info

def info(alvo: dict) -> dict:
    caminho = alvo["caminho"]
    saida = {"arquivo": str(caminho), "tipo": alvo["tipo"], "git": situacao_git(caminho)}
    if alvo["tipo"] == "pbip":
        fr = alvo["fonte_relatorio"]
        saida.update(
            origem="Desktop normal",
            relatorio=str(fr.pasta) if fr else None,
            relatorio_formato=("PBIR" if fr and fr.existe("definition/pages/pages.json")
                               else "report.json (antigo)" if fr and fr.existe("report.json") else None),
            modelo=str(alvo["fonte_modelo"]) if alvo["fonte_modelo"] else None,
            modelo_formato="TMDL" if alvo["fonte_modelo"] and (alvo["fonte_modelo"] / "definition").is_dir()
            else "model.bim" if alvo["fonte_modelo"] and (alvo["fonte_modelo"] / "model.bim").is_file() else None,
        )
        return saida

    z: zipfile.ZipFile = alvo["zip"]
    nomes = set(z.namelist())
    meta = json.loads(texto(z.read("Metadata"))) if "Metadata" in nomes else {}
    criado = meta.get("CreatedFrom")
    origem = {"OnPrem": "Desktop do Report Server", "Cloud": "Desktop normal"}.get(criado, "desconhecida")
    if "Report/Layout" in nomes:
        formato = "antigo (Report/Layout)"
    elif any(n.startswith("Report/definition/") for n in nomes):
        formato = "PBIR (Report/definition/)"
    else:
        formato = None
    saida.update(
        tamanho_kb=round(caminho.stat().st_size / 1024),
        origem=origem,
        versao_desktop=meta.get("CreatedFromRelease"),
        relatorio_formato=formato,
        modelo=("DataModel (lido pelo PBIXRay)" if "DataModel" in nomes
                else "DataModelSchema (JSON, sem dados)" if "DataModelSchema" in nomes else None),
        security_bindings="SecurityBindings" in nomes,
    )
    return saida


def info_texto(d: dict) -> str:
    tam = ""
    if "tamanho_kb" in d:
        kb = d["tamanho_kb"]
        tam = f" ({numero(kb / 1024, 1)} MB)" if kb >= 1024 else f" ({numero(kb)} KB)"
    linhas = [f"Arquivo: {d['arquivo']}{tam}"]
    tipo = {"pbix": "PBIX", "pbit": "PBIT (modelo sem dados)", "pbip": "PBIP"}[d["tipo"]]
    origem = d["origem"] + (f", versão {d['versao_desktop']}" if d.get("versao_desktop") else "")
    linhas += [f"Tipo: {tipo}, do {origem}",
               f"Relatório: {d.get('relatorio_formato') or 'não encontrado'}",
               f"Modelo: {d.get('modelo_formato') or d.get('modelo') or 'não encontrado'}"]
    if d["tipo"] == "pbip":
        linhas += [f"Pasta do relatório: {d.get('relatorio')}", f"Pasta do modelo: {d.get('modelo')}"]
    else:
        linhas.append(f"SecurityBindings: {'sim' if d['security_bindings'] else 'não'}")
    g = d["git"]
    linhas.append(f"Git: {g['situacao']}" + (f" (repositório {g['repositorio']})" if g["repositorio"] else ""))
    return "\n".join(linhas)


# ---------------------------------------------------------------- relatório

def descrever_campo(expr: dict, apelidos: dict | None = None) -> dict:
    """Traduz uma expressão de campo do Power BI para Tabela[Campo]."""
    apelidos = apelidos or {}

    def entidade(no: dict) -> str:
        ref = no.get("Expression", {}).get("SourceRef", {}) if "Expression" in no else no.get("SourceRef", {})
        return ref.get("Entity") or apelidos.get(ref.get("Source"), ref.get("Source", "?"))

    if "Measure" in expr:
        m = expr["Measure"]
        return {"campo": f"{entidade(m)}[{m['Property']}]", "tipo": "medida"}
    if "Column" in expr:
        c = expr["Column"]
        return {"campo": f"{entidade(c)}[{c['Property']}]", "tipo": "coluna"}
    if "Aggregation" in expr:
        a = expr["Aggregation"]
        dentro = descrever_campo(a["Expression"], apelidos)
        return {**dentro, "agregacao": AGREGACOES.get(a.get("Function"), str(a.get("Function")))}
    if "HierarchyLevel" in expr:
        h = expr["HierarchyLevel"]
        hier = h["Expression"]["Hierarchy"]
        base = hier["Expression"]
        if "PropertyVariationSource" in base:
            pvs = base["PropertyVariationSource"]
            return {"campo": f"{entidade(pvs)}[{pvs['Property']}]", "tipo": "coluna",
                    "nivel": f"{hier['Hierarchy']} > {h['Level']}"}
        return {"campo": f"{entidade(hier)}[{hier['Hierarchy']}]", "tipo": "hierarquia", "nivel": h["Level"]}
    return {"campo": json.dumps(expr, ensure_ascii=False)[:80], "tipo": "outro"}


def campos_em(obj) -> list[dict]:
    """Acha os campos (Tabela[Campo]) citados em qualquer JSON de filtro do Power BI."""
    achados = []

    def anda(no):
        if isinstance(no, str) and no[:1] in "[{":
            try:
                anda(json.loads(no))
            except ValueError:
                pass
        elif isinstance(no, dict):
            for chave in ("Measure", "Column"):
                alvo = no.get(chave)
                if isinstance(alvo, dict) and "Entity" in alvo.get("Expression", {}).get("SourceRef", {}):
                    achados.append(descrever_campo({chave: alvo}))
            for v in no.values():
                anda(v)
        elif isinstance(no, list):
            for v in no:
                anda(v)

    anda(obj)
    vistos, unicos = set(), []
    for c in achados:
        if c["campo"] not in vistos:
            vistos.add(c["campo"])
            unicos.append(c)
    return unicos


def titulo_visual(objetos: dict) -> str | None:
    try:
        valor = objetos["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        return valor.strip("'")
    except (KeyError, IndexError, TypeError):
        return None


def relatorio_layout(fonte: Fonte, nome: str) -> dict:
    layout = fonte.json(nome)
    paginas, filtros = [], []
    if campos_em(layout.get("filters")):
        filtros.append({"onde": "relatório", "campos": campos_em(layout.get("filters"))})
    for s in sorted(layout.get("sections", []), key=lambda s: s.get("ordinal", 0)):
        cfg_pagina = json.loads(s.get("config") or "{}")
        if campos_em(s.get("filters")):
            filtros.append({"onde": f"página {s.get('displayName')}", "campos": campos_em(s.get("filters"))})
        visuais = []
        for vc in s.get("visualContainers", []):
            cfg = json.loads(vc.get("config") or "{}")
            if campos_em(vc.get("filters")):
                filtros.append({"onde": f"visual {cfg.get('name')} (página {s.get('displayName')})",
                                "campos": campos_em(vc.get("filters"))})
            v = {"nome": cfg.get("name"), "x": vc.get("x"), "y": vc.get("y"),
                 "largura": vc.get("width"), "altura": vc.get("height")}
            sv = cfg.get("singleVisual")
            if sv is None:
                v.update(tipo="grupo" if "singleVisualGroup" in cfg else "desconhecido", campos={})
                visuais.append(v)
                continue
            pq = sv.get("prototypeQuery", {})
            apelidos = {f["Name"]: f["Entity"] for f in pq.get("From", [])}
            sel = {it.get("Name"): descrever_campo(it, apelidos) for it in pq.get("Select", [])}
            campos = {papel: [sel.get(p.get("queryRef"), {"campo": p.get("queryRef"), "tipo": "?"}) for p in projs]
                      for papel, projs in sv.get("projections", {}).items()}
            v.update(tipo=sv.get("visualType"), titulo=titulo_visual(sv.get("vcObjects", {})), campos=campos)
            visuais.append(v)
        paginas.append({"nome": s.get("name"), "exibicao": s.get("displayName"),
                        "oculta": cfg_pagina.get("visibility") == 1,
                        "largura": s.get("width"), "altura": s.get("height"), "visuais": visuais})
    return {"formato": "antigo (Report/Layout)", "paginas": paginas, "filtros": filtros}


def relatorio_pbir(fonte: Fonte, base: str) -> dict:
    nomes = fonte.nomes()
    ordem = fonte.json(f"{base}pages/pages.json").get("pageOrder", [])
    todas = sorted({n.split("/")[len(base.split("/")) ] for n in nomes
                    if n.startswith(f"{base}pages/") and n.endswith("/page.json")})
    paginas, filtros = [], []
    if fonte.existe(f"{base}report.json"):
        campos = campos_em(fonte.json(f"{base}report.json").get("filterConfig"))
        if campos:
            filtros.append({"onde": "relatório", "campos": campos})
    for p in ordem + [x for x in todas if x not in ordem]:
        pj = fonte.json(f"{base}pages/{p}/page.json")
        if campos_em(pj.get("filterConfig")):
            filtros.append({"onde": f"página {pj.get('displayName')}", "campos": campos_em(pj.get("filterConfig"))})
        visuais = []
        prefixo = f"{base}pages/{p}/visuals/"
        for n in sorted(x for x in nomes if x.startswith(prefixo) and x.endswith("/visual.json")):
            vj = fonte.json(n)
            if campos_em(vj.get("filterConfig")):
                filtros.append({"onde": f"visual {vj.get('name')} (página {pj.get('displayName')})",
                                "campos": campos_em(vj.get("filterConfig"))})
            pos = vj.get("position", {})
            v = {"nome": vj.get("name"), "x": pos.get("x"), "y": pos.get("y"),
                 "largura": pos.get("width"), "altura": pos.get("height"), "oculto": vj.get("isHidden", False)}
            if "visual" not in vj:
                v.update(tipo="grupo" if "visualGroup" in vj else "desconhecido", campos={})
            else:
                vis = vj["visual"]
                estado = vis.get("query", {}).get("queryState", {})
                campos = {papel: [descrever_campo(pr["field"]) for pr in conteudo.get("projections", [])]
                          for papel, conteudo in estado.items()}
                v.update(tipo=vis.get("visualType"), titulo=titulo_visual(vis.get("visualContainerObjects", {})),
                         campos=campos)
            visuais.append(v)
        paginas.append({"nome": p, "exibicao": pj.get("displayName"),
                        "oculta": pj.get("visibility") == "HiddenInViewMode",
                        "largura": pj.get("width"), "altura": pj.get("height"), "visuais": visuais})
    return {"formato": "PBIR", "paginas": paginas, "filtros": filtros}


def relatorio(alvo: dict) -> dict:
    fonte: Fonte | None = alvo["fonte_relatorio"]
    if fonte is None:
        raise Erro("Não achei o relatório.")
    pre = alvo["prefixo_relatorio"]
    if fonte.existe(f"{pre}Layout"):
        r = relatorio_layout(fonte, f"{pre}Layout")
    elif fonte.existe(f"{pre}definition/pages/pages.json"):
        r = relatorio_pbir(fonte, f"{pre}definition/")
    elif fonte.existe("report.json"):
        raise Erro("Relatório de PBIP no formato report.json: ainda não suportado. Salve o PBIP em PBIR.")
    else:
        raise Erro("Não achei o relatório (nem Report/Layout, nem PBIR).")
    for p in r["paginas"]:  # ordem de leitura: de cima para baixo, da esquerda para a direita
        p["visuais"].sort(key=lambda v: (v.get("y") or 0, v.get("x") or 0))
    return r


def relatorio_texto(r: dict, arquivo: str) -> str:
    total = sum(len(p["visuais"]) for p in r["paginas"])
    linhas = [f"Relatório: {arquivo}: formato {r['formato']}, {plural(len(r['paginas']), 'página')}, "
              f"{plural(total, 'visual', 'visuais')}"]
    uso: dict[tuple, int] = {}
    for i, p in enumerate(r["paginas"], 1):
        oculta = " (oculta)" if p.get("oculta") else ""
        linhas.append(f"\n{i}. {p['exibicao']}{oculta}: {plural(len(p['visuais']), 'visual', 'visuais')} [{p['nome']}]")
        for v in p["visuais"]:
            partes = []
            for papel, campos in v.get("campos", {}).items():
                itens = []
                for c in campos:
                    extra = c.get("agregacao") or ("medida" if c["tipo"] == "medida" else "")
                    if c.get("nivel"):
                        extra = (extra + ", " if extra else "") + c["nivel"]
                    itens.append(c["campo"] + (f" ({extra})" if extra else ""))
                    uso[(c["campo"], c["tipo"])] = uso.get((c["campo"], c["tipo"]), 0) + 1
                partes.append(f"{papel} = " + ", ".join(itens))
            titulo = f' "{v["titulo"]}"' if v.get("titulo") else ""
            oculto = " (oculto)" if v.get("oculto") else ""
            pos = f"@{v.get('x', 0):.0f},{v.get('y', 0):.0f} {v.get('largura', 0):.0f}x{v.get('altura', 0):.0f}"
            linhas.append(f"   - {v['tipo']}{titulo}{oculto} {pos}: " + ("; ".join(partes) or "sem campos")
                          + f" [{v['nome']}]")
    if r.get("filtros"):
        linhas.append("\nFiltros:")
        for f in r["filtros"]:
            linhas.append(f"   - {f['onde']}: " + ", ".join(c["campo"] for c in f["campos"]))
    medidas = sorted((c, n) for (c, t), n in uso.items() if t == "medida")
    colunas = sorted((c, n) for (c, t), n in uso.items() if t != "medida")
    linhas.append(f"\nCampos usados: {len(medidas)} medidas, {len(colunas)} colunas")
    if medidas:
        linhas.append("  medidas: " + ", ".join(f"{c} ({n})" for c, n in medidas))
    if colunas:
        linhas.append("  colunas: " + ", ".join(f"{c} ({n})" for c, n in colunas))
    return "\n".join(linhas)


# ---------------------------------------------------------------- modelo

def fontes_do_m(tabelas: list[dict]) -> list[dict]:
    achadas: dict[str, list[str]] = {}
    for t in tabelas:
        for funcao, args in FONTE_M.findall(t.get("m") or ""):
            chave = f"{funcao}({' '.join(args.split())})"
            achadas.setdefault(chave, []).append(t["nome"])
    return [{"fonte": k, "tabelas": sorted(set(v))} for k, v in sorted(achadas.items())]


def juntar(valor) -> str | None:
    if valor is None:
        return None
    return "\n".join(valor) if isinstance(valor, list) else str(valor)


def vazio(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or v == ""


def modelo_pbix(caminho: Path) -> dict:
    try:
        from pbixray import PBIXRay
    except ImportError:
        raise Erro("O PBIXRay não está instalado neste Python. Rode a skill configurar do plugin pbi.")
    m = PBIXRay(str(caminho))
    tabelas_meta = m.tmschema_tables
    colunas_meta = m.tmschema_columns
    m_por_tabela = dict(zip(m.power_query["TableName"], m.power_query["Expression"])) if len(m.power_query) else {}
    dax_tabela = dict(zip(m.dax_tables["TableName"], m.dax_tables["Expression"])) if len(m.dax_tables) else {}

    # Formato das medidas: só pela conexão SQLite interna do PBIXRay (API privada; versão fixa).
    formatos, aviso = {}, None
    try:
        cur = m._metadata._meta._db.conn.cursor()
        for tab, nome, fmt in cur.execute(
                "SELECT t.Name, ms.Name, ms.FormatString FROM Measure ms JOIN [Table] t ON ms.TableID = t.ID"):
            formatos[(tab, nome)] = fmt
    except Exception as e:  # a parte interna mudou numa versão nova do PBIXRay
        aviso = f"Formato das medidas indisponível nesta versão do PBIXRay ({type(e).__name__})."

    # O tmschema_tables deixa de fora as tabelas automáticas de data; m.tables traz todas.
    meta_por_nome = {t["Name"]: t for _, t in tabelas_meta.iterrows()}
    nomes = list(meta_por_nome) + [n for n in m.tables if n not in meta_por_nome]
    tabelas = []
    for nome in nomes:
        t = meta_por_nome.get(nome, {"IsHidden": True, "Description": None})
        cols = colunas_meta[colunas_meta["TableName"] == nome]
        colunas = []
        for _, c in cols.iterrows():
            if c["Type"] in (3, "RowNumber") or str(c["Name"]).startswith("RowNumber-"):
                continue
            calc = c["Type"] in (2, "Calculated") or not vazio(c.get("Expression"))
            colunas.append({"nome": c["Name"], "tipo_dado": TIPOS_DADO.get(c["DataType"], str(c["DataType"])),
                            "calculada": bool(calc), "expressao": None if vazio(c.get("Expression")) else c["Expression"],
                            "formato": None if vazio(c.get("FormatString")) else c["FormatString"],
                            "oculta": bool(c.get("IsHidden")),
                            "descricao": None if vazio(c.get("Description")) else c["Description"]})
        medidas = []
        for _, ms in m.dax_measures[m.dax_measures["TableName"] == nome].iterrows():
            fmt = formatos.get((nome, ms["Name"]))
            medidas.append({"nome": ms["Name"], "expressao": None if vazio(ms.get("Expression")) else ms["Expression"],
                            "formato": None if vazio(fmt) else fmt,
                            "pasta": None if vazio(ms.get("DisplayFolder")) else ms["DisplayFolder"],
                            "descricao": None if vazio(ms.get("Description")) else ms["Description"]})
        tabelas.append({"nome": nome, "automatica": bool(AUTOMATICAS.match(nome)),
                        "oculta": bool(t.get("IsHidden")),
                        "categoria": None if vazio(t.get("DataCategory")) else t.get("DataCategory"),
                        "descricao": None if vazio(t.get("Description")) else t["Description"],
                        "colunas": colunas, "medidas": medidas,
                        "m": m_por_tabela.get(nome), "dax": dax_tabela.get(nome)})

    relacoes = []
    for _, r in m.relationships.iterrows():
        relacoes.append({"de": f"{r['FromTableName']}[{r['FromColumnName']}]",
                         "para": f"{r['ToTableName']}[{r['ToColumnName']}]",
                         "ativa": bool(r["IsActive"]),
                         "direcao": "ambas" if str(r["CrossFilteringBehavior"]).lower().startswith("both") else "única",
                         "cardinalidade": str(r["Cardinality"])})

    estat = []
    for _, s in m.statistics.iterrows():
        kb = (s["Dictionary"] + s["HashIndex"] + s["DataSize"]) / 1024
        estat.append({"coluna": f"{s['TableName']}[{s['ColumnName']}]", "kb": round(kb, 1),
                      "cardinalidade": int(s["Cardinality"])})
    estat.sort(key=lambda e: -e["kb"])
    saida = {"origem": "PBIXRay", "tabelas": tabelas, "relacoes": relacoes, "estatisticas": estat}
    if aviso:
        saida["aviso"] = aviso
    return saida


def modelo_pbit(z: zipfile.ZipFile) -> dict:
    esquema = json.loads(texto(z.read("DataModelSchema")))["model"]
    tabelas = []
    for t in esquema.get("tables", []):
        colunas = [{"nome": c["name"], "tipo_dado": TIPOS_TMDL.get(c.get("dataType"), c.get("dataType")),
                    "calculada": c.get("type") == "calculated",
                    "expressao": juntar(c.get("expression")), "formato": c.get("formatString"),
                    "oculta": bool(c.get("isHidden")), "descricao": c.get("description")}
                   for c in t.get("columns", []) if c.get("type") != "rowNumber"]
        medidas = [{"nome": ms["name"], "expressao": juntar(ms.get("expression")),
                    "formato": ms.get("formatString"), "pasta": ms.get("displayFolder"),
                    "descricao": ms.get("description")} for ms in t.get("measures", [])]
        m_txt = dax = None
        for p in t.get("partitions", []):
            src = p.get("source", {})
            if src.get("type") == "m":
                m_txt = juntar(src.get("expression"))
            elif src.get("type") == "calculated":
                dax = juntar(src.get("expression"))
        tabelas.append({"nome": t["name"], "automatica": bool(AUTOMATICAS.match(t["name"])),
                        "oculta": bool(t.get("isHidden")), "descricao": t.get("description"),
                        "categoria": t.get("dataCategory"),
                        "colunas": colunas, "medidas": medidas, "m": m_txt, "dax": dax})
    relacoes = []
    for r in esquema.get("relationships", []):
        card = f"{'1' if r.get('fromCardinality') == 'one' else 'M'}:{'M' if r.get('toCardinality') == 'many' else '1'}"
        relacoes.append({"de": f"{r['fromTable']}[{r['fromColumn']}]", "para": f"{r['toTable']}[{r['toColumn']}]",
                         "ativa": r.get("isActive", True),
                         "direcao": "ambas" if r.get("crossFilteringBehavior") == "bothDirections" else "única",
                         "cardinalidade": card})
    return {"origem": "DataModelSchema", "tabelas": tabelas, "relacoes": relacoes, "estatisticas": []}


TIPOS_TMDL = {"int64": "inteiro", "string": "texto", "double": "decimal", "dateTime": "data/hora",
              "decimal": "decimal fixo", "boolean": "booleano", "binary": "binário"}
CABECALHO_TMDL = re.compile(r"^(table|measure|column|partition|hierarchy|calculationItem|relationship)\s+"
                            r"('(?:[^']|'')*'|[^\s=]+)\s*(?:=\s*(.*))?$")


def nome_tmdl(bruto: str) -> str:
    return bruto[1:-1].replace("''", "'") if bruto.startswith("'") else bruto


def recuo(linha: str) -> int:
    return len(linha) - len(linha.lstrip("\t"))


def bloco_tmdl(linhas: list[str], i: int, inicio: str, nivel: int) -> tuple[str, int]:
    """Lê uma expressão que começa em `inicio` e pode continuar nas linhas mais indentadas."""
    if inicio.startswith("```"):
        partes, i = [inicio[3:]], i + 1
        while i < len(linhas) and "```" not in linhas[i]:
            partes.append(linhas[i].strip("\t"))
            i += 1
        return "\n".join(p for p in partes if p.strip()), i + 1
    partes = [inicio] if inicio.strip() else []
    i += 1
    corpo = []
    while i < len(linhas) and (not linhas[i].strip() or recuo(linhas[i]) > nivel + 1):
        corpo.append(linhas[i])
        i += 1
    while corpo and not corpo[-1].strip():  # a linha em branco antes da próxima propriedade
        corpo.pop()
    if corpo:
        base = min(recuo(l) for l in corpo if l.strip())
        partes += [l[base:] if l.strip() else "" for l in corpo]
    return "\n".join(partes).strip("\n"), i


def tabela_tmdl(texto_tmdl: str) -> dict | None:
    linhas = texto_tmdl.replace("\r\n", "\n").split("\n")
    tabela, objeto, descricao, i = None, None, [], 0
    while i < len(linhas):
        linha = linhas[i]
        nivel, conteudo = recuo(linha), linha.strip()
        if conteudo.startswith("///"):
            descricao.append(conteudo[3:].strip())
            i += 1
            continue
        cab = CABECALHO_TMDL.match(conteudo)
        if cab and nivel <= 1:
            tipo, nome, resto = cab.group(1), nome_tmdl(cab.group(2)), cab.group(3)
            desc = " ".join(descricao) or None
            descricao = []
            if tipo == "table" and nivel == 0:
                tabela = {"nome": nome, "automatica": bool(AUTOMATICAS.match(nome)), "oculta": False,
                          "descricao": desc, "categoria": None, "colunas": [], "medidas": [], "m": None, "dax": None}
                objeto, i = tabela, i + 1
                continue
            if tabela is None:
                i += 1
                continue
            expr = None
            if resto is not None and tipo in ("measure", "column"):
                expr, i = bloco_tmdl(linhas, i, resto, nivel)
            else:
                i += 1
            if tipo == "measure":
                objeto = {"nome": nome, "expressao": expr, "formato": None, "pasta": None, "descricao": desc}
                tabela["medidas"].append(objeto)
            elif tipo == "column":
                objeto = {"nome": nome, "tipo_dado": None, "calculada": expr is not None, "expressao": expr,
                          "formato": None, "oculta": False, "descricao": desc}
                tabela["colunas"].append(objeto)
            elif tipo == "partition":
                objeto = {"_particao": (resto or "").strip()}
            else:
                objeto = {}
            continue
        if objeto is not None and conteudo:
            if conteudo.startswith("source =") or conteudo == "source=":
                inicio = conteudo.split("=", 1)[1].strip()
                expr, i = bloco_tmdl(linhas, i, inicio, nivel)
                if objeto.get("_particao") == "m":
                    tabela["m"] = expr
                elif objeto.get("_particao") == "calculated":
                    tabela["dax"] = expr
                continue
            chave, _, valor = conteudo.partition(":")
            valor = valor.strip()
            if objeto is tabela and conteudo == "isHidden":
                tabela["oculta"] = True
            elif chave == "formatString" and "formato" in objeto:
                objeto["formato"] = valor
            elif chave == "dataCategory" and objeto is tabela:
                tabela["categoria"] = valor
            elif chave == "displayFolder" and "pasta" in objeto:
                objeto["pasta"] = valor
            elif chave == "dataType" and "tipo_dado" in objeto:
                objeto["tipo_dado"] = TIPOS_TMDL.get(valor, valor)
            elif conteudo == "isHidden" and "oculta" in objeto:
                objeto["oculta"] = True
        i += 1
    return tabela


def coluna_tmdl(ref: str) -> str:
    """'Tabela X'.'Coluna Y' ou Tabela.Coluna → Tabela X[Coluna Y]."""
    m = re.match(r"^('(?:[^']|'')*'|[^.']+)\.(.+)$", ref.strip())
    if not m:
        return ref
    return f"{nome_tmdl(m.group(1))}[{nome_tmdl(m.group(2))}]"


def modelo_tmdl(pasta: Path) -> dict:
    definicao = pasta / "definition"
    if not definicao.is_dir():
        if (pasta / "model.bim").is_file():
            raise Erro("O modelo deste PBIP está em model.bim (formato antigo); ainda não suportado.")
        raise Erro(f"Não achei a pasta definition do modelo em {pasta}.")
    tabelas = [t for t in (tabela_tmdl(texto(p.read_bytes()))
                           for p in sorted((definicao / "tables").glob("*.tmdl"))) if t]
    # A ordem das tabelas é a do model.tmdl (ref table ...), a mesma do Desktop.
    if (definicao / "model.tmdl").is_file():
        ordem = [nome_tmdl(m.group(1)) for m in re.finditer(r"^ref table\s+('(?:[^']|'')*'|\S+)",
                                                            texto((definicao / "model.tmdl").read_bytes()), re.M)]
        posicao = {n: i for i, n in enumerate(ordem)}
        tabelas.sort(key=lambda t: posicao.get(t["nome"], len(posicao)))
    relacoes = []
    arq_rel = definicao / "relationships.tmdl"
    if arq_rel.is_file():
        atual = None
        for linha in texto(arq_rel.read_bytes()).replace("\r\n", "\n").split("\n"):
            conteudo = linha.strip()
            if conteudo.startswith("relationship "):
                atual = {"de": None, "para": None, "ativa": True, "direcao": "única", "_de": "many", "_para": "one"}
                relacoes.append(atual)
            elif atual is not None and ":" in conteudo:
                chave, _, valor = conteudo.partition(":")
                valor = valor.strip()
                if chave == "fromColumn":
                    atual["de"] = coluna_tmdl(valor)
                elif chave == "toColumn":
                    atual["para"] = coluna_tmdl(valor)
                elif chave == "isActive":
                    atual["ativa"] = valor.lower() != "false"
                elif chave == "crossFilteringBehavior":
                    atual["direcao"] = "ambas" if valor == "bothDirections" else "única"
                elif chave == "fromCardinality":
                    atual["_de"] = valor
                elif chave == "toCardinality":
                    atual["_para"] = valor
        for r in relacoes:
            r["cardinalidade"] = f"{'1' if r.pop('_de') == 'one' else 'M'}:{'M' if r.pop('_para') == 'many' else '1'}"
    return {"origem": "TMDL", "tabelas": tabelas, "relacoes": relacoes, "estatisticas": []}


def modelo(alvo: dict) -> dict:
    if alvo["tipo"] == "pbip":
        pasta = alvo["fonte_modelo"]
        if not pasta:
            raise Erro("Não achei a pasta .SemanticModel do PBIP.")
        dados = modelo_tmdl(pasta)
        dados["fontes"] = fontes_do_m(dados["tabelas"])
        return dados
    if alvo["tipo"] == "pbit":
        dados = modelo_pbit(alvo["zip"])
    else:
        if "DataModel" not in alvo["zip"].namelist():
            raise Erro("Este PBIX não tem o DataModel (conexão ao vivo ou DirectQuery a um modelo publicado?).")
        dados = modelo_pbix(alvo["caminho"])
    dados["fontes"] = fontes_do_m(dados["tabelas"])
    return dados


def modelo_texto(d: dict, arquivo: str, tudo: bool) -> str:
    reais = [t for t in d["tabelas"] if tudo or not t["automatica"]]
    autos = [t for t in d["tabelas"] if t["automatica"]]
    n_med = sum(len(t["medidas"]) for t in reais)
    n_calc = sum(sum(c["calculada"] for c in t["colunas"]) for t in reais if not t["automatica"])
    rels = [r for r in d["relacoes"] if tudo or not AUTOMATICAS.match(r["para"])]
    linhas = [f"Modelo: {arquivo} (lido de {d['origem']}): {plural(len(reais), 'tabela')}, "
              f"{plural(n_med, 'medida')}, {plural(n_calc, 'coluna calculada', 'colunas calculadas')}, "
              f"{plural(len(rels), 'relação', 'relações')}"]
    if autos and not tudo:
        linhas.append(f"Auto data/hora ligada: {len(autos)} tabelas automáticas de data (use --tudo para ver).")
    if d.get("aviso"):
        linhas.append(f"Aviso: {d['aviso']}")
    if d.get("fontes"):
        linhas.append("\nFontes:")
        for f in d["fontes"]:
            linhas.append(f"  - {f['fonte']}: {', '.join(f['tabelas'])}")
    for t in reais:
        oculta = " (oculta)" if t["oculta"] else ""
        linhas.append(f"\n## {t['nome']}{oculta}: {len(t['colunas'])} colunas, {len(t['medidas'])} medidas")
        if t.get("descricao"):
            linhas.append(f"Descrição: {t['descricao']}")
        if t["medidas"]:
            linhas.append("Medidas:")
            for ms in t["medidas"]:
                fmt = f"formato: {ms['formato']}" if ms.get("formato") else "SEM FORMATO"
                expr = (ms["expressao"] or "").strip()
                if "\n" in expr:
                    expr = "\n      " + expr.replace("\n", "\n      ")
                linhas.append(f"  - {ms['nome']} = {expr}   [{fmt}]")
        calc = [c for c in t["colunas"] if c["calculada"]]
        if calc:
            linhas.append("Colunas calculadas:")
            for c in calc:
                linhas.append(f"  - {c['nome']} = {(c['expressao'] or '').strip()}")
        dados = [c for c in t["colunas"] if not c["calculada"]]
        if dados:
            linhas.append("Colunas: " + ", ".join(f"{c['nome']} ({c['tipo_dado']})" for c in dados))
        if t.get("dax"):
            linhas.append(f"Tabela calculada (DAX): {t['dax'].strip()}")
        if t.get("m"):
            linhas.append("M:\n    " + t["m"].strip().replace("\n", "\n    "))
    if rels:
        linhas.append("\nRelações:")
        for r in rels:
            marcas = [r["cardinalidade"], "ativa" if r["ativa"] else "INATIVA",
                      "AMBAS AS DIREÇÕES" if r["direcao"] == "ambas" else "direção única"]
            linhas.append(f"  - {r['de']} → {r['para']} ({', '.join(marcas)})")
    if d.get("estatisticas"):
        top = [e for e in d["estatisticas"] if tudo or not AUTOMATICAS.match(e["coluna"])][:10]
        linhas.append("\nColunas que mais pesam:")
        for e in top:
            linhas.append(f"  - {e['coluna']}: {numero(e['kb'], 1)} KB, cardinalidade {numero(e['cardinalidade'])}")
    return "\n".join(linhas)


# ---------------------------------------------------------------- linhagem

REF_QUALIFICADA = re.compile(r"(?:'((?:[^']|'')+)'|([^\W\d][\w.]*))\s*\[([^\]]+)\]")
REF_SOLTA = re.compile(r"\[([^\]]+)\]")
NAVEGACAO_M = re.compile(r'\{\[\s*Schema\s*=\s*"([^"]+)"\s*,\s*Item\s*=\s*"([^"]+)"\s*\]\}'
                         r'|\{\[\s*(?:Name|Item)\s*=\s*"([^"]+)"[^\]]*\]\}')
PASSO_M = re.compile(r'^\s*(#"(?:[^"]|"")+"|[^\W\d]\w*)\s*=\s*([A-Z][A-Za-z]*\.[A-Za-z]+)\s*\(', re.M)


def limpa_dax(expr: str) -> str:
    expr = re.sub(r"/\*.*?\*/", " ", expr or "", flags=re.S)
    expr = re.sub(r'"(?:[^"]|"")*"', '""', expr)
    return re.sub(r"(//|--)[^\n]*", " ", expr)


def refs_dax(expr: str, tabela_dona: str, medidas: dict) -> tuple[set, set]:
    """Devolve (medidas, colunas) citadas numa expressão DAX. Colunas como 'Tabela[Coluna]'."""
    texto_dax = limpa_dax(expr)
    usa_medidas, usa_colunas, ocupado = set(), set(), []
    for m in REF_QUALIFICADA.finditer(texto_dax):
        tabela = (m.group(1) or "").replace("''", "'") or m.group(2)
        nome = m.group(3)
        ocupado.append(m.span())
        if nome in medidas and medidas[nome]["tabela"] == tabela:
            usa_medidas.add(nome)
        else:
            usa_colunas.add(f"{tabela}[{nome}]")
    for m in REF_SOLTA.finditer(texto_dax):
        if any(a <= m.start() < b for a, b in ocupado):
            continue
        nome = m.group(1)
        if nome in medidas:
            usa_medidas.add(nome)
        else:
            usa_colunas.add(f"{tabela_dona}[{nome}]")
    return usa_medidas, usa_colunas


def origem_m(m_txt: str | None) -> dict:
    if not m_txt:
        return {}
    fontes = [f"{f}({' '.join(a.split())})" for f, a in FONTE_M.findall(m_txt)]
    nav = NAVEGACAO_M.search(m_txt)
    objeto = None
    if nav:
        objeto = f"{nav.group(1)}.{nav.group(2)}" if nav.group(1) else nav.group(3)
    passos = [{"passo": p.strip("#").strip('"'), "funcao": f} for p, f in PASSO_M.findall(m_txt)]
    return {"fontes": fontes, "objeto": objeto, "sql_proprio": bool(re.search(r"\bQuery\s*=", m_txt)),
            "passos": passos}


def linhagem(alvo: dict) -> dict:
    rel = relatorio(alvo)
    mod = modelo(alvo)
    tabelas = {t["nome"]: t for t in mod["tabelas"]}
    medidas = {ms["nome"]: {**ms, "tabela": t["nome"]} for t in mod["tabelas"] for ms in t["medidas"]}
    colunas = {f"{t['nome']}[{c['nome']}]": {**c, "tabela": t["nome"]} for t in mod["tabelas"] for c in t["colunas"]}

    deps_medida: dict[str, tuple[set, set]] = {}
    for nome, ms in medidas.items():
        deps_medida[nome] = refs_dax(ms["expressao"], ms["tabela"], medidas)
    deps_coluna = {chave: refs_dax(c["expressao"], c["tabela"], medidas)
                   for chave, c in colunas.items() if c["calculada"]}

    def fecha(med_iniciais: set, col_iniciais: set) -> tuple[set, set]:
        meds, cols, fila = set(), set(), list(med_iniciais) + list(col_iniciais)
        while fila:
            item = fila.pop()
            if item in medidas:
                if item in meds:
                    continue
                meds.add(item)
                fila += list(deps_medida[item][0]) + list(deps_medida[item][1])
            else:
                if item in cols:
                    continue
                cols.add(item)
                if item in deps_coluna:
                    fila += list(deps_coluna[item][0]) + list(deps_coluna[item][1])
        return meds, cols

    visuais, usados_m, usadas_c, inexistentes = [], set(), set(), set()
    for p in rel["paginas"]:
        for v in p["visuais"]:
            campos = []
            for papel, lista in v.get("campos", {}).items():
                for c in lista:
                    campo = c["campo"]
                    nome_medida = campo.split("[", 1)[1][:-1] if "[" in campo else campo
                    if c["tipo"] == "medida" and nome_medida in medidas:
                        meds, cols = fecha({nome_medida}, set())
                    elif campo in colunas:
                        meds, cols = fecha(set(), {campo})
                    else:
                        inexistentes.add(campo)
                        meds, cols = set(), set()
                    usados_m |= meds
                    usadas_c |= cols
                    campos.append({"papel": papel, "campo": campo, "tipo": c["tipo"],
                                   "agregacao": c.get("agregacao"), "medidas": sorted(meds - {nome_medida}),
                                   "colunas": sorted(cols), "existe": campo in colunas or nome_medida in medidas})
            visuais.append({"pagina": p["exibicao"], "visual": v["nome"], "tipo": v["tipo"],
                            "titulo": v.get("titulo"), "campos": campos})
    for f in rel.get("filtros", []):  # campo usado só em filtro também conta como usado
        for c in f["campos"]:
            nome_medida = c["campo"].split("[", 1)[1][:-1] if "[" in c["campo"] else c["campo"]
            if c["tipo"] == "medida" and nome_medida in medidas:
                meds, cols = fecha({nome_medida}, set())
            elif c["campo"] in colunas:
                meds, cols = fecha(set(), {c["campo"]})
            else:
                inexistentes.add(c["campo"])
                meds, cols = set(), set()
            usados_m |= meds
            usadas_c |= cols

    tabelas_usadas = {colunas[c]["tabela"] for c in usadas_c if c in colunas} | {medidas[m]["tabela"] for m in usados_m}
    relacoes_usadas = [r for r in mod["relacoes"]
                       if r["de"].split("[")[0] in tabelas_usadas or r["para"].split("[")[0] in tabelas_usadas]
    # Tabela sem campo usado, mas ligada a duas ou mais tabelas usadas: é ponte nas relações (não sobra).
    vizinhas: dict[str, set] = {}
    for r in mod["relacoes"]:
        a, b = r["de"].split("[")[0], r["para"].split("[")[0]
        vizinhas.setdefault(a, set()).add(b)
        vizinhas.setdefault(b, set()).add(a)
    pontes = {t: sorted(v & tabelas_usadas) for t, v in vizinhas.items()
              if t not in tabelas_usadas and len(v & tabelas_usadas) >= 2}
    nao_usadas_m = sorted(m for m, ms in medidas.items() if m not in usados_m and not AUTOMATICAS.match(ms["tabela"]))
    nao_usadas_calc = sorted(c for c, col in colunas.items()
                             if col["calculada"] and c not in usadas_c and not AUTOMATICAS.match(col["tabela"]))
    return {
        "visuais": visuais,
        "filtros": rel.get("filtros", []),
        "usadas_colunas": sorted(usadas_c),
        "medidas": {m: {"tabela": medidas[m]["tabela"], "expressao": medidas[m]["expressao"],
                        "formato": medidas[m].get("formato"), "usa_medidas": sorted(deps_medida[m][0]),
                        "usa_colunas": sorted(deps_medida[m][1])} for m in sorted(usados_m)},
        "colunas_calculadas": {c: {"expressao": colunas[c]["expressao"],
                                   "usa": sorted(deps_coluna[c][0] | deps_coluna[c][1])}
                               for c in sorted(usadas_c) if c in deps_coluna},
        "tabelas": {t: {**origem_m(tabelas[t].get("m")), "dax": tabelas[t].get("dax")}
                    for t in sorted(tabelas_usadas) if t in tabelas},
        "relacoes": relacoes_usadas,
        "nao_usados": {"medidas": nao_usadas_m, "colunas_calculadas": nao_usadas_calc,
                       "tabelas": [{"tabela": t, "ponte_entre": pontes.get(t, [])} for t in sorted(
                           t for t, d in tabelas.items() if t not in tabelas_usadas and not d["automatica"])]},
        "inexistentes": sorted(inexistentes),
    }


def linhagem_texto(d: dict, arquivo: str) -> str:
    paginas = list(dict.fromkeys(v["pagina"] for v in d["visuais"]))
    linhas = [f"Linhagem: {arquivo}",
              f"Totais: {plural(len(paginas), 'página')} com visuais, {plural(len(d['visuais']), 'visual', 'visuais')}, "
              f"{plural(len(d['medidas']), 'medida usada', 'medidas usadas')}, "
              f"{plural(len(d['tabelas']), 'tabela usada', 'tabelas usadas')}",
              "(visual → campo → medidas → colunas; depois cada medida, cada tabela e a origem)"]
    pagina = None
    for v in d["visuais"]:
        if v["pagina"] != pagina:
            pagina = v["pagina"]
            linhas.append(f"\n## {pagina}")
        titulo = f' "{v["titulo"]}"' if v.get("titulo") else ""
        linhas.append(f"- {v['tipo']}{titulo} [{v['visual']}]")
        for c in v["campos"]:
            extra = []
            if c.get("agregacao"):
                extra.append(c["agregacao"])
            if not c["existe"]:
                extra.append("NÃO EXISTE NO MODELO")
            rotulo = f"{c['papel']}: {c['campo']}" + (f" ({', '.join(extra)})" if extra else "")
            caminho = []
            if c["medidas"]:
                caminho.append("medidas " + ", ".join(c["medidas"]))
            outras = [x for x in c["colunas"] if x != c["campo"]]
            if outras:
                rotulo_col = "colunas " if c["tipo"] == "medida" else "coluna calculada, usa "
                caminho.append(rotulo_col + ", ".join(outras))
            linhas.append(f"    {rotulo}" + (f" → {'; '.join(caminho)}" if caminho else ""))
    if d.get("filtros"):
        linhas.append("\n## Filtros")
        for f in d["filtros"]:
            linhas.append(f"- {f['onde']}: " + ", ".join(c["campo"] for c in f["campos"]))
    if d["medidas"]:
        linhas.append("\n## Medidas usadas")
        for nome, ms in d["medidas"].items():
            fmt = f"formato {ms['formato']}" if ms.get("formato") else "SEM FORMATO"
            linhas.append(f"- {nome} ({ms['tabela']}, {fmt}) = {' '.join((ms['expressao'] or '').split())}")
    if d["colunas_calculadas"]:
        linhas.append("\n## Colunas calculadas usadas")
        for nome, c in d["colunas_calculadas"].items():
            linhas.append(f"- {nome} = {' '.join((c['expressao'] or '').split())}")
    linhas.append("\n## Tabelas e origem")
    for nome, t in d["tabelas"].items():
        if t.get("dax"):
            linhas.append(f"- {nome}: tabela calculada em DAX: {' '.join(t['dax'].split())}")
            continue
        origem = ", ".join(t.get("fontes") or ["fonte não identificada no M"])
        objeto = f" → {t['objeto']}" if t.get("objeto") else ""
        sql = " (consulta SQL escrita à mão)" if t.get("sql_proprio") else ""
        passos = [p["passo"] for p in t.get("passos", [])[1:]]
        linhas.append(f"- {nome} ← {origem}{objeto}{sql}" + (f"; passos: {', '.join(passos)}" if passos else ""))
    if d["relacoes"]:
        linhas.append("\n## Relações entre as tabelas usadas")
        for r in d["relacoes"]:
            if AUTOMATICAS.match(r["para"]):
                continue
            marcas = [r["cardinalidade"], "ativa" if r["ativa"] else "INATIVA",
                      "AMBAS AS DIREÇÕES" if r["direcao"] == "ambas" else "direção única"]
            linhas.append(f"- {r['de']} → {r['para']} ({', '.join(marcas)})")
    nu = d["nao_usados"]
    linhas.append("\n## Não usados por nenhum visual")
    linhas.append("- medidas: " + (", ".join(nu["medidas"]) or "nenhuma"))
    linhas.append("- colunas calculadas: " + (", ".join(nu["colunas_calculadas"]) or "nenhuma"))
    sobras = [t["tabela"] for t in nu["tabelas"] if not t["ponte_entre"]]
    pontes = [f"{t['tabela']} (ponte entre {' e '.join(t['ponte_entre'])})" for t in nu["tabelas"] if t["ponte_entre"]]
    linhas.append("- tabelas: " + (", ".join(sobras) or "nenhuma"))
    if pontes:
        linhas.append("- tabelas sem campo em visual, mas que ligam outras nas relações (não apagar): "
                      + ", ".join(pontes))
    if d["inexistentes"]:
        linhas.append("\n## Campos dos visuais que não existem no modelo (visual quebrado)")
        linhas += [f"- {c}" for c in d["inexistentes"]]
    return "\n".join(linhas)


# ---------------------------------------------------------------- revisão

FONTES_COM_FOLDING = re.compile(r"\b(Sql\.Databases?|Oracle\.Database|PostgreSQL\.Database|MySQL\.Database|"
                                r"Odbc\.(?:DataSource|Query)|OleDb\.DataSource|Teradata\.Database|Snowflake\.Databases|"
                                r"GoogleBigQuery\.Database|AmazonRedshift\.Database|Databricks\.\w+|SapHana\.Database|"
                                r"DB2\.Database)\s*\(")
QUEBRA_FOLDING = {"Table.AddIndexColumn", "Table.Buffer", "List.Buffer", "Table.FillDown", "Table.FillUp",
                  "Table.Transpose", "Table.AddRankColumn"}
AGREGA_DAX = re.compile(r"\b(CALCULATE|CALCULATETABLE|SUMX?|AVERAGEX?|COUNTX?|COUNTA|COUNTROWS|DISTINCTCOUNT|"
                        r"MINX?|MAXX?|RELATEDTABLE|FILTER|ALL|ALLEXCEPT|SUMMARIZE)\s*\(", re.I)
SUFIXO_CHAVE = re.compile(r"(Key|SK|ID|Id|_id|Cod|Codigo|Código)$")
TABELA_DATA = re.compile(r"(date|data|calend|tempo|time|dcal|dim_?tempo)", re.I)
SEVERIDADES = ("alta", "média", "baixa")


def revisar(alvo: dict) -> dict:
    mod = modelo(alvo)
    try:
        lin = linhagem(alvo)
    except Erro:
        lin = None  # sem relatório: revisa só o modelo
    achados = []

    def achado(regra, severidade, objeto, detalhe, como_corrigir):
        achados.append({"regra": regra, "severidade": severidade, "objeto": objeto,
                        "detalhe": detalhe, "como_corrigir": como_corrigir})

    if lin is not None and not lin["visuais"]:
        lin = None  # relatório sem visuais: as regras de uso não fazem sentido
        sem_visuais = True
    else:
        sem_visuais = False
    reais = [t for t in mod["tabelas"] if not t["automatica"]]
    usadas_c = set(lin["usadas_colunas"]) if lin else set()
    medidas_usadas = set(lin["medidas"]) if lin else set()
    sem_formato_disponivel = bool(mod.get("aviso"))

    for t in reais:
        for ms in t["medidas"]:
            nome = f"{t['nome']}[{ms['nome']}]"
            if not ms.get("formato") and not sem_formato_disponivel:
                achado("medida sem formato", "baixa", nome,
                       "O número aparece cru: sem separador de milhar, moeda ou casas decimais fixas.",
                       "Definir o formato da medida (Ferramentas de medida → Formato, ou formatString pelo MCP).")
            if "/" in limpa_dax(ms.get("expressao")):
                achado("divisão com /", "média", nome,
                       f"{' '.join((ms['expressao'] or '').split())}. Com divisor zero ou vazio, a conta dá erro ou infinito.",
                       "Trocar a / por DIVIDE(numerador, denominador), que devolve vazio quando o divisor é zero.")
        for c in t["colunas"]:
            if c["calculada"] and AGREGA_DAX.search(limpa_dax(c.get("expressao"))):
                nome = f"{t['nome']}[{c['nome']}]"
                usada = nome in usadas_c
                achado("coluna calculada que agrega", "alta" if usada else "média", nome,
                       f"{' '.join((c['expressao'] or '').split())}. É calculada uma vez, na atualização, e não "
                       f"responde aos filtros do relatório" + (" (e está num visual)." if usada else "."),
                       "Trocar por uma medida com a mesma fórmula, e usar a medida nos visuais.")
        m_txt = t.get("m") or ""
        if FONTES_COM_FOLDING.search(m_txt):
            passos = origem_m(m_txt)["passos"]
            quebra = next((i for i, p in enumerate(passos) if p["funcao"] in QUEBRA_FOLDING), None)
            if quebra is not None:
                depois = [p["passo"] for p in passos[quebra + 1:]]
                navegacao = (re.search(r"\bSql\.Databases?\s*\(", m_txt)
                             and "CreateNavigationProperties=false" not in m_txt.replace(" ", ""))
                detalhe = (f"O passo \"{passos[quebra]['passo']}\" ({passos[quebra]['funcao']}) impede que o resto da "
                           "consulta vá para o banco" + (f": {', '.join(depois)} rodam no Power Query." if depois else "."))
                if navegacao:
                    achado("colunas de relação numa consulta sem folding", "alta", t["nome"],
                           detalhe + " E o Sql.Database cria colunas de relação: cada chave vira uma consulta ao banco, "
                           "o que pode levar a milhares de consultas na atualização.",
                           "Pôr [CreateNavigationProperties=false] no Sql.Database e levar o passo que quebra o "
                           "folding para o fim, ou fazê-lo no banco (view ou consulta).")
                else:
                    achado("passo de M que quebra o folding", "média", t["nome"], detalhe,
                           "Levar esse passo para o fim da consulta, ou fazê-lo no banco (view ou consulta).")
    autos = [t for t in mod["tabelas"] if t["automatica"]]
    if autos:
        achado("auto data/hora ligada", "baixa", "modelo",
               f"{len(autos)} tabelas automáticas de data, uma para cada coluna de data, ocultas e ocupando espaço.",
               "Desligar em Arquivo → Opções → Dados atuais → Carga de Dados → Data/hora automática, e usar uma "
               "tabela de datas própria, marcada como tabela de datas.")
    for t in reais:
        tem_data = any(c.get("tipo_dado") == "data/hora" for c in t["colunas"])
        if TABELA_DATA.search(t["nome"]) and tem_data and (t.get("categoria") or "").lower() != "time":
            achado("tabela de datas não marcada", "baixa", t["nome"],
                   "Parece a tabela de datas do modelo, mas não está marcada como tabela de datas.",
                   "Ferramentas de tabela → Marcar como tabela de datas, escolhendo a coluna de data.")
    for r in mod["relacoes"]:
        if AUTOMATICAS.match(r["para"].split("[")[0]):
            continue
        if r["direcao"] == "ambas":
            achado("relação bidirecional", "média", f"{r['de']} → {r['para']}",
                   "O filtro anda nos dois sentidos. Pode mudar números sem ninguém perceber e criar caminhos ambíguos.",
                   "Voltar para direção única e, se uma medida precisar do outro sentido, usar CROSSFILTER nela.")
        if not r["ativa"]:
            colunas_rel = {r["de"], r["para"]}
            usa = any("USERELATIONSHIP" in limpa_dax(ms.get("expressao")).upper()
                      and any(c.split("[")[1][:-1] in (ms.get("expressao") or "") for c in colunas_rel)
                      for t in reais for ms in t["medidas"])
            if not usa:
                achado("relação inativa sem uso", "baixa", f"{r['de']} → {r['para']}",
                       "Nenhuma medida ativa essa relação com USERELATIONSHIP.",
                       "Se a análise por essa data for necessária, criar medidas com USERELATIONSHIP; senão, apagar.")
    ligadas = {(r["de"], r["para"]) for r in mod["relacoes"]} | {(r["para"], r["de"]) for r in mod["relacoes"]}
    for a in reais:
        for c in a["colunas"]:
            if not SUFIXO_CHAVE.search(c["nome"]):
                continue
            base = SUFIXO_CHAVE.sub("", c["nome"]).lower()
            for b in reais:
                if b is a or len(base) < 3 or base not in b["nome"].lower():
                    continue
                if any(cb["nome"] == c["nome"] for cb in b["colunas"]):
                    par = (f"{a['nome']}[{c['nome']}]", f"{b['nome']}[{c['nome']}]")
                    if par not in ligadas:
                        achado("possível relação faltando", "média", f"{par[0]} ↔ {par[1]}",
                               "As duas tabelas têm a mesma chave, mas não há relação entre elas.",
                               "Conferir se a relação deveria existir e criá-la (muitos para um, direção única).")
    if lin:
        for campo in lin["inexistentes"]:
            achado("visual quebrado", "alta", campo,
                   "Um visual ou filtro usa um campo que não existe no modelo.",
                   "Trocar o campo no visual ou recriar a medida ou coluna que sumiu.")
        for m in lin["nao_usados"]["medidas"]:
            achado("medida sem uso", "baixa", m,
                   "Nenhum visual, filtro ou medida usada depende dela.",
                   "Conferir se é usada fora do relatório (outros relatórios, Excel); se não, apagar.")
        for c in lin["nao_usados"]["colunas_calculadas"]:
            achado("coluna calculada sem uso", "baixa", c, "Nenhum visual, filtro ou medida usada depende dela.",
                   "Conferir se é usada fora do relatório; se não, apagar.")
        if mod.get("estatisticas"):
            em_relacao = {r["de"] for r in mod["relacoes"]} | {r["para"] for r in mod["relacoes"]}
            # Coluna citada por qualquer medida ou coluna calculada (mesmo sem uso) não entra como "pesada sem uso".
            indice = {ms["nome"]: {**ms, "tabela": t["nome"]} for t in reais for ms in t["medidas"]}
            citadas = set()
            for t in reais:
                for item in t["medidas"] + [c for c in t["colunas"] if c["calculada"]]:
                    citadas |= refs_dax(item.get("expressao"), t["nome"], indice)[1]
            pesadas = [e for e in mod["estatisticas"]
                       if not AUTOMATICAS.match(e["coluna"]) and e["kb"] >= 100
                       and e["coluna"] not in usadas_c and e["coluna"] not in em_relacao and e["coluna"] not in citadas]
            if pesadas:
                total = sum(e["kb"] for e in pesadas)
                lista = "; ".join(f"{e['coluna']} ({numero(e['kb'], 1)} KB)" for e in pesadas[:10])
                achado("colunas pesadas sem uso", "média", f"{len(pesadas)} colunas, {numero(total / 1024, 1)} MB",
                       f"Nenhum visual, filtro, medida ou relação usa estas colunas: {lista}"
                       + ("; e outras." if len(pesadas) > 10 else "."),
                       "Remover as colunas no Power Query (Escolher colunas), depois de conferir que nenhum outro "
                       "relatório, ordenação ou regra de segurança (RLS) depende delas.")
    achados.sort(key=lambda a: SEVERIDADES.index(a["severidade"]))
    resumo = {s: sum(a["severidade"] == s for a in achados) for s in SEVERIDADES}
    return {"resumo": resumo, "achados": achados, "revisou_relatorio": lin is not None,
            "relatorio_sem_visuais": sem_visuais, "formato_das_medidas": not sem_formato_disponivel}


def revisar_texto(d: dict, arquivo: str) -> str:
    r = d["resumo"]
    linhas = [f"Revisão: {arquivo}",
              f"{plural(len(d['achados']), 'achado')}: {r['alta']} de severidade alta, {r['média']} média, "
              f"{r['baixa']} baixa"]
    if d.get("relatorio_sem_visuais"):
        linhas.append("Aviso: o relatório não tem visuais; as regras de uso (sem uso, colunas pesadas) foram puladas.")
    elif not d["revisou_relatorio"]:
        linhas.append("Aviso: não achei o relatório; só o modelo foi revisado.")
    if not d["formato_das_medidas"]:
        linhas.append("Aviso: o formato das medidas não pôde ser lido; a regra de medida sem formato foi pulada.")
    sev = None
    for a in d["achados"]:
        if a["severidade"] != sev:
            sev = a["severidade"]
            linhas.append(f"\n## Severidade {sev}")
        linhas.append(f"- [{a['regra']}] {a['objeto']}\n    {a['detalhe']}\n    Como corrigir: {a['como_corrigir']}")
    return "\n".join(linhas)


# ---------------------------------------------------------------- comparar

def normaliza(expr: str | None) -> str:
    return " ".join((expr or "").split())


def foto(caminho: str, com_modelo: bool = True) -> dict:
    """Retrato comparável de um arquivo: o modelo e o relatório, normalizados."""
    alvo = abrir(caminho)
    f = {"medidas": {}, "calculadas": {}, "colunas": {}, "relacoes": {}, "consultas": {}, "tabelas": [],
         "paginas": {}, "visuais": {}, "filtros": [], "avisos": []}
    if com_modelo:
        try:
            mod = modelo(alvo)
        except Erro as e:
            mod = None
            f["avisos"].append(f"modelo não lido: {e}")
        if mod:
            for t in mod["tabelas"]:
                if t["automatica"]:
                    continue
                f["tabelas"].append(t["nome"])
                for ms in t["medidas"]:
                    f["medidas"][f"{t['nome']}[{ms['nome']}]"] = {
                        "expressao": normaliza(ms["expressao"]), "formato": ms.get("formato"),
                        "pasta": ms.get("pasta"), "descricao": ms.get("descricao")}
                for c in t["colunas"]:
                    chave = f"{t['nome']}[{c['nome']}]"
                    if c["calculada"]:
                        f["calculadas"][chave] = {"expressao": normaliza(c["expressao"]), "formato": c.get("formato")}
                    else:
                        f["colunas"][chave] = {"tipo": c.get("tipo_dado")}
                if t.get("m"):
                    f["consultas"][t["nome"]] = t["m"].replace("\r\n", "\n").strip()
                elif t.get("dax"):
                    f["consultas"][t["nome"]] = "(tabela calculada) " + normaliza(t["dax"])
            for r in mod["relacoes"]:
                if AUTOMATICAS.match(r["para"].split("[")[0]):
                    continue
                f["relacoes"][f"{r['de']} → {r['para']}"] = {
                    "ativa": r["ativa"], "direcao": r["direcao"], "cardinalidade": r["cardinalidade"]}
    try:
        rel = relatorio(alvo)
    except Erro as e:
        rel = None
        f["avisos"].append(f"relatório não lido: {e}")
    if rel:
        for p in rel["paginas"]:
            f["paginas"][p["nome"]] = {"nome": p["exibicao"], "oculta": bool(p.get("oculta"))}
            for v in p["visuais"]:
                campos = {papel: [c["campo"] + (f" ({c['agregacao']})" if c.get("agregacao") else "") for c in lista]
                          for papel, lista in v.get("campos", {}).items()}
                f["visuais"][v["nome"]] = {"pagina": p["exibicao"], "tipo": v["tipo"], "titulo": v.get("titulo"),
                                           "campos": campos,
                                           "posicao": [round(v.get(k) or 0) for k in ("x", "y", "largura", "altura")]}
        for fil in rel.get("filtros", []):
            for c in fil["campos"]:
                f["filtros"].append(f"{fil['onde']}: {c['campo']}")
    return f


def compara_dicts(antes: dict, depois: dict) -> dict:
    return {"novos": [{"objeto": k, "valor": depois[k]} for k in sorted(depois) if k not in antes],
            "removidos": [{"objeto": k, "valor": antes[k]} for k in sorted(antes) if k not in depois],
            "alterados": [{"objeto": k, "antes": antes[k], "depois": depois[k]}
                          for k in sorted(antes) if k in depois and antes[k] != depois[k]]}


def comparar_fotos(a: dict, b: dict) -> dict:
    d = {chave: compara_dicts(a[chave], b[chave])
         for chave in ("medidas", "calculadas", "colunas", "relacoes", "consultas", "paginas", "visuais")}
    d["filtros"] = {"novos": [{"objeto": x, "valor": None} for x in sorted(set(b["filtros"]) - set(a["filtros"]))],
                    "removidos": [{"objeto": x, "valor": None} for x in sorted(set(a["filtros"]) - set(b["filtros"]))],
                    "alterados": []}
    for c in d["consultas"]["alterados"]:  # o M sai como diff de linhas
        c["diff"] = list(difflib.unified_diff(c["antes"].split("\n"), c["depois"].split("\n"), "antes", "depois",
                                              lineterm="", n=1))[2:]
    d["avisos"] = a["avisos"] + b["avisos"]
    d["igual"] = not any(v["novos"] or v["removidos"] or v["alterados"] for k, v in d.items() if isinstance(v, dict))
    return d


def versao_git(caminho: Path, rev: str, destino: Path) -> Path:
    """Escreve no `destino` (pasta temporária) a versão do arquivo (ou da pasta do PBIP) na revisão `rev`."""
    pasta = caminho.parent if caminho.is_file() else caminho
    r = subprocess.run(["git", "-C", str(pasta), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode:
        raise Erro("O arquivo não está num repositório Git. Para comparar, passe dois arquivos.")
    raiz = Path(r.stdout.strip())

    def mostra(rel: str) -> bytes:
        s = subprocess.run(["git", "-C", str(raiz), "show", f"{rev}:{rel}"], capture_output=True)
        if s.returncode:
            raise Erro(f"Não achei {rel} na versão {rev} do Git.")
        return s.stdout

    if caminho.is_file() and caminho.suffix.lower() in (".pbix", ".pbit"):
        rel = caminho.resolve().relative_to(raiz.resolve()).as_posix()
        saida = destino / caminho.name
        saida.write_bytes(mostra(rel))
        return saida
    # PBIP: traz a pasta inteira onde está o .pbip (ou a pasta pedida)
    base = (caminho.parent if caminho.is_file() else caminho).resolve().relative_to(raiz.resolve()).as_posix()
    ls = subprocess.run(["git", "-C", str(raiz), "ls-tree", "-r", "--name-only", rev, "--", base or "."],
                        capture_output=True, text=True, encoding="utf-8")
    nomes = [n for n in ls.stdout.splitlines() if n]
    if not nomes:
        raise Erro(f"Não achei {base or 'a pasta'} na versão {rev} do Git.")
    for n in nomes:
        alvo = destino / n
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(mostra(n))
    return destino / caminho.resolve().relative_to(raiz.resolve())


def comparar(arquivo: str, outro: str | None, rev: str | None) -> dict:
    temporaria = Path(tempfile.mkdtemp(prefix="pbi-x-comparar-"))
    try:
        if outro:
            antes, depois = arquivo, outro
            rotulo = (arquivo, outro)
        else:
            antes = str(versao_git(Path(arquivo), rev or "HEAD", temporaria))
            depois = arquivo
            rotulo = (f"{arquivo} (Git: {rev or 'HEAD'})", f"{arquivo} (atual)")
        d = comparar_fotos(foto(antes), foto(depois))
        d["antes"], d["depois"] = rotulo
        return d
    finally:
        shutil.rmtree(temporaria, ignore_errors=True)


def descreve_item(chave: str, objeto: str, valor) -> str:
    """Uma linha legível para um item novo ou removido."""
    if chave == "paginas":
        return f"página \"{valor['nome']}\" [{objeto}]"
    if chave == "visuais":
        campos = "; ".join(f"{papel} = {', '.join(lista)}" for papel, lista in valor["campos"].items())
        titulo = f" \"{valor['titulo']}\"" if valor.get("titulo") else ""
        return f"{valor['tipo']}{titulo} na página \"{valor['pagina']}\": {campos or 'sem campos'} [{objeto}]"
    if chave in ("medidas", "calculadas"):
        fmt = f"   [formato: {valor['formato']}]" if valor.get("formato") else ""
        return f"{objeto} = {valor['expressao']}{fmt}"
    if chave == "relacoes":
        return f"{objeto} ({valor['cardinalidade']}, {'ativa' if valor['ativa'] else 'inativa'}, {valor['direcao']})"
    if chave == "colunas":
        return f"{objeto} ({valor['tipo']})"
    return objeto


def comparar_texto(d: dict) -> str:
    linhas = [f"Comparação\n  antes:  {d['antes']}\n  depois: {d['depois']}"]
    for aviso in d.get("avisos", []):
        linhas.append(f"Aviso: {aviso}")
    if d["igual"]:
        linhas.append("\nNenhuma diferença no modelo nem no relatório.")
        return "\n".join(linhas)
    titulos = [("medidas", "Medidas"), ("calculadas", "Colunas calculadas"), ("colunas", "Colunas"),
               ("relacoes", "Relações"), ("consultas", "Consultas (M)"), ("paginas", "Páginas"),
               ("visuais", "Visuais"), ("filtros", "Filtros")]
    for chave, titulo in titulos:
        c = d[chave]
        if not (c["novos"] or c["removidos"] or c["alterados"]):
            continue
        linhas.append(f"\n## {titulo}: {len(c['novos'])} novos, {len(c['alterados'])} alterados, "
                      f"{len(c['removidos'])} removidos")
        for sinal, lista in (("+", c["novos"]), ("-", c["removidos"])):
            for n in lista:
                linhas.append(f"{sinal} {descreve_item(chave, n['objeto'], n['valor'])}")
        for alt in c["alterados"]:
            antes, depois = alt["antes"], alt["depois"]
            if chave == "visuais":
                linhas.append(f"~ {depois['tipo']} na página \"{depois['pagina']}\" [{alt['objeto']}]")
                for papel in sorted(set(antes["campos"]) | set(depois["campos"])):
                    a, b = antes["campos"].get(papel, []), depois["campos"].get(papel, [])
                    saiu, entrou = [x for x in a if x not in b], [x for x in b if x not in a]
                    if saiu or entrou:
                        linhas.append(f"    {papel}: " + "; ".join([f"sai {x}" for x in saiu] + [f"entra {x}" for x in entrou]))
                    elif a != b:
                        linhas.append(f"    {papel}: mudou a ordem dos campos")
                if antes["tipo"] != depois["tipo"]:
                    linhas.append(f"    tipo: {antes['tipo']} → {depois['tipo']}")
                if antes.get("titulo") != depois.get("titulo"):
                    linhas.append(f"    título: {antes.get('titulo')!r} → {depois.get('titulo')!r}")
                if antes["posicao"] != depois["posicao"]:
                    linhas.append("    posição (x, y, largura, altura): {} → {}".format(antes["posicao"], depois["posicao"]))
                if antes["pagina"] != depois["pagina"]:
                    linhas.append(f"    página: {antes['pagina']} → {depois['pagina']}")
                continue
            linhas.append(f"~ {alt['objeto']}")
            if chave == "consultas":
                linhas += [f"    {l}" for l in alt["diff"]]
                continue
            for campo in depois:
                if antes.get(campo) != depois.get(campo):
                    linhas.append(f"    {campo}: {antes.get(campo)!r} → {depois.get(campo)!r}")
    return "\n".join(linhas)


# ---------------------------------------------------------------- fluxo guiado de visuais

PASTA_FLUXO = Path(tempfile.gettempdir()) / "pbi-x"
CHAVES_TEXTO = ("config", "filters", "query", "dataTransforms")  # no Layout, estes valores são JSON em texto


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as arq:
        for bloco in iter(lambda: arq.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def titulos_desktop() -> list[str]:
    """Títulos das janelas do Power BI Desktop abertas (os dois Desktops usam PBIDesktop.exe)."""
    if os.name != "nt":
        return []
    try:
        r = subprocess.run(["tasklist", "/v", "/fo", "csv", "/nh", "/fi", "imagename eq PBIDesktop.exe"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    saida = r.stdout.decode("oem", errors="replace")
    return [linha[-1] for linha in csv.reader(io.StringIO(saida)) if len(linha) > 2 and linha[0].lower() == "pbidesktop.exe"]


def aberto_no_desktop(arquivo: Path) -> str | None:
    """O Desktop do RS mostra "<nome> - Power BI Desktop (<mês> de <ano>)"; o normal, só "<nome>"."""
    nome = arquivo.stem.lower()
    for t in titulos_desktop():
        limpo = t.strip().rstrip("*").strip().lower()
        if limpo == nome or limpo.startswith(f"{nome} - "):
            return t
    return None


def checar(caminho: str) -> dict:
    alvo = abrir(caminho)
    arquivo = alvo["caminho"].resolve()
    d = info(alvo)
    problemas, avisos = [], []
    if alvo["tipo"] not in ("pbix", "pbit"):
        problemas.append("O fluxo de visuais é para PBIX e PBIT do Report Server. Num PBIP, o relatório é texto "
                         "e a edição de PBIP está fora da primeira versão do plugin.")
    elif d["origem"] != "Desktop do Report Server" or "Report/Layout" not in alvo["zip"].namelist():
        problemas.append("Este arquivo é do Desktop normal (relatório em PBIR dentro do zip). Na primeira versão, "
                         "o fluxo de visuais é só para arquivos do Desktop do Report Server.")
    janela = aberto_no_desktop(arquivo)
    if janela:
        problemas.append(f"O Desktop está com o arquivo aberto (\"{janela}\"). Salve e feche antes de começar.")
    if not d["git"]["situacao"].startswith("versionado"):
        avisos.append(f"Git: {d['git']['situacao']}. Na troca, o original é substituído de vez e não há backup: "
                      "sem histórico no Git, não dá para voltar à versão anterior.")
    elif "não commitadas" in d["git"]["situacao"]:
        avisos.append("O arquivo tem mudanças não commitadas. Faça o commit antes, para poder voltar a esta versão.")
    return {"arquivo": str(arquivo), "tipo": alvo["tipo"], "ok": not problemas, "problemas": problemas,
            "avisos": avisos, "git": d["git"]}


def abre_textos(layout: dict) -> dict:
    """Abre em objetos os textos JSON do Layout (no relatório, no nível da página e no do visual)."""
    def abre(no: dict):
        for chave in CHAVES_TEXTO:
            if isinstance(no.get(chave), str) and no[chave][:1] in "[{":
                no[chave] = json.loads(no[chave])
    abre(layout)
    for s in layout.get("sections", []):
        abre(s)
        for vc in s.get("visualContainers", []):
            abre(vc)
    return layout


def fecha_textos(layout: dict) -> dict:
    """Volta os objetos a texto JSON compacto, como o Desktop grava."""
    def fecha(no: dict):
        for chave in CHAVES_TEXTO:
            if isinstance(no.get(chave), (dict, list)):
                no[chave] = json.dumps(no[chave], ensure_ascii=False, separators=(",", ":"))
    fecha(layout)
    for s in layout.get("sections", []):
        fecha(s)
        for vc in s.get("visualContainers", []):
            fecha(vc)
    return layout


def limpar_sobras(dias: float = 1.0, tudo: bool = False) -> list[str]:
    apagadas = []
    if PASTA_FLUXO.is_dir():
        limite = time.time() - dias * 86400
        for p in PASTA_FLUXO.iterdir():
            if p.is_dir() and (tudo or p.stat().st_mtime < limite):
                shutil.rmtree(p, ignore_errors=True)
                apagadas.append(str(p))
    return apagadas


def pasta_de_trabalho(pasta: str) -> tuple[Path, dict]:
    p = Path(pasta).resolve()
    try:
        p.relative_to(PASTA_FLUXO.resolve())
    except ValueError:
        raise Erro(f"Isto não é uma pasta de trabalho do plugin (elas ficam em {PASTA_FLUXO}).")
    if not (p / "estado.json").is_file():
        raise Erro(f"Não achei o estado.json em {p}. Rode extrair de novo.")
    return p, json.loads((p / "estado.json").read_text(encoding="utf-8"))


def extrair(caminho: str) -> dict:
    c = checar(caminho)
    if not c["ok"]:
        raise Erro(" ".join(c["problemas"]))
    limpar_sobras()
    original = Path(c["arquivo"])
    pasta = PASTA_FLUXO / f"{original.stem}-{datetime.now():%Y%m%d-%H%M%S}"
    pasta.mkdir(parents=True)
    with zipfile.ZipFile(original) as z:
        layout = abre_textos(json.loads(texto(z.read("Report/Layout"))))
    (pasta / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    estado = {"original": str(original), "sha256": sha256(original), "tipo": c["tipo"],
              "extraido": datetime.now().isoformat(timespec="seconds"), "candidato": None}
    (pasta / "estado.json").write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pasta": str(pasta), "layout": str(pasta / "layout.json"), "avisos": c["avisos"], "git": c["git"]}


def campos_do_modelo(original: Path) -> set | None:
    try:
        mod = modelo(abrir(str(original)))
    except Erro:
        return None
    nomes = set()
    for t in mod["tabelas"]:
        nomes |= {f"{t['nome']}[{c['nome']}]" for c in t["colunas"]}
        nomes |= {f"{t['nome']}[{m['nome']}]" for m in t["medidas"]}
    return nomes


def validar_layout(layout: dict, existentes: set | None) -> tuple[list[str], list[str]]:
    erros, ajustes = [], []
    secoes = layout.get("sections")
    if not isinstance(secoes, list) or not secoes:
        return ["O layout precisa de pelo menos uma página em \"sections\"."], ajustes
    nomes_pag, nomes_vis = set(), set()
    for s in secoes:
        pag = s.get("displayName", "?")
        for chave in ("name", "displayName", "visualContainers"):
            if chave not in s:
                erros.append(f"Página \"{pag}\": falta \"{chave}\".")
        if s.get("name") in nomes_pag:
            erros.append(f"Página \"{pag}\": o name {s.get('name')} se repete.")
        nomes_pag.add(s.get("name"))
        for vc in s.get("visualContainers", []):
            cfg = vc.get("config")
            if not isinstance(cfg, dict):
                erros.append(f"Página \"{pag}\": um visual está sem config.")
                continue
            nome = cfg.get("name")
            if not nome or nome in nomes_vis:
                erros.append(f"Página \"{pag}\": visual sem name ou com name repetido ({nome}).")
            nomes_vis.add(nome)
            for k in ("x", "y", "width", "height"):
                if not isinstance(vc.get(k), (int, float)):
                    erros.append(f"Visual {nome}: \"{k}\" precisa ser número.")
            # a posição do container e a de layouts[0] precisam bater: o container manda
            pos = {k: vc.get(k) for k in ("x", "y", "z", "width", "height") if k in vc}
            lay = cfg.setdefault("layouts", [{"id": 0, "position": {}}])
            if lay and any(lay[0].setdefault("position", {}).get(k) != v for k, v in pos.items()):
                lay[0]["position"].update(pos)
                ajustes.append(f"Visual {nome}: alinhei a posição de layouts[0] com a do container.")
            for cache in ("query", "dataTransforms"):  # cache de consulta: o Desktop refaz ao abrir
                if cache in vc:
                    del vc[cache]
            sv = cfg.get("singleVisual")
            if sv is None:
                if "singleVisualGroup" not in cfg:
                    erros.append(f"Visual {nome}: falta singleVisual (ou singleVisualGroup, para grupo).")
                continue
            if not sv.get("visualType"):
                erros.append(f"Visual {nome}: falta visualType.")
            pq = sv.get("prototypeQuery", {})
            apelidos = {f.get("Name"): f.get("Entity") for f in pq.get("From", [])}
            selecionados = set()
            for item in pq.get("Select", []):
                selecionados.add(item.get("Name"))
                for no in (item.get("Column"), item.get("Measure"),
                           (item.get("Aggregation") or {}).get("Expression", {}).get("Column")):
                    if isinstance(no, dict):
                        fonte = no.get("Expression", {}).get("SourceRef", {}).get("Source")
                        if fonte not in apelidos:
                            erros.append(f"Visual {nome}: o apelido \"{fonte}\" não está no From.")
                campo = descrever_campo(item, apelidos)
                if existentes is not None and campo["tipo"] != "outro" and campo["campo"] not in existentes \
                        and "nivel" not in campo:
                    erros.append(f"Visual {nome}: o campo {campo['campo']} não existe no modelo.")
            for papel, projs in sv.get("projections", {}).items():
                for pr in projs:
                    if pr.get("queryRef") not in selecionados:
                        erros.append(f"Visual {nome}: o queryRef \"{pr.get('queryRef')}\" do papel {papel} não está "
                                     "no Select do prototypeQuery.")
    return erros, ajustes


def grava_copia(original: Path, destino: Path, layout_bytes: bytes) -> None:
    """Copia o zip entrada por entrada, troca o Layout e tira o SecurityBindings."""
    override = '<Override PartName="/SecurityBindings" ContentType="" />'
    with zipfile.ZipFile(original) as zin, zipfile.ZipFile(destino, "w") as zout:
        for info_zip in zin.infolist():
            if info_zip.filename == "SecurityBindings":
                continue
            dados = layout_bytes if info_zip.filename == "Report/Layout" else zin.read(info_zip.filename)
            if info_zip.filename == "[Content_Types].xml":
                dados = dados.decode("utf-8").replace(override, "").encode("utf-8")
            novo = zipfile.ZipInfo(info_zip.filename, date_time=info_zip.date_time)
            novo.compress_type = info_zip.compress_type
            novo.create_system = info_zip.create_system
            novo.external_attr = info_zip.external_attr
            zout.writestr(novo, dados)


def montar(pasta_txt: str) -> dict:
    pasta, estado = pasta_de_trabalho(pasta_txt)
    original = Path(estado["original"])
    if sha256(original) != estado["sha256"]:
        raise Erro("O original mudou desde a extração. Rode limpar e extrair de novo.")
    try:
        layout = json.loads((pasta / "layout.json").read_text(encoding="utf-8"))
    except ValueError as e:
        raise Erro(f"O layout.json não é um JSON válido: {e}")
    erros, ajustes = validar_layout(layout, campos_do_modelo(original))
    if erros:
        return {"ok": False, "erros": erros, "ajustes": ajustes}
    (pasta / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    bruto = json.dumps(fecha_textos(copy.deepcopy(layout)), ensure_ascii=False, separators=(",", ":"))
    candidato = pasta / f"{original.stem} (editado){original.suffix}"
    if candidato.exists():
        candidato.unlink()
    grava_copia(original, candidato, bruto.encode("utf-16-le"))
    estado.update(candidato=str(candidato), montado=datetime.now().isoformat(timespec="seconds"))
    (pasta / "estado.json").write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    diferencas = comparar_fotos(foto(str(original), com_modelo=False), foto(str(candidato), com_modelo=False))
    diferencas["antes"], diferencas["depois"] = str(original), str(candidato)
    return {"ok": True, "candidato": str(candidato), "ajustes": ajustes, "diferencas": diferencas}


def abrir_candidato(pasta_txt: str) -> dict:
    pasta, estado = pasta_de_trabalho(pasta_txt)
    if not estado.get("candidato") or not Path(estado["candidato"]).is_file():
        raise Erro("Ainda não há cópia montada. Rode montar antes.")
    candidato = Path(estado["candidato"])
    if os.name == "nt":
        os.startfile(str(candidato))  # abre no Desktop padrão do .pbix/.pbit
        aberto = True
    else:
        aberto = False
    if estado["tipo"] == "pbit":
        como_salvar = (f"Confira os visuais e use Arquivo → Exportar → Modelo do Power BI, salvando por cima de "
                       f"\"{candidato}\". Depois feche o Desktop sem salvar o PBIX.")
    else:
        como_salvar = "Confira os visuais, salve com Ctrl+S e feche o Desktop."
    return {"candidato": str(candidato), "aberto": aberto, "como_salvar": como_salvar,
            "lembrete": "Se a máquina tiver os dois Desktops, a cópia precisa abrir no do Report Server."}


def trocar(pasta_txt: str, confirmo: bool) -> dict:
    if not confirmo:
        raise Erro("A troca substitui o original de vez. Rode com --confirmo só depois do ok da pessoa.")
    pasta, estado = pasta_de_trabalho(pasta_txt)
    original, candidato = Path(estado["original"]), Path(estado.get("candidato") or "")
    if not candidato.is_file():
        raise Erro("Não há cópia montada nesta pasta de trabalho.")
    janela = aberto_no_desktop(candidato) or aberto_no_desktop(original)
    if janela:
        raise Erro(f"Feche o Desktop antes da troca (está aberto: \"{janela}\").")
    with zipfile.ZipFile(candidato) as z:
        nomes = z.namelist()
        meta = json.loads(texto(z.read("Metadata"))) if "Metadata" in nomes else {}
    if "SecurityBindings" not in nomes:
        passo = ("exporte o PBIT (Arquivo → Exportar → Modelo do Power BI) por cima da cópia"
                 if estado["tipo"] == "pbit" else "salve com Ctrl+S")
        raise Erro(f"A cópia ainda não foi salva pelo Desktop (falta o SecurityBindings). Abra a cópia, confira, "
                   f"{passo} e feche o Desktop.")
    if meta.get("CreatedFrom") != "OnPrem":
        raise Erro("A cópia foi salva pelo Desktop normal e o Desktop do Report Server não abre mais esse arquivo. "
                   "Rode montar de novo e abra a cópia pelo Desktop do Report Server.")
    if sha256(original) != estado["sha256"]:
        raise Erro("O original mudou desde a extração. Nada foi trocado; comece de novo (limpar e extrair).")
    temporario = original.with_name(f".{original.stem}.pbi-x-troca{original.suffix}")
    shutil.copy2(candidato, temporario)
    os.replace(temporario, original)
    shutil.rmtree(pasta, ignore_errors=True)
    return {"original": str(original), "sha256": sha256(original), "git": situacao_git(original)}


# ---------------------------------------------------------------- linha de comando

PBIXRAY_VERSAO = "0.15.5"  # a mesma do cabeçalho do script; o formato das medidas depende dela


def ambiente() -> int:
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    try:
        from importlib.metadata import version
        import pbixray  # noqa: F401
        v = version("pbixray")
    except Exception:
        print("PBIXRay: não instalado neste Python. Rode a skill configurar do plugin pbi.")
        return 1
    aviso = "" if v == PBIXRAY_VERSAO else f" (o plugin foi testado com a {PBIXRAY_VERSAO})"
    print(f"PBIXRay: {v}{aviso}")
    print("Tudo pronto para ler PBIX sem abrir o Desktop.")
    return 0


def principal(argv: list[str]) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="pbi.py", description="Lê PBIX, PBIT e PBIP para o plugin pbi.")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("ambiente", help="confere o Python e o PBIXRay")
    for nome, ajuda in (("info", "tipo do arquivo e de qual Desktop veio"),
                        ("relatorio", "páginas, visuais, campos e filtros"),
                        ("modelo", "tabelas, medidas, relações, M e fontes"),
                        ("linhagem", "de cada visual até a fonte, e o que não é usado"),
                        ("revisar", "regras de boas práticas no modelo, no DAX, nas relações e no M"),
                        ("checar", "confere se o arquivo pode entrar no fluxo de visuais"),
                        ("extrair", "cria a pasta de trabalho com o layout.json editável")):
        sp = sub.add_parser(nome, help=ajuda)
        sp.add_argument("arquivo")
        sp.add_argument("--json", action="store_true", help="saída em JSON")
        if nome in ("relatorio", "modelo"):
            sp.add_argument("--tudo", action="store_true", help="inclui as tabelas automáticas de data/hora")
    sp = sub.add_parser("comparar", help="o que mudou entre duas versões")
    sp.add_argument("arquivo", help="o arquivo (ou o de antes, se houver um segundo)")
    sp.add_argument("outro", nargs="?", help="o arquivo de depois; sem ele, compara com o Git")
    sp.add_argument("--rev", default=None, help="versão do Git para comparar (padrão: HEAD)")
    sp.add_argument("--json", action="store_true")
    for nome, ajuda in (("montar", "valida o layout.json e gera a cópia"),
                        ("abrir", "abre a cópia no Desktop"),
                        ("trocar", "põe a cópia salva no lugar do original")):
        sp = sub.add_parser(nome, help=ajuda)
        sp.add_argument("pasta")
        sp.add_argument("--json", action="store_true")
        if nome == "trocar":
            sp.add_argument("--confirmo", action="store_true", help="a pessoa deu o ok para substituir o original")
    sp = sub.add_parser("limpar", help="apaga a pasta de trabalho ou as sobras")
    sp.add_argument("pasta", nargs="?")
    sp.add_argument("--tudo", action="store_true", help="apaga todas as pastas de trabalho do plugin")
    a = p.parse_args(argv)
    if a.comando == "ambiente":
        return ambiente()

    def mostra(d, texto_fn):
        print(json.dumps(d, ensure_ascii=False, indent=2, default=str) if getattr(a, "json", False) else texto_fn(d))

    try:
        if a.comando == "comparar":
            mostra(comparar(a.arquivo, a.outro, a.rev), comparar_texto)
            return 0
        if a.comando == "checar":
            d = checar(a.arquivo)
            mostra(d, lambda d: ("Pode seguir." if d["ok"] else "Não dá para seguir:") + "".join(
                f"\n- {x}" for x in d["problemas"] + d["avisos"]))
            return 0 if d["ok"] else 1
        if a.comando == "extrair":
            mostra(extrair(a.arquivo), lambda d: f"Pasta de trabalho: {d['pasta']}\nEdite: {d['layout']}"
                   + "".join(f"\nAviso: {x}" for x in d["avisos"]))
            return 0
        if a.comando == "montar":
            d = montar(a.pasta)
            if not d["ok"]:
                mostra(d, lambda d: "O layout.json tem problemas; nada foi gerado:" + "".join(
                    f"\n- {x}" for x in d["erros"]))
                return 1
            mostra(d, lambda d: f"Cópia gerada: {d['candidato']}" + "".join(f"\n({x})" for x in d["ajustes"])
                   + "\n\nO que muda no relatório:\n" + comparar_texto(d["diferencas"]).split("\n", 3)[-1])
            return 0
        if a.comando == "abrir":
            mostra(abrir_candidato(a.pasta), lambda d: (f"Abri no Desktop: {d['candidato']}" if d["aberto"] else
                                                        f"Abra no Desktop: {d['candidato']}")
                   + f"\n{d['como_salvar']}\n{d['lembrete']}")
            return 0
        if a.comando == "trocar":
            mostra(trocar(a.pasta, a.confirmo), lambda d: f"Pronto: a cópia tomou o lugar de {d['original']}.\n"
                   f"Git: {d['git']['situacao']}. Para ver o que mudou, use o subcomando comparar.")
            return 0
        if a.comando == "limpar":
            if a.pasta:
                pasta, _ = pasta_de_trabalho(a.pasta)
                shutil.rmtree(pasta, ignore_errors=True)
                print(f"Apaguei {pasta}.")
            else:
                apagadas = limpar_sobras(tudo=a.tudo)
                print(f"Apaguei {len(apagadas)} pasta(s) de trabalho." if apagadas else "Nada para apagar.")
            return 0
        alvo = abrir(a.arquivo)
        if a.comando == "info":
            d = info(alvo)
            print(json.dumps(d, ensure_ascii=False, indent=2) if a.json else info_texto(d))
        elif a.comando == "relatorio":
            d = relatorio(alvo)
            print(json.dumps(d, ensure_ascii=False, indent=2) if a.json else relatorio_texto(d, a.arquivo))
        elif a.comando == "modelo":
            d = modelo(alvo)
            print(json.dumps(d, ensure_ascii=False, indent=2, default=str) if a.json
                  else modelo_texto(d, a.arquivo, a.tudo))
        elif a.comando == "linhagem":
            d = linhagem(alvo)
            print(json.dumps(d, ensure_ascii=False, indent=2, default=str) if a.json
                  else linhagem_texto(d, a.arquivo))
        else:
            d = revisar(alvo)
            print(json.dumps(d, ensure_ascii=False, indent=2, default=str) if a.json
                  else revisar_texto(d, a.arquivo))
    except Erro as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv[1:]))
