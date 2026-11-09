# HANDOFF

Baca file ini dulu; jangan baca spec.md/plan.md/tasks.md utuh. Buka hanya baris task + FR yang dirujuk.

## Status
- Terakhir selesai: T067 (commit `391bb88`), T066, T065b, plus `58326f2` (golden biaya).
- Berikutnya: T068/T069 (order state machine), lalu T070/T071, T072/T073, T074/T075, T075a/b,
  T071a/b, T076/T077, T078, lalu Phase 8, 9, 5, 10.
- Gate: `ATLAS_DB_DSN=postgresql://atlas:atlas@localhost:5432/atlas_fix_check .venv/bin/pytest tests -q`
  + `.venv/bin/ruff check .` → wajib 0 failed / 0 skipped. Jangan tampilkan output penuh.

## Aturan yang tidak boleh dilanggar
- Test lebih dulu; commit per slice (≤ ~1.200 baris); tanpa amend; **tanpa push**.
- Tanpa perintah destruktif (DROP/TRUNCATE/downgrade/reset DB/rm -rf/reset --hard/force push)
  tanpa bertanya. DSN hanya localhost + nama DB uji.
- Mode PAPER; tidak ada LIVE, tidak ada kredensial exchange di repo, tidak ada secret.
- Uang = Decimal string di audit; schema berubah hanya lewat migrasi baru.
- Hanya `orders.py` yang boleh memanggil place/cancel venue; exit (reduce-only) tidak pernah diblokir.

## Invarian aktif
- `risk_verdict` dibelanjakan tepat sekali (`consumed_at` + `consumed_by_order_id` berpasangan).
- Hash chain audit v2: sha256(domain ‖ record_type ‖ 0x00 ‖ prev ‖ canonical ‖ seq_be64).
- Mode: PAPER→CO_PILOT→AUTONOMOUS, *→HALTED; HALTED hanya ke PAPER/CO_PILOT.
- `mode_state` append-only (trigger), urut `seq`; audit append-only di DB.
- Biaya Indodax: **satu sumber** `services/execution/atlas_execution/venues/indodax_facts.py`
  (persen: maker 0,1 / taker 0,2 / PPh 0,21 / PPN 0 / CFX NULL, assumed 0,1). Seed SQL
  `infra/db/seeds/0001_venue_profile_indodax.sql` wajib sama dengan itu dan dengan ADR 0001.
  Jangan pernah menulis angka biaya dari ingatan.

## Lokasi modul kunci
- `services/execution/atlas_execution/gate.py` — verdict gate (`decide`, `consume`, `mode_gate`).
- `services/execution/atlas_execution/orders.py` — satu-satunya penulis order.
- `services/execution/atlas_execution/audit.py` — penulis audit (SQLAlchemy).
- `packages/common/atlas_common/audit_chain.py` — penulis audit (asyncpg, gateway).
- `services/risk-engine/atlas_risk/{engine,rules,costs,verdict,baseline,management}.py`.
- `services/api-gateway/atlas_api/{auth,app,repo,ws}.py`; `tests/unit/risk/` konfigurasi uji.
