"""scripts/fund_lag_git.sh contra repositorios git LOCALES (sin red): lectura de la primera línea, creación de la rama huérfana, añadido, push sin force y solo a `probe-data`."""
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fund_lag_git.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="necesita git y bash")

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(*args, cwd=None, env=None, check=True):
    e = {**os.environ, **GIT_ENV, **(env or {})}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=e, check=check)


def bare(tmp_path, name="remote.git"):
    p = tmp_path / name
    git("init", "-q", "--bare", "-b", "main", str(p))
    return p


def seed_other_branches(tmp_path, remote):
    """Deja en el remoto `main` y `data` con contenido propio y devuelve sus SHA (no deben cambiar nunca)."""
    w = tmp_path / "seed"
    git("init", "-q", "-b", "main", str(w))
    (w / "README").write_text("main\n")
    git("add", "README", cwd=w)
    git("commit", "-q", "-m", "main", cwd=w)
    git("checkout", "-q", "--orphan", "data", cwd=w)
    (w / "README").write_text("feed\n")
    git("add", "README", cwd=w)
    git("commit", "-q", "-m", "data", cwd=w)
    git("push", "-q", str(remote), "main", "data", cwd=w)
    return {b: git("--git-dir", str(remote), "rev-parse", b).stdout.strip() for b in ("main", "data")}


def sh(tmp_path, *args, remote=None, push=None, extra=None, path=None):
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR", "GIT_COMMITTER"))}, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "RUNNER_TEMP": str(tmp_path), "GH_TOKEN": "tok-efimero", "GITHUB_REPOSITORY": "o/r",
           "READ_URL": "file://" + str(remote), "PUSH_URL": "file://" + str(push or remote), "GITHUB_OUTPUT": str(tmp_path / "gh_out")}
    env.update(extra or {})
    if path:
        env["PATH"] = path + os.pathsep + env["PATH"]
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)


def state(tmp_path):
    return dict(ln.split("=", 1) for ln in (tmp_path / "gh_out").read_text().splitlines())["state"]


def new(tmp_path, text, name="new.jsonl"):
    p = tmp_path / name
    p.write_text(text)
    return p


def branch_file(remote, branch="probe-data"):
    return git("--git-dir", str(remote), "show", f"{branch}:lag/fund_lag.jsonl").stdout


# ------------------------------------------------------------------ head
def test_head_sin_rama_es_missing_y_deja_el_fichero_vacio(tmp_path):
    r = bare(tmp_path)
    out = tmp_path / "head.txt"
    p = sh(tmp_path, "head", str(out), remote=r)
    assert p.returncode == 0 and state(tmp_path) == "missing" and out.read_text() == ""


def test_head_con_repositorio_inalcanzable_es_error_no_missing(tmp_path):
    p = sh(tmp_path, "head", str(tmp_path / "h"), remote=tmp_path / "no_existe.git")
    assert p.returncode == 0 and state(tmp_path) == "error"


def test_head_devuelve_solo_la_primera_linea(tmp_path):
    r = bare(tmp_path)
    assert sh(tmp_path, "publish", str(new(tmp_path, '{"n":1}\n{"n":2}\n')), remote=r).returncode == 0
    out = tmp_path / "head.txt"
    assert sh(tmp_path, "head", str(out), remote=r).returncode == 0 and state(tmp_path) == "found" and out.read_text() == '{"n":1}\n'
    assert sh(tmp_path, "publish", str(new(tmp_path, '{"n":3}\n', "n2.jsonl")), remote=r).returncode == 0
    assert sh(tmp_path, "head", str(out), remote=r).returncode == 0 and out.read_text() == '{"n":1}\n'          # la primera línea no cambia al añadir


def test_head_con_rama_pero_sin_fichero_es_missing(tmp_path):
    r = bare(tmp_path)
    w = tmp_path / "w"
    git("init", "-q", "-b", "probe-data", str(w))
    (w / "otro").write_text("x")
    git("add", "otro", cwd=w)
    git("commit", "-q", "-m", "x", cwd=w)
    git("push", "-q", str(r), "probe-data", cwd=w)
    assert sh(tmp_path, "head", str(tmp_path / "h"), remote=r).returncode == 0 and state(tmp_path) == "missing"


# ------------------------------------------------------------------ publish
def test_publish_crea_la_rama_huerfana_con_un_solo_fichero_y_no_toca_main_ni_data(tmp_path):
    r = bare(tmp_path)
    before = seed_other_branches(tmp_path, r)
    p = sh(tmp_path, "publish", str(new(tmp_path, '{"a":1}\n')), remote=r)
    assert p.returncode == 0, p.stderr
    after = {b: git("--git-dir", str(r), "rev-parse", b).stdout.strip() for b in ("main", "data")}
    assert after == before                                                                    # main y data intactas
    assert git("--git-dir", str(r), "rev-list", "--parents", "probe-data").stdout.split() == [git("--git-dir", str(r), "rev-parse", "probe-data").stdout.strip()]   # raíz: sin padres
    assert git("--git-dir", str(r), "ls-tree", "-r", "--name-only", "probe-data").stdout.split() == ["lag/fund_lag.jsonl"]
    assert branch_file(r) == '{"a":1}\n'
    assert sorted(git("--git-dir", str(r), "for-each-ref", "--format=%(refname)").stdout.split()) == ["refs/heads/data", "refs/heads/main", "refs/heads/probe-data"]


def test_publish_anade_al_final_en_avance_rapido_y_conserva_las_lineas_anteriores(tmp_path):
    r = bare(tmp_path)
    for i in (1, 2, 3):
        assert sh(tmp_path, "publish", str(new(tmp_path, '{"n":%d}\n{"m":%d}\n' % (i, i), f"n{i}.jsonl")), remote=r).returncode == 0
    assert branch_file(r) == '{"n":1}\n{"m":1}\n{"n":2}\n{"m":2}\n{"n":3}\n{"m":3}\n'
    parents = git("--git-dir", str(r), "rev-list", "--parents", "probe-data").stdout.splitlines()
    assert len(parents) == 3 and sum(len(p.split()) == 1 for p in parents) == 1               # historia lineal de 3 commits con una sola raíz


def test_publish_firma_como_el_bot_de_actions_y_no_como_nadie_mas(tmp_path):
    r = bare(tmp_path)
    assert sh(tmp_path, "publish", str(new(tmp_path, '{"a":1}\n')), remote=r).returncode == 0
    who = git("--git-dir", str(r), "log", "-1", "--format=%an <%ae> | %cn <%ce>", "probe-data").stdout.strip()
    assert who == "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com> | github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"


def test_un_push_rechazado_por_no_ser_avance_rapido_falla_sin_forzar_y_no_cambia_la_rama(tmp_path):
    """El script construye sobre lo que LEE (READ_URL); aquí el destino (PUSH_URL) tiene otra historia: sin --force el push se rechaza y el destino queda igual."""
    leido, destino = bare(tmp_path, "leido.git"), bare(tmp_path, "destino.git")
    assert sh(tmp_path, "publish", str(new(tmp_path, '{"leido":1}\n')), remote=leido).returncode == 0
    assert sh(tmp_path, "publish", str(new(tmp_path, '{"destino":1}\n', "d.jsonl")), remote=destino).returncode == 0
    antes = git("--git-dir", str(destino), "rev-parse", "probe-data").stdout
    p = sh(tmp_path, "publish", str(new(tmp_path, '{"x":1}\n', "x.jsonl")), remote=leido, push=destino)
    assert p.returncode != 0
    assert git("--git-dir", str(destino), "rev-parse", "probe-data").stdout == antes and branch_file(destino) == '{"destino":1}\n'


def test_un_push_rechazado_por_el_servidor_falla_con_codigo_distinto_de_cero(tmp_path):
    r = bare(tmp_path)
    hook = r / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho rechazado >&2\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    p = sh(tmp_path, "publish", str(new(tmp_path, '{"a":1}\n')), remote=r)
    assert p.returncode != 0 and git("--git-dir", str(r), "for-each-ref").stdout == ""


def test_no_poder_comprobar_la_rama_falla_sin_empujar_nada(tmp_path):
    p = sh(tmp_path, "publish", str(new(tmp_path, '{"a":1}\n')), remote=tmp_path / "no_existe.git")
    assert p.returncode != 0 and "::warning::" in p.stdout


def test_publish_sin_lineas_o_sin_token_falla(tmp_path):
    r = bare(tmp_path)
    assert sh(tmp_path, "publish", str(new(tmp_path, "")), remote=r).returncode != 0
    p = sh(tmp_path, "publish", str(new(tmp_path, '{"a":1}\n', "ok.jsonl")), remote=r, extra={"GH_TOKEN": ""})
    assert p.returncode != 0 and git("--git-dir", str(r), "for-each-ref").stdout == ""


def test_el_token_no_sale_en_la_salida_aunque_el_push_lo_cite(tmp_path):
    """git ya oculta las credenciales de la URL en sus errores; aquí un `git` de pega que SÍ las imprime comprueba que el `sed` del script las enmascara."""
    r = bare(tmp_path)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    real = shutil.which("git")
    shim = bin_ / "git"
    shim.write_text(f'#!/bin/bash\nfor a in "$@"; do [ "$a" = push ] && {{ echo "fatal: push a $*" ; exit 1; }}; done\nexec {real} "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    p = sh(tmp_path, "publish", str(new(tmp_path, '{"a":1}\n')), remote=r, push="x", extra={"PUSH_URL": "https://x-access-token:tok-efimero@github.com/o/r.git"}, path=str(bin_))
    assert p.returncode != 0 and "tok-efimero" not in p.stdout + p.stderr and "***" in p.stdout + p.stderr


def test_uso_incorrecto_sale_con_2(tmp_path):
    r = bare(tmp_path)
    for args in ((), ("otra", "x"), ("head",), ("publish",), ("head", "a", "b")):
        assert sh(tmp_path, *args, remote=r).returncode == 2, args


def test_el_script_es_sintaxis_bash_valida_y_ejecutable():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
