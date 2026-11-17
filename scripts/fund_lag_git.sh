#!/usr/bin/env bash
# Git del experimento del retraso de los fondos 0P. Dos subcomandos; NINGUNO toca otra rama que `probe-data` (rama HUÉRFANA, ni main ni la rama `data` del feed).
#
#   fund_lag_git.sh head OUT      Lee SOLO la primera línea de lag/fund_lag.jsonl de probe-data (lectura anónima: el repo es público) y la deja en OUT.
#                                 Escribe `state=found|missing|error` en $GITHUB_OUTPUT (y en stdout):
#                                   missing = no existe la rama o el fichero (primera ejecución) · error = no se pudo comprobar (red, GitHub caído).
#   fund_lag_git.sh publish NEW   Añade las líneas de NEW (ya validadas por el workflow) a lag/fund_lag.jsonl de probe-data y empuja SOLO esa rama,
#                                 sin --force. Si la rama no existe la crea huérfana. Un push rechazado o fallido sale con código != 0 (el workflow lo trata como aviso).
#
# Variables (las del runner; las de URL existen para poder probar contra un repositorio local):
#   GITHUB_REPOSITORY  owner/repo        READ_URL  (por defecto https://github.com/$GITHUB_REPOSITORY.git)
#   GH_TOKEN           token del job de escritura (solo `publish`)        PUSH_URL  (por defecto https://x-access-token:$GH_TOKEN@github.com/$GITHUB_REPOSITORY.git)
#   RUNNER_TEMP        directorio de trabajo temporal (por defecto /tmp)
# Sin secretos: GH_TOKEN es el token efímero del propio job (github.token), con contents: write.
set -u
BRANCH="probe-data"
FILE="lag/fund_lag.jsonl"
export GIT_TERMINAL_PROMPT=0
READ_URL="${READ_URL:-https://github.com/${GITHUB_REPOSITORY:-}.git}"
WORK="${RUNNER_TEMP:-/tmp}"

emit_state() {
  echo "state=$1"
  [ -n "${GITHUB_OUTPUT:-}" ] && echo "state=$1" >> "$GITHUB_OUTPUT"
  return 0
}

cmd_head() {
  out="$1"
  : > "$out"
  git ls-remote --exit-code --heads "$READ_URL" "$BRANCH" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 2 ]; then emit_state missing; return 0; fi
  if [ "$rc" -ne 0 ]; then emit_state error; return 0; fi
  tmp="$(mktemp -d "$WORK/lag-head.XXXXXX")"
  if ! git -C "$tmp" init -q || ! git -C "$tmp" fetch -q --depth=1 "$READ_URL" "$BRANCH" 2>/dev/null; then emit_state error; return 0; fi
  if ! git -C "$tmp" cat-file -e "FETCH_HEAD:$FILE" 2>/dev/null; then emit_state missing; return 0; fi
  git -C "$tmp" show "FETCH_HEAD:$FILE" 2>/dev/null | head -n 1 > "$out"
  emit_state found
}

cmd_publish() {
  new="$1"
  [ -s "$new" ] || { echo "::error::no hay líneas que publicar"; return 1; }
  : "${GH_TOKEN:?falta el token del job}"
  push_url="${PUSH_URL:-https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY:-}.git}"
  repo="$(mktemp -d "$WORK/lag-pub.XXXXXX")"
  git -C "$repo" init -q || return 1
  git -C "$repo" ls-remote --exit-code --heads "$READ_URL" "$BRANCH" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 0 ]; then
    git -C "$repo" fetch -q --depth=1 "$READ_URL" "$BRANCH" || return 1
    git -C "$repo" checkout -q -B "$BRANCH" FETCH_HEAD || return 1
  elif [ "$rc" -eq 2 ]; then
    git -C "$repo" checkout -q --orphan "$BRANCH" || return 1
  else
    echo "::warning::no se pudo comprobar la rama $BRANCH"
    return 1
  fi
  mkdir -p "$repo/lag"
  cat "$new" >> "$repo/$FILE"
  git -C "$repo" add "$FILE" || return 1
  git -C "$repo" -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit -q -m "fund-lag: $(wc -l < "$new" | tr -d ' ') líneas" || return 1
  git -C "$repo" push -q "$push_url" "HEAD:refs/heads/$BRANCH" 2>&1 | sed -e "s#${GH_TOKEN}#***#g"
  return "${PIPESTATUS[0]}"
}

case "${1:-}" in
  head)    [ $# -eq 2 ] || { echo "uso: $0 head OUT" >&2; exit 2; }; cmd_head "$2" ;;
  publish) [ $# -eq 2 ] || { echo "uso: $0 publish NEW" >&2; exit 2; }; cmd_publish "$2" ;;
  *)       echo "uso: $0 head OUT | publish NEW" >&2; exit 2 ;;
esac
