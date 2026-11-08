# Migrations

Hand-written SQL migrations, one file per schema change, numbered by revision.

```bash
export ATLAS_DB_DSN='postgresql+asyncpg://atlas:***@127.0.0.1:5432/atlas'
pip install -r infra/db/requirements.txt
alembic -c infra/db/alembic.ini upgrade head
alembic -c infra/db/alembic.ini revision --autogenerate -m "0001 core"   # empty on purpose
```

`--autogenerate` yields an empty revision because there is no ORM metadata: the money,
order and verdict tables are written and reviewed by hand. Print the SQL of a pending
migration with `alembic -c infra/db/alembic.ini upgrade head --sql`.

Migration order (plan.md): `0001` core, `0002` market hypertables and aggregates,
`0003` decision/verdict/order/position tables, `0004` audit and promotion tables.
Revisions are authored in Phase 2; this directory holds only the runner at M0.
