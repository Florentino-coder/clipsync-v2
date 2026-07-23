import 'dart:convert';
import 'dart:io';

import 'package:clipsync_app/slip/outbox.dart';
import 'package:clipsync_app/slip/slip_event.dart';
import 'package:clipsync_app/slip/slip_store.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;

const _secret = 'test-secret-key-32chars!!!!!!!!';

SlipEvent _sampleEvent({
  required String eventId,
  required String capturedAt,
}) {
  return SlipEvent(
    eventId: eventId,
    capturedAt: capturedAt,
    bank: 'SCB',
    amount: 350.0,
    senderName: null,
    receiverAccountLast4: '6789',
    refNumber: '202607221432001',
    ocrConfidence: 0.9,
    parseFailed: false,
    localImagePath: '/tmp/slip-$eventId.jpg',
  );
}

/// Canonical JSON matching PC `slip_payload_sig` (sorted keys, compact).
String _canonicalJson(Map<String, dynamic> payload) {
  final sorted = Map<String, dynamic>.fromEntries(
    payload.entries.toList()..sort((a, b) => a.key.compareTo(b.key)),
  );
  return jsonEncode(sorted);
}

String _expectedSig(Map<String, dynamic> payload) {
  return Hmac(sha256, utf8.encode(_secret))
      .convert(utf8.encode(_canonicalJson(payload)))
      .toString();
}

void main() {
  late Directory tempDir;
  late SlipStore store;
  late List<Map<String, dynamic>> sent;
  late SlipOutbox outbox;

  setUp(() async {
    tempDir = await Directory.systemTemp.createTemp('outbox_test_');
    store = SlipStore(slipsDir: Directory('${tempDir.path}/slips'));
    await store.init();
    sent = [];
    outbox = SlipOutbox(
      store: store,
      sharedSecret: _secret,
      send: (message) async {
        sent.add(Map<String, dynamic>.from(message));
      },
    );
  });

  tearDown(() async {
    if (await tempDir.exists()) {
      await tempDir.delete(recursive: true);
    }
  });

  test('enqueue saves unsent and sends until ack', () async {
    final event = _sampleEvent(
      eventId: 'evt-1',
      capturedAt: '2026-07-22T17:00:00+07:00',
    );

    await outbox.enqueue(event);

    final unsent = await store.unsent();
    expect(unsent.map((e) => e.eventId), ['evt-1']);
    expect(sent, hasLength(1));
    expect(sent.first['type'], 'slip_event');
    expect(sent.first['payload']['event_id'], 'evt-1');
    expect(sent.first.containsKey('sig'), isFalse);

    await outbox.handleIncoming({
      'type': 'slip_ack',
      'event_id': 'evt-1',
    });

    expect(await store.unsent(), isEmpty);
  });

  test('reconnect resends all unsent events', () async {
    await outbox.enqueue(_sampleEvent(
      eventId: 'evt-a',
      capturedAt: '2026-07-22T10:00:00+07:00',
    ));
    await outbox.enqueue(_sampleEvent(
      eventId: 'evt-b',
      capturedAt: '2026-07-22T11:00:00+07:00',
    ));
    sent.clear();

    await outbox.onReconnect();

    expect(sent.map((m) => m['payload']['event_id']), ['evt-a', 'evt-b']);
    expect(await store.unsent().then((u) => u.map((e) => e.eventId).toList()),
        ['evt-a', 'evt-b']);
  });

  test('relay path attaches HMAC sig of payload', () async {
    final event = _sampleEvent(
      eventId: 'evt-relay',
      capturedAt: '2026-07-22T12:00:00+07:00',
    );

    await outbox.enqueue(event, forRelay: true);

    expect(sent, hasLength(1));
    final message = sent.first;
    expect(message['type'], 'slip_event');
    final payload = Map<String, dynamic>.from(message['payload'] as Map);
    expect(message['sig'], _expectedSig(payload));
    expect(signSlipPayload(_secret, payload), _expectedSig(payload));
  });

  test('relay message may include thumbnail outside signed payload', () async {
    final big = img.Image(width: 200, height: 300);
    img.fill(big, color: img.ColorRgb8(10, 20, 30));
    final path = '${tempDir.path}/slip.png';
    await File(path).writeAsBytes(img.encodePng(big));

    final event = SlipEvent(
      eventId: 'evt-thumb',
      capturedAt: '2026-07-22T12:00:00+07:00',
      bank: 'SCB',
      amount: 100.0,
      senderName: null,
      receiverAccountLast4: '1234',
      refNumber: 'R1',
      ocrConfidence: 0.9,
      parseFailed: false,
      localImagePath: path,
    );

    await outbox.enqueue(event, forRelay: true);

    final message = sent.single;
    final payload = Map<String, dynamic>.from(message['payload'] as Map);
    expect(payload.containsKey('thumbnail_jpeg_b64'), isFalse);
    expect(message['sig'], _expectedSig(payload));
    expect(message['thumbnail_jpeg_b64'], isA<String>());
    expect((message['thumbnail_jpeg_b64'] as String).isNotEmpty, isTrue);
  });

  test('ack for unknown event_id is ignored', () async {
    await outbox.enqueue(_sampleEvent(
      eventId: 'evt-keep',
      capturedAt: '2026-07-22T13:00:00+07:00',
    ));

    await outbox.handleIncoming({
      'type': 'slip_ack',
      'event_id': 'evt-other',
    });

    expect((await store.unsent()).map((e) => e.eventId), ['evt-keep']);
  });

  test('non-ack messages are ignored', () async {
    await outbox.enqueue(_sampleEvent(
      eventId: 'evt-1',
      capturedAt: '2026-07-22T14:00:00+07:00',
    ));

    await outbox.handleIncoming({'type': 'auth_ok'});

    expect((await store.unsent()).map((e) => e.eventId), ['evt-1']);
  });
}
