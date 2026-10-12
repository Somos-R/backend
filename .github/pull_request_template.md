## Qué hace y por qué

<!-- Qué cambia y qué problema resuelve. -->

## Antes de mergear

- [ ] `ruff check`, `mypy` y `pytest --cov` pasan
- [ ] **¿Agrega o actualiza dependencias (`pyproject.toml` / `poetry.lock`)?** Si sí: `docker compose restart app` (o `up -d --build --no-deps app`) y `curl localhost:8000/health/ready` da 200. Quien haga `git pull` debe hacer lo mismo
- [ ] **¿Incluye una migración?** Si sí: `docker compose exec app poetry run alembic upgrade head`
- [ ] **¿Agrega variables de entorno?** Si sí: están en `.env.example` y en la guía de despliegue (`docs-archivo/back/despliegue.md`, fuera del repo)
- [ ] **¿Cambia un contrato de la API?** Si sí: avisado al frontend y `docs.py` actualizado

## Para el revisor

<!-- Riesgos, decisiones y pendientes que conviene conocer. -->
