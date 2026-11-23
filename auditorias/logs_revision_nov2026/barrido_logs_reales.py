import sys,json,re,hashlib
sys.path.insert(0,"tests"); import privacy_guard as pg
gen={e["isin"] for e in json.load(open("config/fund_probe_isins_generic.json"))}
ISIN_ANY=re.compile(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}[0-9])(?![A-Z0-9])")
def valid(i):
    try: return pg.isin_check_digit(i[:11])==int(i[11])
    except Exception: return False
for p in sys.argv[1:]:
    t=open(p).read()
    try: t=json.loads(t)["logs_content"]
    except Exception: pass
    isins=[m for m in ISIN_ANY.findall(t) if valid(m)]
    out={"isin_validos_total":len(isins),"isin_no_genericos":len([i for i in isins if i not in gen]),
         "0P_no_permitidos":pg.zero_p_leaks(t),"ids_ficha":pg.product_id_leaks(t),
         "supabase":len(re.findall(r"[a-z0-9]{15,}\.supabase\.(co|in)",t)),"hc_ping_clave":len(re.findall(r"hc-ping\.com/[A-Za-z0-9_-]{16,}",t)),
         "jwt":len(re.findall(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",t)),"proxy_dueno":len(pg.hits(t,pg.PROXY_HASHES)),"cripto_idio":len(pg.hits(t,pg.CRYPTO_HASHES)),
         "add_mask_lineas":t.count("::add-mask::")}
    print(p.split("/")[-1], json.dumps(out))
