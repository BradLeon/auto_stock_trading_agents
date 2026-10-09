import hashlib,json,sqlite3,sys
from pathlib import Path
from contextlib import closing
from ats.workflow.business_replay_inputs import implementation_hashes

out=Path(sys.argv[1]); result={"implementation":implementation_hashes(),"authorities":{}}
for name in ("var/phase_f_cutover.sqlite","var/phase_f_routes.sqlite","var/phase_f_dispatch.sqlite","var/phase_f_batches.sqlite"):
    path=Path(name).resolve()
    if not path.is_file():
        result["authorities"][name]={"exists":False};continue
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        names=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        tables={n:sorted([repr(tuple(r)) for r in conn.execute('SELECT * FROM "'+n.replace('"','""')+'"')]) for n in names}
    result["authorities"][name]={"exists":True,"tables":{n:len(v) for n,v in tables.items()},"hash":hashlib.sha256(json.dumps(tables,sort_keys=True).encode()).hexdigest()}
for name in ("var/structured_data/releases.yaml","config/workflow/workflow_owners.yaml","config/workflow/phase_e_schedules.yaml"):
    p=Path(name)
    result["authorities"][name]={"exists":p.is_file(),"hash":hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None}
with out.open("x") as f:json.dump(result,f,indent=2)
print(json.dumps({"output":str(out),"closure_paths":len(result["implementation"]),"authorities":result["authorities"]}))
