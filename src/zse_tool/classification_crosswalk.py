from __future__ import annotations

import argparse, hashlib, json, sqlite3, tempfile, time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CATALOG_SCHEMA_VERSION = "official-crosswalk-catalog-v0.1"
DB_SCHEMA_VERSION = "official-classification-crosswalk-v0.1"
OFFICIAL_EVIDENCE_CLASS = "O1_OFFICIAL_CROSSWALK"
EMPIRICAL_EVIDENCE_CLASS = "E1_EMPIRICAL_CROSS_SYSTEM_EVIDENCE"
ALLOWED_SOURCE_HOSTS = {"unstats.un.org"}
MAX_SOURCE_BYTES = 16 * 1024 * 1024


class CrosswalkError(RuntimeError): pass


@dataclass(frozen=True)
class CrosswalkSourceSpec:
    source_id: str; provider: str; adapter: str; url: str
    from_system: str; from_version: str; to_system: str; to_version: str
    expected_from_code_count: int; expected_to_code_count: int


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()


def _validate_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme != "https" or (p.hostname or "").lower() not in ALLOWED_SOURCE_HOSTS:
        raise CrosswalkError(f"crosswalk source host is not allowlisted: {p.hostname!r}")
    if p.username or p.password: raise CrosswalkError("source URL must not contain credentials")


def load_catalog(path: Path) -> list[CrosswalkSourceSpec]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != CATALOG_SCHEMA_VERSION: raise CrosswalkError("unsupported catalog schema")
    out = []
    for x in obj.get("sources") or []:
        s = CrosswalkSourceSpec(
            str(x["source_id"]), str(x["provider"]), str(x["adapter"]), str(x["url"]),
            str(x["from_system"]), str(x["from_version"]), str(x["to_system"]), str(x["to_version"]),
            int(x["expected_from_code_count"]), int(x["expected_to_code_count"]),
        )
        _validate_url(s.url)
        if s.adapter != "unsd-isic-rev4-rev5-xlsx": raise CrosswalkError(f"unsupported adapter: {s.adapter}")
        if min(s.expected_from_code_count, s.expected_to_code_count) < 1: raise CrosswalkError("expected counts must be positive")
        out.append(s)
    if not out or len({s.source_id for s in out}) != len(out): raise CrosswalkError("catalog needs unique sources")
    return out


def _download(url: str, timeout: float = 90) -> bytes:
    _validate_url(url); req = Request(url, headers={"User-Agent":"zse-value-scanner/0.4.13 crosswalk"}); err = None
    for n in range(3):
        try:
            with urlopen(req, timeout=timeout) as r:  # nosec B310 - allowlisted URL
                data = r.read(MAX_SOURCE_BYTES + 1)
                if len(data) > MAX_SOURCE_BYTES: raise CrosswalkError("source exceeds byte limit")
                return data
        except Exception as exc:
            err = exc
            if n < 2: time.sleep(0.5 * (n + 1))
    raise CrosswalkError(f"download failed after retries: {err}")


from .classification_crosswalk_unsd import AdapterError, parse_unsd_isic_rev4_rev5_xlsx

def _counts(spec: CrosswalkSourceSpec, edges: list[dict[str,Any]]) -> dict[str,int]:
    a={x["from_code"] for x in edges}; b={x["to_code"] for x in edges}
    if len(a)!=spec.expected_from_code_count: raise CrosswalkError(f"{spec.source_id} expected {spec.expected_from_code_count} source codes, observed {len(a)}")
    if len(b)!=spec.expected_to_code_count: raise CrosswalkError(f"{spec.source_id} expected {spec.expected_to_code_count} target codes, observed {len(b)}")
    return {"edge_count":len(edges),"from_code_count":len(a),"to_code_count":len(b)}


def _conn(path: Path, ro: bool=False) -> sqlite3.Connection:
    c=sqlite3.connect(f"file:{path.expanduser().resolve()}?mode=ro",uri=True) if ro else sqlite3.connect(path.expanduser().resolve())
    c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c


def _init(c: sqlite3.Connection) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS crosswalk_sources(source_id TEXT PRIMARY KEY,provider TEXT,adapter TEXT,source_url TEXT,from_system TEXT,from_version TEXT,to_system TEXT,to_version TEXT,evidence_class TEXT,retrieved_at TEXT,raw_path TEXT,raw_sha256 TEXT,edge_count INT,from_code_count INT,to_code_count INT);
    CREATE TABLE IF NOT EXISTS crosswalk_edges(edge_id INTEGER PRIMARY KEY,source_id TEXT REFERENCES crosswalk_sources(source_id),from_system TEXT,from_version TEXT,from_code TEXT,to_system TEXT,to_version TEXT,to_code TEXT,relation TEXT,official_change_type TEXT,official_note TEXT,evidence_class TEXT);
    CREATE INDEX IF NOT EXISTS x_from ON crosswalk_edges(from_system,from_version,from_code); CREATE INDEX IF NOT EXISTS x_to ON crosswalk_edges(to_system,to_version,to_code);
    CREATE TABLE IF NOT EXISTS empirical_observations(observation_id TEXT PRIMARY KEY,entity_key TEXT,left_system TEXT,left_version TEXT,left_code TEXT,right_system TEXT,right_version TEXT,right_code TEXT,evidence_class TEXT,source_url TEXT,observed_at TEXT,note TEXT);
    """); c.execute("INSERT OR REPLACE INTO metadata VALUES('schema_version',?)",(DB_SCHEMA_VERSION,))


def _insert(c: sqlite3.Connection,s: CrosswalkSourceSpec,raw:Path,digest:str,edges:list[dict[str,Any]],counts:dict[str,int],at:str) -> None:
    c.execute("DELETE FROM crosswalk_edges WHERE source_id=?",(s.source_id,)); c.execute("DELETE FROM crosswalk_sources WHERE source_id=?",(s.source_id,))
    c.execute("INSERT INTO crosswalk_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(s.source_id,s.provider,s.adapter,s.url,s.from_system,s.from_version,s.to_system,s.to_version,OFFICIAL_EVIDENCE_CLASS,at,str(raw),digest,counts['edge_count'],counts['from_code_count'],counts['to_code_count']))
    c.executemany("INSERT INTO crosswalk_edges(source_id,from_system,from_version,from_code,to_system,to_version,to_code,relation,official_change_type,official_note,evidence_class) VALUES(?,?,?,?,?,?,?,?,?,?,?)",[(s.source_id,s.from_system,s.from_version,e['from_code'],s.to_system,s.to_version,e['to_code'],'official_correspondence',e.get('official_change_type'),e.get('official_note'),OFFICIAL_EVIDENCE_CLASS) for e in edges])


# Stable internal seams used by regression tests and future adapters.
_download_bounded = _download
_connect = _conn
_init_db = _init
_validate_edges = _counts

def _write_source(c:sqlite3.Connection,s:CrosswalkSourceSpec,*,retrieved_at:str,raw_path:Path,raw_sha256:str,edges:list[dict[str,Any]],counts:dict[str,int])->None:
    _insert(c,s,raw_path,raw_sha256,edges,counts,retrieved_at)


def sync(catalog:Path,output_db:Path,raw_dir:Path,replace:bool=False)->dict[str,Any]:
    specs=load_catalog(catalog); output_db=output_db.expanduser().resolve(); raw_dir=raw_dir.expanduser().resolve(); raw_dir.mkdir(parents=True,exist_ok=True)
    if output_db.exists() and not replace: raise CrosswalkError(f"output DB exists: {output_db}")
    at=_now(); parsed=[]; report=[]
    for s in specs:
        data=_download_bounded(s.url); digest=_sha(data); d=raw_dir/s.source_id; d.mkdir(parents=True,exist_ok=True); raw=d/f"{digest}.xlsx"
        if not raw.exists(): raw.write_bytes(data)
        try: edges=parse_unsd_isic_rev4_rev5_xlsx(data)
        except AdapterError as exc: raise CrosswalkError(str(exc)) from exc
        counts=_counts(s,edges); parsed.append((s,raw,digest,edges,counts)); report.append({"source_id":s.source_id,"raw_path":str(raw),"raw_sha256":digest,**counts})
    output_db.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output_db.parent,delete=False) as f: tmp=Path(f.name)
    try:
        c=_conn(tmp); _init(c)
        for x in parsed: _insert(c,*x,at)
        c.commit(); ok=c.execute("PRAGMA integrity_check").fetchone()[0]; c.close()
        if ok!="ok": raise CrosswalkError(f"integrity_check={ok}")
        if output_db.exists(): output_db.unlink()
        tmp.replace(output_db)
    finally:
        if tmp.exists(): tmp.unlink()
    manifest={"schema_version":DB_SCHEMA_VERSION,"retrieved_at":at,"output_db":str(output_db),"sources":report}; blob=json.dumps(manifest,sort_keys=True,separators=(",",":")).encode(); m=raw_dir/f"manifest-{_sha(blob)}.json"; m.write_bytes(blob); manifest["manifest_path"]=str(m); return manifest


def status(db:Path)->dict[str,Any]:
    c=_conn(db,True)
    try:return {"database":str(db.expanduser().resolve()),"schema_version":c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0],"source_count":c.execute("SELECT COUNT(*) FROM crosswalk_sources").fetchone()[0],"edge_count":c.execute("SELECT COUNT(*) FROM crosswalk_edges").fetchone()[0],"empirical_observation_count":c.execute("SELECT COUNT(*) FROM empirical_observations").fetchone()[0],"sources":[dict(x) for x in c.execute("SELECT * FROM crosswalk_sources ORDER BY source_id")]}
    finally:c.close()


def _shape(c:sqlite3.Connection,r:sqlite3.Row)->str:
    a=c.execute("SELECT COUNT(DISTINCT to_code) FROM crosswalk_edges WHERE source_id=? AND from_code=?",(r['source_id'],r['from_code'])).fetchone()[0]; b=c.execute("SELECT COUNT(DISTINCT from_code) FROM crosswalk_edges WHERE source_id=? AND to_code=?",(r['source_id'],r['to_code'])).fetchone()[0]
    return "one_to_one" if a==b==1 else "one_to_many" if a>1 and b==1 else "many_to_one" if a==1 and b>1 else "many_to_many"


def show_code(db:Path,system:str,version:str,code:str)->dict[str,Any]:
    c=_conn(db,True)
    try:
        f=list(c.execute("SELECT * FROM crosswalk_edges WHERE from_system=? AND from_version=? AND from_code=? ORDER BY to_code",(system,version,code))); r=list(c.execute("SELECT * FROM crosswalk_edges WHERE to_system=? AND to_version=? AND to_code=? ORDER BY from_code",(system,version,code)))
        return {"node":{"system":system,"version":version,"code":code},"forward":[{**dict(x),"mapping_shape":_shape(c,x)} for x in f],"reverse":[{**dict(x),"mapping_shape":_shape(c,x)} for x in r]}
    finally:c.close()


def translate(db:Path,fs:str,fv:str,fc:str,ts:str,tv:str,max_hops:int=4)->dict[str,Any]:
    c=_conn(db,True); start=(fs,fv,fc); q=deque([(start,[],{start})]); out=[]; shortest=None
    try:
        while q:
            state,path,seen=q.popleft()
            if len(path)>=max_hops or (shortest is not None and len(path)>=shortest): continue
            rows=[(x,(x['to_system'],x['to_version'],x['to_code']),'forward') for x in c.execute("SELECT * FROM crosswalk_edges WHERE from_system=? AND from_version=? AND from_code=?",state)]
            rows += [(x,(x['from_system'],x['from_version'],x['from_code']),'reverse') for x in c.execute("SELECT * FROM crosswalk_edges WHERE to_system=? AND to_version=? AND to_code=?",state)]
            for r,nxt,direction in rows:
                if nxt in seen: continue
                e={**dict(r),"direction":direction,"mapping_shape":_shape(c,r)}; np=path+[e]
                if nxt[0]==ts and nxt[1]==tv:
                    shortest=len(np) if shortest is None else shortest
                    if len(np)==shortest: out.append({"target_code":nxt[2],"hops":len(np),"edges":np})
                else:q.append((nxt,np,seen|{nxt}))
        return {"from":{"system":fs,"version":fv,"code":fc},"to":{"system":ts,"version":tv},"path_count":len(out),"shortest_hops":shortest,"paths":sorted(out,key=lambda x:x['target_code'])}
    finally:c.close()


def add_empirical_observation(db:Path,p:dict[str,Any])->dict[str,Any]:
    for k in ("observation_id","entity_key","left","right","source_url","observed_at"):
        if k not in p: raise CrosswalkError(f"observation missing {k}")
    l,r=p['left'],p['right']; u=urlparse(str(p['source_url']))
    if u.scheme not in {'http','https'} or not u.hostname: raise CrosswalkError("observation source_url must be absolute HTTP(S)")
    c=_conn(db); _init(c); c.execute("INSERT INTO empirical_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(p['observation_id'],p['entity_key'],l['system'],l['version'],l['code'],r['system'],r['version'],r['code'],EMPIRICAL_EVIDENCE_CLASS,p['source_url'],p['observed_at'],p.get('note'))); c.commit(); c.close(); return {**p,"evidence_class":EMPIRICAL_EVIDENCE_CLASS}


def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('sync'); s.add_argument('--catalog',type=Path,required=True); s.add_argument('--output-db',type=Path,required=True); s.add_argument('--raw-dir',type=Path,required=True); s.add_argument('--replace',action='store_true')
    s=sub.add_parser('status'); s.add_argument('--db',type=Path,required=True)
    s=sub.add_parser('show'); s.add_argument('--db',type=Path,required=True); s.add_argument('--system',required=True); s.add_argument('--version',required=True); s.add_argument('--code',required=True)
    s=sub.add_parser('translate'); s.add_argument('--db',type=Path,required=True); s.add_argument('--from-system',required=True); s.add_argument('--from-version',required=True); s.add_argument('--from-code',required=True); s.add_argument('--to-system',required=True); s.add_argument('--to-version',required=True); s.add_argument('--max-hops',type=int,default=4)
    s=sub.add_parser('observe'); s.add_argument('--db',type=Path,required=True); s.add_argument('--input',type=Path,required=True)
    a=ap.parse_args(argv)
    if a.cmd=='sync': out=sync(a.catalog,a.output_db,a.raw_dir,a.replace)
    elif a.cmd=='status': out=status(a.db)
    elif a.cmd=='show': out=show_code(a.db,a.system,a.version,a.code)
    elif a.cmd=='translate': out=translate(a.db,a.from_system,a.from_version,a.from_code,a.to_system,a.to_version,a.max_hops)
    else: out=add_empirical_observation(a.db,json.loads(a.input.read_text(encoding='utf-8')))
    print(json.dumps(out,indent=2,sort_keys=True,ensure_ascii=False)); return 0


if __name__=='__main__': raise SystemExit(main())
