-- Three floors. Ground takes the big stuff, the upper floors are cars and
-- bikes, which is why slot allocation has to prefer the smallest slot that
-- fits rather than the first one it finds.
INSERT INTO slots (code, floor, size)
SELECT 'G-T' || g, 0, 'oversize' FROM generate_series(1, 4) g
ON CONFLICT (code) DO NOTHING;
INSERT INTO slots (code, floor, size)
SELECT 'G-L' || g, 0, 'large' FROM generate_series(1, 6) g
ON CONFLICT (code) DO NOTHING;
INSERT INTO slots (code, floor, size)
SELECT '1-C' || lpad(g::text, 2, '0'), 1, 'compact' FROM generate_series(1, 12) g
ON CONFLICT (code) DO NOTHING;
INSERT INTO slots (code, floor, size)
SELECT '1-L' || g, 1, 'large' FROM generate_series(1, 3) g
ON CONFLICT (code) DO NOTHING;
INSERT INTO slots (code, floor, size)
SELECT '2-C' || lpad(g::text, 2, '0'), 2, 'compact' FROM generate_series(1, 10) g
ON CONFLICT (code) DO NOTHING;
INSERT INTO slots (code, floor, size)
SELECT '2-B' || lpad(g::text, 2, '0'), 2, 'bike' FROM generate_series(1, 15) g
ON CONFLICT (code) DO NOTHING;

-- Vehicles already inside, including KA01LONG26 which has been here 26 hours.
-- Check that one out and the daily-cap argument appears in a real receipt.
INSERT INTO tickets (plate, vehicle_kind, slot_id, entered_at)
SELECT v.plate, v.kind, s.id, v.entered_at
FROM (VALUES
  ('KA01LONG26', 'compact',  '1-C01', now() - INTERVAL '26 hours'),
  ('KA02NIGHT9', 'large',    'G-L1',  now() - INTERVAL '9 hours'),
  ('KA03QUICK1', 'bike',     '2-B01', now() - INTERVAL '20 minutes'),
  ('KA04CAPPED', 'compact',  '1-C02', now() - INTERVAL '14 hours'),
  ('KA05TRUCK1', 'oversize', 'G-T1',  now() - INTERVAL '3 hours'),
  ('KA06WEEKLY', 'compact',  '2-C01', now() - INTERVAL '49 hours')
) AS v(plate, kind, code, entered_at)
JOIN slots s ON s.code = v.code
WHERE NOT EXISTS (SELECT 1 FROM tickets);

-- A settled ticket, so the history is not empty on the first page load.
INSERT INTO tickets (plate, vehicle_kind, slot_id, entered_at, exited_at, fee_paise)
SELECT 'KA09GONE00', 'compact', s.id,
       now() - INTERVAL '30 hours', now() - INTERVAL '28 hours', 8000
FROM slots s WHERE s.code = '1-C03'
  AND NOT EXISTS (SELECT 1 FROM tickets WHERE plate='KA09GONE00');
