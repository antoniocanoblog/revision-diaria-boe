#!/usr/bin/env python3
"""Recupera fuentes oficiales del BOE sin servicios de IA ni credenciales externas."""
import argparse, concurrent.futures, datetime as dt, json, pathlib, re, time, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = pathlib.Path("fuentes-boe")
HOSTS = ("www.boe.es", "boe.es")
MEL = re.compile(r"\bmelilla\b|\bmelillense[s]?\b|\brostrogordo\b|\bchafarinas\b", re.I)
MPT = re.compile(r"pol[ií]tica territorial|memoria democr[aá]tica|delegaci[oó]n del gobierno|subdelegaci[oó]n del gobierno|direcci[oó]n insular", re.I)
AREAS = re.compile(r"protecci[oó]n civil|emergencia|seguridad nacional|seguridad ciudadana|extranjer[ií]a|migraci[oó]n|frontera|sanidad exterior|empleo|personal|funci[oó]n p[uú]blica|transport|costas|infraestructur|seguridad social|educaci[oó]n|subvenci[oó]n|contrataci[oó]n|administraci[oó]n general del estado|ingesa|justicia|hacienda|interior", re.I)

def fetch(url, accept):
    u=urllib.parse.urlsplit(url)
    if u.scheme!="https" or u.hostname not in HOSTS: raise ValueError("URL no oficial")
    errors=[]
    for attempt in range(4):
        host=HOSTS[attempt%2]
        target=urllib.parse.urlunsplit((u.scheme,host,u.path,u.query,""))
        try:
            req=urllib.request.Request(target,headers={"Accept":accept,"User-Agent":"Seguimiento-BOE/1.0 (consulta publica)","Cache-Control":"no-cache"})
            with urllib.request.urlopen(req,timeout=25) as r:
                if urllib.parse.urlsplit(r.geturl()).hostname not in HOSTS: raise ValueError("Redireccion no oficial")
                return r.read(40_000_001)
        except Exception as e:
            errors.append(f"{host}: {e}")
            if attempt<3: time.sleep(2**attempt)
    raise RuntimeError("; ".join(errors))

def text(el):
    return " ".join(" ".join(el.itertext()).split()) if el is not None else ""

def parse_summary(raw, date):
    root=ET.fromstring(raw)
    if root.findtext("./status/code") not in ("200",None): raise ValueError("Estado API no satisfactorio")
    s=root.find("./data/sumario")
    if s is None: raise ValueError("No hay nodo sumario")
    if s.findtext("./metadatos/fecha_publicacion")!=date.replace("-",""): raise ValueError("Fecha del sumario no coincide")
    if s.findtext("./metadatos/publicacion")!="BOE": raise ValueError("Publicacion distinta del BOE")
    issues=[]; rows={}
    def walk(node, ctx):
        ctx=dict(ctx)
        if node.tag in ("seccion","departamento","epigrafe"):
            ctx[node.tag]=node.get("nombre","")
            if node.tag=="seccion": ctx["codigo_seccion"]=node.get("codigo","")
        if node.tag=="item":
            ident=node.findtext("identificador","")
            if not re.fullmatch(r"BOE-[A-Z]+-\d{4}-\d+",ident): return
            if ident in rows: return
            rows[ident]={**ctx,"id":ident,"titulo":node.findtext("titulo",""),"url_html":node.findtext("url_html",""),"url_xml":node.findtext("url_xml",""),"url_pdf":node.findtext("url_pdf","")}
            return
        for child in node: walk(child,ctx)
    for d in s.findall("diario"):
        issues.append({"numero":d.get("numero"),"sumario_pdf":d.findtext("./sumario_diario/url_pdf","")})
        walk(d,{"numero":d.get("numero")})
    if not issues or not rows: raise ValueError("Sumario vacio o estructura inesperada")
    return issues,list(rows.values())

def inspect(row,folder,date):
    r=dict(row)
    title=r.get("titulo","")
    scope=" ".join([title,r.get("departamento",""),r.get("epigrafe","")])
    r["melilla_titulo"]=bool(MEL.search(scope))
    r["politica_territorial_candidato"]=bool(MPT.search(scope))
    r["otros_candidato"]=bool(AREAS.search(scope)) or r.get("codigo_seccion")=="1"
    r["texto_verificado"]=False
    try:
        url=r["url_xml"] or "https://www.boe.es/diario_boe/xml.php?id="+r["id"]
        raw=fetch(url,"application/xml,text/xml")
        root=ET.fromstring(raw)
        body=root.find(".//texto")
        if body is None: raise ValueError("XML sin cuerpo de texto")
        published=root.findtext(".//fecha_publicacion")
        if published and published.replace("-","")!=date.replace("-",""): raise ValueError("Fecha del documento distinta")
        ident=root.findtext(".//identificador")
        if ident and ident!=r["id"]: raise ValueError("Identificador del documento distinto")
        bodytext=text(body)
        if not bodytext: raise ValueError("Cuerpo vacio")
        r["texto_verificado"]=True
        r["melilla_texto"]=bool(MEL.search(bodytext))
        r["politica_territorial_candidato"] |= bool(MPT.search(text(root.find(".//metadatos"))))
        # No reproducir indiscriminadamente textos ajenos al objeto de seguimiento.
        if r["melilla_titulo"] or r["melilla_texto"] or r["politica_territorial_candidato"] or r["otros_candidato"]:
            p=folder/"textos"/(r["id"]+".txt")
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text("\n".join([r["id"],title,r["url_html"],bodytext]),encoding="utf-8")
            r["archivo_texto"]=str(p)
    except Exception as e:
        r["error_texto"]=str(e)[:500]
    return r

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fecha",default=dt.datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat())
    args=ap.parse_args()
    date=dt.date.fromisoformat(args.fecha).isoformat()
    folder=ROOT/date
    folder.mkdir(parents=True,exist_ok=True)
    api="https://www.boe.es/datosabiertos/api/boe/sumario/"+date.replace("-","")
    html="https://www.boe.es/boe/dias/"+date.replace("-","/")+"/"
    meta={"fecha":date,"fuente_api":api,"fuente_html":html,"estado":"error","consulta":dt.datetime.now(ZoneInfo("Europe/Madrid")).isoformat(),"alcance":"Sumario API; suplementos que figuren en ella. TEU y TEJU independientes no cubiertos.","total":None}
    try:
        raw=fetch(api,"application/xml,text/xml")
        issues,rows=parse_summary(raw,date)
        meta.update({"boletines":issues,"total":len(rows),"estado":"sumario_verificado"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            rows=list(pool.map(lambda row:inspect(row,folder,date),rows))
        meta.update({"textos_verificados":sum(r["texto_verificado"] for r in rows),"textos_no_verificados":sum(not r["texto_verificado"] for r in rows),"estado":"textos_comprobados" if all(r["texto_verificado"] for r in rows) else "parcial"})
        # Indice completo dividido en lotes pequenos para su lectura por ChatGPT.
        for p in folder.glob("indice-*.json"): p.unlink()
        files=[]
        for i in range(0,len(rows),30):
            p=folder/f"indice-{i//30+1:03d}.json"
            p.write_text(json.dumps(rows[i:i+30],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            files.append(str(p))
        meta["indices"]=files
        meta["candidatos_melilla"]=[r["id"] for r in rows if r["melilla_titulo"] or r.get("melilla_texto")]
        meta["candidatos_politica_territorial"]=[r["id"] for r in rows if r["politica_territorial_candidato"]]
        meta["candidatos_otros"]=[r["id"] for r in rows if r["otros_candidato"]]
    except Exception as e:
        meta["error"]=str(e)[:1000]
    p=folder/"estado.json"
    # Una incidencia posterior no debe destruir una descarga previa util.
    if p.exists() and meta["estado"]=="error":
        previous=json.loads(p.read_text(encoding="utf-8"))
        if previous.get("total") is not None:
            previous["ultima_incidencia"]=meta
            meta=previous
    p.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(meta,ensure_ascii=False))
if __name__=="__main__": main()
