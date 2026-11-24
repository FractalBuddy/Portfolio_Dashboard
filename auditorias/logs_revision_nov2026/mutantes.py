"""10 mutantes al azar (semilla 2026-11-23) sobre una COPIA del repo; cada uno contra la suite completa (-x). Solo escribe en el scratchpad."""
import random, shutil, subprocess, sys, os, time, json
REPO = "/home/user/Portfolio_Dashboard"
S = sys.argv[1]
PY = S + "/venv/bin/python"
M = [
 ("M01", "scripts/feed_v2_common.py", "hi <= NEIGHBOR_SPREAD * lo and (x > SPIKE_UP * hi or x < SPIKE_DOWN * lo)", "hi <= NEIGHBOR_SPREAD * lo and (x > SPIKE_UP * hi)", "spike: no detecta picos a la baja"),
 ("M02", "scripts/feed_v2_common.py", "return currency_family(a) == currency_family(b)", "return currency_family(a)[0] == currency_family(b)[0]", "same_unit ignora la escala (GBp == GBP)"),
 ("M03", "scripts/feed_v2_common.py", "rng[0] <= price <= rng[1]", "rng[0] <= price", "price_plausible sin tope superior"),
 ("M04", "scripts/feed_v2.py", "        if gap_days <= limit:", "        if gap_days < limit:", "period_for_gap: límite estricto (borde)"),
 ("M05", "scripts/feed_v2.py", "abs(r - 1) > REBASE_TOLERANCE:", "abs(r - 1) > 10 * REBASE_TOLERANCE:", "rebase: tolerancia x10 (20 %)"),
 ("M06", "tests/privacy_guard.py", "return sum(1 for m in ISIN_WINDOW.finditer(text) if m.group(0) not in ISIN_PERMITIDOS)", "return sum(1 for m in ISIN_WINDOW.finditer(text) if m.group(0) not in ISIN_PERMITIDOS and m.group(0)[2] != '0')", "guardia ISIN: ignora los ISIN IE0… (casi todos)"),
 ("M07", "scripts/yahoo_shadow.py", "if a <= now.hour < b), None)", "if a <= now.hour <= b), None)", "franja de la sombra cerrada por arriba"),
 ("M08", "scripts/yahoo_shadow.py", "if len(e & f) / len(e) + 1e-9 < min_coverage:", "if len(e & f) / len(e) + 1e-9 < min_coverage / 2:", "sombra: cobertura de nombre a la mitad"),
 ("M09", "scripts/feed_v2.py", "abs(f) <= 1000 else None", "abs(f) <= 100 else None", "_pct: cota de variación cripto 100 %"),
 ("M10", "scripts/feed_v2.py", "if (self.today - last_day).days > FROZEN_DAYS:", "if (self.today - last_day).days > 10 * FROZEN_DAYS:", "símbolo congelado: 450 días"),
 ("M11", "scripts/feed_v2.py", "if len(dropped_all) > MAX_DROPPED_BARS or", "if len(dropped_all) > 10 * MAX_DROPPED_BARS or", "tope de barras descartadas x10"),
 ("M12", "scripts/yahoo_shadow.py", "if not isinstance(d, date) or d >= today:", "if not isinstance(d, date) or d > today:", "sombra: acepta la barra de HOY (abierta)"),
 ("M13", "scripts/freshness_gate.py", "    if age_min < 0:\n", "    if age_min < -60:\n", "puerta: tolera generated_at 1 h en el futuro"),
 ("M14", "scripts/freshness_gate.py", 'return event == "workflow_dispatch" and', 'return event in ("workflow_dispatch", "schedule") and', "force también en schedule"),
]
rng = random.Random(20261123)
pick = sorted(rng.sample(M, 10))
import glob
res = [json.loads(l) for l in open(os.path.join(S,'logs','mutantes_stdout.log')) if l.startswith('{')]
done={r['id'] for r in res}
print('sorteo:', [m[0] for m in pick], flush=True)
for mid, rel, old, new, desc in pick:
    if mid in done: continue
    work = os.path.join(S, "mut", mid)
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    p = os.path.join(work, rel)
    t = open(p, encoding="utf-8").read()
    assert t.count(old) == 1, (mid, t.count(old))
    open(p, "w", encoding="utf-8").write(t.replace(old, new))
    t0 = time.time()
    r = subprocess.run([PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"], cwd=work, capture_output=True, text=True)
    last = [l for l in r.stdout.splitlines() if l.strip()][-1:] 
    failed = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    res.append({"id": mid, "file": rel, "desc": desc, "killed": r.returncode != 0, "rc": r.returncode, "secs": round(time.time() - t0), "summary": last, "first_failure": failed})
    print(json.dumps(res[-1], ensure_ascii=False), flush=True)
    shutil.rmtree(work, ignore_errors=True)
json.dump(res, open(os.path.join(S, "logs", "mutantes.json"), "w"), ensure_ascii=False, indent=1)
