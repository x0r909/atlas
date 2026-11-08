# ATLAS

Sistem trading kripto otomatis untuk satu operator, satu VPS, dan satu akun bursa —
dengan modal kecil, tesis pasar yang dipantau LLM, dan Risk Engine yang tidak bisa
dilangkangi oleh siapa pun, termasuk oleh LLM-nya sendiri.

> **Status: tahap spesifikasi.** Belum ada kode yang berjalan. Artifact M0–M3
> (spec, plan, riset, data model, kontrak, 124 task) sudah lengkap dan konsisten;
> implementasi dimulai dari `specs/001-autonomous-crypto-trader/tasks.md` Phase 1.

## Batasan yang membentuk desain

Bukan angka opsional — ini yang membuat arsitekturnya terlihat seperti sekarang.

| Batasan | Nilai | Konsekuensi |
|---|---|---|
| Modal total | Rp100.000 | posisi kecil, stop distance sempit |
| Minimum order Indodax | Rp10.000 | ≤5 posisi bersamaan, tidak ada averaging |
| Biaya LLM | ≤ Rp5.000/hari | ≤6k token input per siklus |
| Anggaran total | Rp250.000/bulan | satu VPS, tanpa managed service |
| Drawdown | 10% per stage → HALTED | HWM per stage Live |
| Host | 1 VPS, 4 GB RAM, 80 GB disk | tanpa HA, harus gagal-aman |

**Nol biaya data berbayar.** Harga dari bursa, berita dari RSS gratis yang di-allowlist.

## Arsitektur singkat

```text
market ingest ─→ news ingest ─→ agents (LLM) ─→ TradeProposal
                                                    │
                                            risk-engine (murni, <50 ms)
                                                    │ SignedOrder (HMAC, 60 s, sekali pakai)
                                            execution (satu-satunya penulis order)
                                                    │
                                              venue adapter ─→ Indodax

stop-monitor (proses terpisah) ── heartbeat 30 s ── watchdog → HALTED
terminal-web (Next.js) ← api-gateway ← event bus (Redis Streams)
```

Sembilan service backend + satu web app. Pemisahan proses bukan hiasan: `stop-monitor`
terpisah karena pengawas stop tidak boleh mati bersama proses yang dia awasi.

## Mode

`Backtest → Paper → Co-pilot → Autonomous`, ditambah `Halted`.

Promosi ke stage berikutnya hanya lewat promotion gate: kriteria kuantitatif harus
lulus, dievaluasi backend, dan dikonfirmasi manusia dengan re-auth TOTP/passkey.
Paper adalah default; sistem tidak pernah jatuh ke live karena konfigurasi kosong.

## Struktur repositori

```text
.specify/                     kerangka Spec Kit (templat, skrip, konstitusi)
specs/001-autonomous-crypto-trader/
  spec.md                     kebutuhan produk (FR, SC, user story)
  plan.md                     keputusan teknis + Constitution Check
  research.md                 riset dan angka yang mendasarinya
  data-model.md               skema dan invariant
  contracts/                  api-gateway, decision-risk, venue-adapter,
                              event-bus, stop-monitor, telegram-control
  quickstart.md               skenario S1–S18 + kriteria keluar M3
  tasks.md                    124 task M0–M3, test-first, jalur kritis
  checklists/                 kualitas spec + keamanan risk/execution
.pi/prompts/                  slash-command Spec Kit untuk agen Pi
```

## Cara membaca

1. `specs/001-autonomous-crypto-trader/spec.md` — apa yang dibangun dan untuk siapa.
2. `.specify/memory/constitution.md` — aturan yang tidak boleh dilanggar.
3. `plan.md` lalu `data-model.md` lalu `contracts/` — bagaimana dibangun.
4. `tasks.md` — urutan kerja; Phase 1 bisa dimulai tanpa membaca sisanya.

## Prinsip keselamatan

- LLM hanya mengusulkan; ia tidak punya kredensial bursa dan tidak bisa menyentuh order.
- Setiap order masuk wajib punya `stop_price`; order tanpa stop ditolak schema.
- Verdict risk berlaku 60 detik dan hanya sekali pakai — order kedaluwarsa ditolak lokal.
- Order yang menutup posisi (reduce-only) tetap jalan saat Halted, breaker aktif,
  atau anggaran habis. Yang diblokir hanya pembukaan posisi.
- Semua order lewat satu penulis, dengan `client_order_id` deterministik agar
  pengiriman ulang tidak pernah menggandakan posisi.
- State `UNKNOWN` adalah state sah: posisi dibekukan, ditanyakan ke bursa, tidak
  pernah dikirim ulang.

## Lisensi

MIT — lihat [LICENSE](LICENSE).

## Peringatan

Perangkat lunak ini dapat kehilangan uang. Modal yang dialokasikan sengaja kecil
(Rp100.000) dan itu bagian dari desain, bukan kekurangan. Jalankan hanya dengan dana
yang siap hilang, dan jangan pernah menonaktifkan promotion gate.
