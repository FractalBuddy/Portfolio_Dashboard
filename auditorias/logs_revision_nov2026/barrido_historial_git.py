"""Barrido de privacidad del historial completo: cada blob de cada commit de todas las ramas.
Imprime ruta, commits y CONTADORES/huellas cortas; nunca el valor."""
import hashlib, json, re, subprocess, sys, collections
sys.path.insert(0, "tests")
import privacy_guard as pg
repo = "."
def git(*a): return subprocess.run(["git", *a], capture_output=True, cwd=repo).stdout
gen = {e["isin"] for e in json.loads(open("config/fund_probe_isins_generic.json").read())}
# ISIN cualquier país con dígito de control válido
ISIN_ANY = re.compile(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}[0-9])(?![A-Z0-9])")
COUNTRIES = set("IE LU DE FR GB US NL BE ES IT AT CH SE DK FI NO JE GG KY CA AU JP XS".split())
def valid(isin):
    try: return pg.isin_check_digit(isin[:11]) == int(isin[11])
    except Exception: return False
PATTERNS = {
  "supabase_url": re.compile(r"[a-z0-9]{15,}\.supabase\.(co|in)"),
  "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
  "sb_key": re.compile(r"sb_(publishable|secret)_[A-Za-z0-9_-]{10,}"),
  "gh_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
  "aws": re.compile(r"AKIA[0-9A-Z]{16}"),
  "hc_ping_uuid": re.compile(r"hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
  "hc_ping_slug": re.compile(r"hc-ping\.com/[A-Za-z0-9_-]{20,}/"),
  "fred_key": re.compile(r"(?i)api_key=[0-9a-f]{32}"),
  "hex32": re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])"),
  "cg_key": re.compile(r"CG-[A-Za-z0-9]{20,}"),
  "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
  "msid": re.compile(r"(?i)(id=|SecId=)F0[0-9A-Z]{8}|F0000[0-9A-Z]{5}"),
}
seen = set(); res = collections.defaultdict(lambda: collections.defaultdict(set))
commits = git("rev-list", "--all").decode().split()
for c in commits:
    for line in git("ls-tree", "-r", c).decode().splitlines():
        meta, path = line.split("\t", 1)
        _, typ, sha = meta.split()
        if typ != "blob" or (sha, path) in seen: continue
        seen.add((sha, path))
        data = git("cat-file", "-p", sha)
        try: t = data.decode("utf-8")
        except UnicodeDecodeError: res[path]["binario"].add(c[:7]); continue
        # ISIN irlandés vía guardia
        if path not in ("config/fund_probe_isins_generic.json",):
            extra = [w for w in pg.ISIN_WINDOW.findall(t) if w not in pg.ISIN_PERMITIDOS and w not in gen]
            if extra: res[path]["IE_no_permitido:" + ",".join(sorted({hashlib.sha256(w.encode()).hexdigest()[:8] for w in extra}))].add(c[:7])
        for m in ISIN_ANY.findall(t):
            if m[:2] in COUNTRIES and m[:2] != "IE" and valid(m) and m not in gen:
                res[path]["ISIN_" + m[:2] + "_valido:" + hashlib.sha256(m.encode()).hexdigest()[:8]].add(c[:7])
        n0 = pg.zero_p_leaks(t)
        if n0: res[path]["0P_no_permitido_x%d:%s" % (n0, ",".join(sorted({hashlib.sha256(w.encode()).hexdigest()[:8] for w in pg.ZERO_P_WINDOW.findall(t) if w not in pg.ZERO_P_SINTETICOS and hashlib.sha256(w.encode()).hexdigest() not in pg.ZERO_P_FIJOS_SHA256})))].add(c[:7])
        n1 = pg.product_id_leaks(t)
        if n1: res[path]["ids_ficha_x%d" % n1].add(c[:7])
        if pg.hits(t, pg.PROXY_HASHES): res[path]["proxy_dueno"].add(c[:7])
        ch = pg.hits(t, pg.CRYPTO_HASHES)
        if ch: res[path]["cripto_idio:%d" % len(ch)].add(c[:7])
        for k, rx in PATTERNS.items():
            ms = rx.findall(t) if k != "hex32" else rx.findall(t)
            if ms: res[path][k + "_x%d" % len(ms)].add(c[:7])
print("commits:", len(commits), "blobs/ruta:", len(seen))
for p in sorted(res):
    for k, cs in sorted(res[p].items()):
        print(f"{p}\t{k}\tcommits={len(cs)} ej={sorted(cs)[:3]}")
