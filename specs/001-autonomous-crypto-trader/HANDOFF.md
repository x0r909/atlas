# HANDOFF

Baca file ini dulu; jangan baca spec.md/plan.md/tasks.md utuh. Buka hanya baris task + FR yang dirujuk.

## Status
- Terakhir selesai: **T086/T087** (`52423bd`: `0009_protective_stop.py`,
  `execute.py` `StopAcceptance`, `orders.py` `place_protective`, `tests/contract/test_protective_stop.py`
  7 test), lalu T070/T071 (`1979ae7`: `fills.py`, `position_sync.py`, `state_machine.py` fee columns,
  `tests/integration/test_partial_fill.py`), T068/T069 (`state_machine.py`), T067 (`391bb88`),
  T066, T065b, plus `58326f2` (golden biaya).
- Dependency check T071↔T087: **tidak ada dependency**. T071 hanya mengembalikan `StopResize`
  (permintaan), tidak menaruh order stop; penempatan stop adalah jalur T087 yang dipakai T084.
  Jadi T070/T071 tetap selesai, dan pasangan berikutnya adalah T086/T087.
- Urutan kerja baru:
  - Tahap A (jalur minimum menuju loop Paper pertama T092): T086/T087 (selesai),
    T070/T071 (selesai), **T072/T073 (berikutnya)**, T079/T080, T091, T093, T092.
  - Tahap B: setelah T092 jalan, loop Paper beberapa jam; catat bug integrasi, perbaiki dengan
    commit terpisah (bukan bagian slice task).
  - Tahap C (sebelum run panjang): T074/T075, T084/T085, T075a/b, T071a/b, T076/T077,
    T081/T082, T083, T087a, T090.
  - Tahap D (sebelum ada API key atau Live; kriteria GAGAL di T097): sisa task, checklist
    risk-execution-security 41 item tercentang operator, review kode Phase 6-8 dengan model kuat,
    T023 (probe read-only + batas Lite/Pro), role DB non-owner, tail checkpoint audit, T080a/b,
    T088/T089.
- Satu pasangan test-lalu-implementasi per sesi; tanpa stub jalur stop.
- Catatan T087: stop tanpa verdict lolos tanpa gerbang mode (reduce-only), tapi ditolak kalau
  `quantity > position.quantity`; satu-satunya tulisan stop yang sah adalah `place_protective`
  (signed_order_id NULL + `is_protective`), dan request yang menyebut pair/venue lain adalah
  `ValueError` (wiring error, bukan refusal). Stop yang lebih lebar → `STOP_NOT_TIGHTENING`
  dicatat `REFUSED`; `generation` sama + harga sama → `Accepted(replayed=True)`; stop lama
  dibatalkan lebih dulu, dan `stop_protection` jadi `NATIVE_ORDER` karena order yang duduk di
  venue itu yang menjaga. Penempatan stop tidak membaca `venue.profile()` di `__init__`
  (`OrderWriter(venue_id=...)` opsional) — jalur fill dilarang membacanya (regresi
  `test_partial_fill.py`). Downgrade 0009 menghapus order `is_protective` (tidak ada verdict di
  belakangnya) dan gagal keras, bukan melebarkan stop diam-diam, kalau ada stop ≥ harga entry.
- Catatan T070: `fill` unik per `(venue_id, venue_trade_id)` sehingga laporan duplikat tidak
  menggandakan fill; timer remainder memakai `COALESCE(submitted_at, created_at)` karena `order`
  tidak punya kolom `unfilled_since`; kolom biaya order adalah `fees_paid_idr`/`tax_paid_idr`,
  posisi `fees_idr`/`taxes_idr`; `position` tidak punya `updated_at`.
- Catatan lingkungan: DB dev `atlas` sempat tertinggal di migrasi `0005` (sudah `upgrade head`) dan
  trigger append-only `audit_record` hilang (sudah dipulihkan manual dari `0004_audit.py`). DB
  `atlas_fix_check` bersih. `mode_state` di DB dev tidak punya `seq`/`actor_ref`, jadi dua test
  `test_order_verdict_gate.py` gagal di situ; di DB gate lulus.
- Catatan T069: `UNKNOWN` non-terminal dan guard satu `UNKNOWN` per venue ada di state machine
  (`VenueFrozen`), bukan di `order` constraint; `FILLED->CANCELED` ditolak dan dicatat `REFUSED`.
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
