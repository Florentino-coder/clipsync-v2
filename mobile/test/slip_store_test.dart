import 'dart:convert';
import 'dart:io';

import 'package:clipsync_app/slip/slip_event.dart';
import 'package:clipsync_app/slip/slip_store.dart';
import 'package:flutter_test/flutter_test.dart';

SlipEvent _sampleEvent({
  required String eventId,
  required String capturedAt,
  String refNumber = '202607221432001',
}) {
  return SlipEvent(
    eventId: eventId,
    capturedAt: capturedAt,
    bank: 'SCB',
    amount: 350.0,
    senderName: null,
    receiverAccountLast4: '6789',
    refNumber: refNumber,
    ocrConfidence: 0.9,
    parseFailed: false,
    localImagePath: '/tmp/slip-$eventId.jpg',
  );
}

void main() {
  late Directory tempDir;
  late SlipStore store;

  setUp(() async {
    tempDir = await Directory.systemTemp.createTemp('slip_store_test_');
    store = SlipStore(slipsDir: Directory('${tempDir.path}/slips'));
    await store.init();
  });

  tearDown(() async {
    if (await tempDir.exists()) {
      await tempDir.delete(recursive: true);
    }
  });

  test('save writes per-day JSON with sent flag default false', () async {
    final event = _sampleEvent(
      eventId: 'evt-1',
      capturedAt: '2026-07-22T17:00:00+07:00',
    );

    await store.save(event);

    final dayFile = File('${tempDir.path}/slips/2026-07-22.json');
    expect(await dayFile.exists(), isTrue);

    final entries = jsonDecode(await dayFile.readAsString()) as List;
    expect(entries, hasLength(1));
    expect(entries.first['event_id'], 'evt-1');
    expect(entries.first['ref_number'], '202607221432001');
    expect(entries.first['image_path'], event.localImagePath);
    expect(entries.first['sent'], isFalse);
    expect(entries.first['bank'], 'SCB');
    expect(entries.first['ocr_confidence'], 0.9);
  });

  test('byDateRange returns events within inclusive dates', () async {
    await store.save(_sampleEvent(
      eventId: 'evt-a',
      capturedAt: '2026-07-20T10:00:00+07:00',
    ));
    await store.save(_sampleEvent(
      eventId: 'evt-b',
      capturedAt: '2026-07-22T10:00:00+07:00',
    ));
    await store.save(_sampleEvent(
      eventId: 'evt-c',
      capturedAt: '2026-07-25T10:00:00+07:00',
    ));

    final results = await store.byDateRange(
      DateTime(2026, 7, 21),
      DateTime(2026, 7, 23),
    );

    expect(results.map((e) => e.eventId), ['evt-b']);
  });

  test('unsent and markSent track delivery state', () async {
    await store.save(_sampleEvent(
      eventId: 'evt-1',
      capturedAt: '2026-07-22T10:00:00+07:00',
    ));
    await store.save(_sampleEvent(
      eventId: 'evt-2',
      capturedAt: '2026-07-22T11:00:00+07:00',
    ));

    var unsent = await store.unsent();
    expect(unsent.map((e) => e.eventId), containsAll(['evt-1', 'evt-2']));

    await store.markSent('evt-1');

    unsent = await store.unsent();
    expect(unsent.map((e) => e.eventId), ['evt-2']);
  });

  test('init removes entries older than retention window', () async {
    final slipsDir = Directory('${tempDir.path}/slips_retention');
    await slipsDir.create(recursive: true);

    final oldDayFile = File('${slipsDir.path}/2026-01-01.json');
    await oldDayFile.writeAsString(jsonEncode([
      {
        'event_id': 'old-evt',
        'ref_number': '202601011200000001',
        'image_path': '/tmp/old.jpg',
        'captured_at': '2026-01-01T12:00:00+07:00',
        'sent': true,
        'bank': 'SCB',
        'amount': 100.0,
        'sender_name': null,
        'receiver_account_last4': '1234',
        'ocr_confidence': 0.8,
        'parse_failed': false,
      },
    ]));

    final recentDayFile = File('${slipsDir.path}/2026-07-22.json');
    await recentDayFile.writeAsString(jsonEncode([
      {
        'event_id': 'recent-evt',
        'ref_number': '202607221432001',
        'image_path': '/tmp/recent.jpg',
        'captured_at': '2026-07-22T12:00:00+07:00',
        'sent': false,
        'bank': 'SCB',
        'amount': 200.0,
        'sender_name': null,
        'receiver_account_last4': '5678',
        'ocr_confidence': 0.9,
        'parse_failed': false,
      },
    ]));

    final retentionStore = SlipStore(
      slipsDir: slipsDir,
      retentionDays: 90,
      now: () => DateTime(2026, 7, 22),
    );
    await retentionStore.init();

    expect(await oldDayFile.exists(), isFalse);
    expect(await recentDayFile.exists(), isTrue);

    final unsent = await retentionStore.unsent();
    expect(unsent.map((e) => e.eventId), ['recent-evt']);
  });

  test('stored entries reconstruct SlipEvent', () async {
    final event = _sampleEvent(
      eventId: 'evt-reconstruct',
      capturedAt: '2026-07-22T17:00:00+07:00',
    );
    await store.save(event);

    final stored = await store.unsent();
    expect(stored, hasLength(1));

    final reconstructed = stored.first.toSlipEvent();
    expect(reconstructed.eventId, event.eventId);
    expect(reconstructed.capturedAt, event.capturedAt);
    expect(reconstructed.bank, event.bank);
    expect(reconstructed.amount, event.amount);
    expect(reconstructed.refNumber, event.refNumber);
    expect(reconstructed.localImagePath, event.localImagePath);
    expect(reconstructed.ocrConfidence, event.ocrConfidence);
    expect(reconstructed.parseFailed, event.parseFailed);
  });
}
