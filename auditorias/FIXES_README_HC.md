# FIXES_README_HC
- README (Publicar, paso 3): el check de Healthchecks pasa a modo Simple con Period = 1 día y Grace Time = 12 h (alerta tras 36 h sin ping). Motivo: GitHub Actions omite crons con frecuencia y solo queremos aviso ante una caída real.
- README (keepalive de Supabase): aclarado que `HC_SUPABASE_URL` es opcional y que, si falta, el workflow solo emite un `::warning::`.
- `tests/test_workflows.py`: el test del README ya no exige "horario cron `<cron>`"; comprueba que menciona Period = 1 día y gracia de 12 h, sin depender del cron del workflow.
- Sin más cambios; pytest y ruff pasan.
