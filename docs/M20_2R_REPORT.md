# M20.2R — Capture Correctness and Replay Audit

## Статус

**OFFLINE_VERIFIED** после корректирующего аудита.

Этот статус относится только к детерминированной обработке небольших синтетических сообщений, fault injection, bounded storage и локальному replay. Он не означает `LIVE_CAPTURE_VERIFIED`, не подтверждает полноту рыночного потока, производительность, сетевую задержку, predictive signal или trading edge и не разрешает Stage A/B.

Исходный Git checkpoint: `b28ed0a4ba64ea46083e4644714fea16963f49d1`. До исправлений состояние M20.2 зафиксировано в `docs/M20_2R_BASELINE_HASHES.json`: 24 файла, 75 992 байта. Первоначальные документы M20.2 и namespace `data/research/m20_2_offline_capture_v1` сохранены побайтно.

## Подтверждённые review findings

| Finding | Воспроизведение до исправления | Исправление | Regression evidence |
|---|---|---|---|
| BBO сравнивался как текст | Численно равный `100.00` против `100` дал `inconsistent`, ожидался `comparable` | Price/quantity разбираются как конечные `Decimal`; raw payload не изменяется | Trailing zeros и численно равные значения дают `comparable`; настоящее отличие — `inconsistent`; NaN, Infinity, отрицательные и нечисловые значения отклоняются |
| Depth применялся неатомарно | Valid bid + отрицательный ask изменил bid, затем выбросил `ValueError`; книга осталась `valid=True` | Обе стороны и IDs полностью валидируются, изменения применяются к копии, invariant проверяется до commit; отклонённый необходимый depth инвалидирует continuity | Книга до/после одинакова, `valid=False`; crossed book и `U>u` не коммитятся |
| Buffered evidence мог теряться | Старый snapshot path очищал buffer до полного успешного применения | Snapshot и весь buffered overlap проверяются на candidate state; buffer очищается только после общего commit | Crossed buffered update оставляет buffer; более новый snapshot безопасно поглощает его как stale |
| Session переносил старую непрерывность | Snapshot в `s1`, затем depth без snapshot в `s2` продолжил старую книгу до update 101 | Любая смена session создаёт новый continuity epoch: книга/checkpoints сбрасываются, depth ждёт snapshot | Новая session остаётся invalid/awaiting recovery, старые checkpoints отсутствуют |
| Обратный monotonic не останавливал обработку | Счётчик rejected увеличился, но depth всё равно применился и книга осталась valid | Monotonic reverse закрывает совместимую clock epoch, инвалидирует книгу и отклоняет событие до parsing/apply | Update ID не меняется, `monotonic_discontinuities=1`, интервал отмечен unknown |
| Receive sequence не проверялся | Envelope sequence не участвовал в continuity gate | Для каждой session проверяется строгая локальная последовательность; ошибка инвалидирует состояние до применения | Sequence 1→3 отклонён, книга не обновлена |
| Duration не закрывался на stop и rejected transition мог исчезнуть | Учёт зависел только от последующих принятых сообщений | Сначала время до события начисляется предыдущему состоянию, затем применяется transition; stop записывается отдельным control record с инъецируемыми часами | На шкале 0–1–4 s: 1 s valid + 3 s invalid = 4 s measured; реальные ожидания не используются |
| Counters смешивали stages и observations | Buffered receipt и его последующее apply могли попадать в один суммарный denominator | `depth_observations` считает уникальные event records; `depth_buffered/depth_applied` остаются operational stages; coverage публикуется отдельно | Один buffered depth: observations 1, buffered 1, applied 1, sequence checks 1 |
| Run всегда назывался OFFLINE_VERIFIED | Даже fault-stop report возвращал milestone status | Run report теперь отдельно содержит `processing_mode`, `run_outcome`, `stop_reason`, `data_quality` и `integrity_status`; milestone status живёт только в этом review | Message limit возвращает `STOPPED`, `message_limit`, `CAPTURE_NOT_REPLAY_VERIFIED` |
| `max_messages` назывался time limit | Expected `message_limit`, actual `time_limit` | Выделена причина `message_limit`; virtual elapsed time сохраняет `time_limit` | Оба stop paths проверены отдельно |
| Replay доверял файлам и default config | Raw можно было изменить без hash failure; config не сохранялся | Final manifest сохраняет effective config/schema/hashes/sizes, а `manifest.sha256` является finalization marker; несовместимый override отклоняется | Изменение одного байта raw или manifest обнаруживается; non-default config восстанавливается |
| Replay загружал весь raw в память | `read_bytes()` materialized весь файл | Hash и JSONL replay выполняются потоково; установлен per-record bound | Oversized record отклоняется без гигабайтных fixtures |
| Неполная finalization была неоднозначна | Отсутствие manifest не имело отдельного integrity verdict | Только manifest + совпадающий final marker означает finalized; иначе read-only recovery `RECOVERY_UNFINALIZED` | Missing marker/manifest и partial tail не выдаются за verified и не меняют raw |
| Stop reason мог выглядеть независимо вычисленным | Replay просто читал его из manifest | Stop control хранится в raw lifecycle log; replay явно сообщает источник. Metadata fallback помечается как не независимый | `stop_reason_independently_replayed=true` только при control record |
| Ошибки finalization закрывали ресурсы неполно | Reserve/report/manifest и исключения не имели полного набора fault tests | Общий preflight учитывает report, manifest и marker; exclusive writes проверяют short write, flush и fsync; handles закрываются в `finally` | Cap/reserve, short write, flush и raw fsync failures оставляют run без final marker |

## Семантика состояния и времени

Состояние книги разделено на: последнее принятое атомарное состояние; текущую пригодность continuity; bounded recovery buffer. При malformed/crossed/пропущенном необходимом depth принятое состояние не мутирует, но использовать его для продолжения нельзя до корректного snapshot. Resnapshot не восстанавливает потерянную историю.

Duration interval начинается с первого наблюдаемого ingress/control timestamp. Каждый положительный monotonic delta относится к состоянию, действовавшему до следующего event/control record. Явный stop clock закрывает последний интервал даже без нового market message. После обратного monotonic величина разрыва не угадывается: увеличивается `unknown_duration_intervals`, а измеряемая длительность продолжается с новой anchor. Поэтому `valid_duration_ns + invalid_duration_ns = measured_duration_ns` только для сопоставимых clock epochs; unknown interval публикуется отдельно.

UTC step при непрерывном monotonic учитывается диагностически и не блокирует обработку. UTC/monotonic ingress timestamps по-прежнему не являются точной сетевой задержкой.

## Определения ratios

- `observed_sequence_continuity`: numerator — sequence-evaluable depth observations без обнаруженного gap; denominator — все sequence-evaluable depth observations; `gaps` публикуется отдельно. Метрика не доказывает полноту исходного потока до приложения или вне наблюдаемой session.
- `sequence_observation_coverage`: numerator — depth observations, для которых sequence стал evaluable; denominator — уникальные полученные depth event records.
- `book_invariants`: numerator — успешные проверки candidate book; denominator — все выполненные invariant checks; validation failures до candidate check учитываются в rejected records, но не маскируются как invariant pass.
- `comparable_share`: exact retained-ID bookTicker comparisons / все классифицированные bookTicker observations.
- `mismatch_rate`: exact-ID inconsistencies / exact-ID comparisons. Нулевой denominator остаётся `NOT_EVALUABLE`.

Ни одна из этих метрик сама по себе не устанавливает upstream completeness или корректность Binance delivery.

## Replay и integrity

Finalized run содержит raw event/control records, capture report, effective config, schema versions, размеры и SHA-256 raw/report, manifest и отдельный SHA-256 finalization marker. Replay сначала проверяет marker, manifest, размеры и hashes, затем независимо пересчитывает книгу, transitions, counters, durations и ratios из потоково прочитанного raw. Capture-side `bytes_written`, processing mode и integrity label не маскируются под независимо воспроизведённые поля.

При отсутствии final marker replay работает только как read-only recovery, сообщает `RECOVERY_UNFINALIZED`, обнаруживает partial tail и не изменяет raw. Если stop reason доступен только из metadata, `stop_reason_independently_replayed=false`.

Ограничение: локальные hashes обнаруживают случайное или одиночное изменение. Они не являются цифровой подписью и не защищают от согласованной перезаписи raw, manifest и marker лицом с доступом к каталогу. Отдельный M20.2R artifact manifest фиксирует опубликованный локальный checkpoint.

## Новый synthetic namespace

Новый create-only namespace: `data/research/m20_2r_offline_capture_v1`. Финальный run — `final_run`; более ранний `verified_run` сохранён и не перезаписан.

Финальный run использует non-default bounded config и содержит 10 event records, один stop control, две sessions, численно эквивалентный BBO с разным форматированием, rejected malformed depth, recovery snapshot и session reset.

- received/persisted/rejected: 10/10/1;
- unique depth observations: 4;
- sequence-evaluable observations: 3/4, observed passes: 3/3;
- comparable/unmatched/stale/inconsistent: 2/1/0/0;
- exact-ID agreement: 2/2;
- book invariant checks: 6/6;
- valid/invalid/unknown: 8 s / 4 s / 0 intervals;
- measured duration: 12 s;
- capture stop source: runtime; replay stop source: control record;
- replay: `VERIFIED_FINALIZED`, hashes verified, derived fields match, stop independently replayed;
- весь новый namespace: 14 файлов, 29 046 байт, значительно меньше 100 MiB.

Синтетические durations, rates и ordering не являются измерениями Binance или будущей производительности.

## Проверки

- Targeted M20.2/M20.2R: **44 passed in 0.11 s**.
- Полный WSL pytest: **374 passed, 0 failed, 0 skipped, 3 существующих dependency warnings in 25.33 s**.
- Protected fixture/manifest selection: **15 passed in 0.84 s**.
- `pip check`: **No broken requirements found**.
- Исходный M20.2 namespace и четыре исходных документа: **14/14 hashes совпадают с baseline**.
- Existing WSL protected fixtures: **55/55 hashes совпадают**.
- Новый artifact manifest проверен отдельно; `git diff --check` прошёл после формирования окончательных документов.

Warnings — прежние deprecation warnings из установленных `python-binance`/`websockets`; зависимости не менялись.

## Оставшиеся ограничения

Live transport отсутствует. Не проверены реальные Binance schemas/rates, REST snapshot/reconnect, callback timing, sustained queue contention, idle timer, реальная нагрузка CPU/RAM/диска, clock discipline, filesystem/power-loss durability, внешний конкурентный расход диска и длительное восстановление. JSONL остаётся узким research-прототипом, а не универсальной storage system.

Перед отдельным решением о Stage A требуются review live public-market adapter, idle/reconnect controls, реальный resource budget, terms/retention, clock monitoring и доказательство отсутствия account/order endpoints. M20.2R не запускает Stage A/B и не разрешает автоматический переход к ним.
