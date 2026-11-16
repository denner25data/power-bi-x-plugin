#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pbixray==0.15.5"]
# ///
"""pbi.py: lê arquivos do Power BI (PBIX, PBIT e PBIP) para o plugin pbi.

Subcomandos:
  ambiente              confere o Python e o PBIXRay (usado pela skill configurar)
  info <arquivo>        que tipo de arquivo é e de qual Desktop veio
  relatorio <arquivo>   páginas, visuais e os campos de cada visual
  modelo <arquivo>      tabelas, medidas, colunas calculadas, relações, M e fontes
  linhagem <arquivo>    de cada visual até a fonte: medidas, colunas, tabelas, M; e o que não é usado

Opções de relatorio e modelo: --json (saída em JSON) e --tudo (inclui as tabelas
automáticas de data/hora).

Este script só lê. Nunca grava em .pbix ou .pbit.
O modelo de um PBIX é lido pelo PBIXRay; o resto usa só a biblioteca padrão.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
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


def titulo_visual(objetos: dict) -> str | None:
    try:
        valor = objetos["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        return valor.strip("'")
    except (KeyError, IndexError, TypeError):
        return None


def relatorio_layout(fonte: Fonte, nome: str) -> dict:
    layout = fonte.json(nome)
    paginas = []
    for s in sorted(layout.get("sections", []), key=lambda s: s.get("ordinal", 0)):
        cfg_pagina = json.loads(s.get("config") or "{}")
        visuais = []
        for vc in s.get("visualContainers", []):
            cfg = json.loads(vc.get("config") or "{}")
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
    return {"formato": "antigo (Report/Layout)", "paginas": paginas}


def relatorio_pbir(fonte: Fonte, base: str) -> dict:
    nomes = fonte.nomes()
    ordem = fonte.json(f"{base}pages/pages.json").get("pageOrder", [])
    todas = sorted({n.split("/")[len(base.split("/")) ] for n in nomes
                    if n.startswith(f"{base}pages/") and n.endswith("/page.json")})
    paginas = []
    for p in ordem + [x for x in todas if x not in ordem]:
        pj = fonte.json(f"{base}pages/{p}/page.json")
        visuais = []
        prefixo = f"{base}pages/{p}/visuals/"
        for n in sorted(x for x in nomes if x.startswith(prefixo) and x.endswith("/visual.json")):
            vj = fonte.json(n)
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
    return {"formato": "PBIR", "paginas": paginas}


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
            medidas.append({"nome": ms["Name"], "expressao": ms["Expression"],
                            "formato": None if vazio(fmt) else fmt,
                            "pasta": None if vazio(ms.get("DisplayFolder")) else ms["DisplayFolder"],
                            "descricao": None if vazio(ms.get("Description")) else ms["Description"]})
        tabelas.append({"nome": nome, "automatica": bool(AUTOMATICAS.match(nome)),
                        "oculta": bool(t.get("IsHidden")),
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
                          "descricao": desc, "colunas": [], "medidas": [], "m": None, "dax": None}
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
        desc = f" ({t['descricao']})" if t.get("descricao") else ""
        linhas.append(f"\n## {t['nome']}{oculta}{desc}: {len(t['colunas'])} colunas, {len(t['medidas'])} medidas")
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
                        ("relatorio", "páginas, visuais e campos"),
                        ("modelo", "tabelas, medidas, relações, M e fontes"),
                        ("linhagem", "de cada visual até a fonte, e o que não é usado")):
        sp = sub.add_parser(nome, help=ajuda)
        sp.add_argument("arquivo")
        sp.add_argument("--json", action="store_true", help="saída em JSON")
        if nome != "info":
            sp.add_argument("--tudo", action="store_true", help="inclui as tabelas automáticas de data/hora")
    a = p.parse_args(argv)
    if a.comando == "ambiente":
        return ambiente()
    try:
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
        else:
            d = linhagem(alvo)
            print(json.dumps(d, ensure_ascii=False, indent=2, default=str) if a.json
                  else linhagem_texto(d, a.arquivo))
    except Erro as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv[1:]))
