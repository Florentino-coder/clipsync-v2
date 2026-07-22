import 'dart:convert';
import 'dart:io';

import 'package:clipsync_app/slip/local_server.dart';
import 'package:clipsync_app/slip/outbox.dart';
import 'package:clipsync_app/slip/slip_event.dart';
import 'package:clipsync_app/slip/slip_store.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:web_socket_channel/web_socket_channel.dart';

const _sharedSecret = 'test-secret-key-32chars!!!!!!!!';

String _authHeader(String secret) {
  return Hmac(sha256, utf8.encode(secret))
      .convert(utf8.encode('clipsync-slip'))
      .toString();
}

Future<File> _createTestImage(String path) async {
  final image = img.Image(width: 4, height: 4);
  img.fill(image, color: img.ColorRgb8(200, 100, 50));
  final file = File(path);
  await file.writeAsBytes(img.encodePng(image));
  return file;
}

SlipEvent _sampleEvent({
  required String eventId,
  required String capturedAt,
  required String imagePath,
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
    localImagePath: imagePath,
  );
}

void main() {
  late Directory tempDir;
  late SlipStore store;
  late LocalSlipServer server;
  late int port;

  setUp(() async {
    tempDir = await Directory.systemTemp.createTemp('local_server_test_');
    store = SlipStore(slipsDir: Directory('${tempDir.path}/slips'));
    await store.init();
    server = LocalSlipServer(store, _sharedSecret);
    await server.start(port: 0);
    port = server.port;
  });

  tearDown(() async {
    await server.stop();
    if (await tempDir.exists()) {
      await tempDir.delete(recursive: true);
    }
  });

  test('GET /ping is unauthenticated health check', () async {
    final client = HttpClient();
    final request = await client.get('127.0.0.1', port, '/ping');
    final response = await request.close();

    expect(response.statusCode, 200);
    final body = await response.transform(utf8.decoder).join();
    expect(jsonDecode(body), {'app': 'clipsync', 'role': 'phone'});
    client.close();
  });

  test('GET /slips without X-Auth returns 401', () async {
    final client = HttpClient();
    final request = await client.get(
      '127.0.0.1',
      port,
      '/slips?from=2026-07-22&to=2026-07-22',
    );
    final response = await request.close();

    expect(response.statusCode, 401);
    client.close();
  });

  test('GET /slips with wrong X-Auth returns 401', () async {
    final client = HttpClient();
    final request = await client.get(
      '127.0.0.1',
      port,
      '/slips?from=2026-07-22&to=2026-07-22',
    );
    request.headers.set('X-Auth', 'bad-token');
    final response = await request.close();

    expect(response.statusCode, 401);
    client.close();
  });

  test('GET /slips with valid auth returns slips with compressed base64 images',
      () async {
    final imageFile =
        await _createTestImage('${tempDir.path}/slip-evt-1.png');
    await store.save(_sampleEvent(
      eventId: 'evt-1',
      capturedAt: '2026-07-22T10:00:00+07:00',
      imagePath: imageFile.path,
    ));

    final client = HttpClient();
    final request = await client.get(
      '127.0.0.1',
      port,
      '/slips?from=2026-07-22&to=2026-07-22',
    );
    request.headers.set('X-Auth', _authHeader(_sharedSecret));
    final response = await request.close();

    expect(response.statusCode, 200);
    final body = await response.transform(utf8.decoder).join();
    final items = jsonDecode(body) as List;

    expect(items, hasLength(1));
    expect(items.first['event_id'], 'evt-1');
    expect(items.first['ref_number'], '202607221432001');
    expect(items.first['image_base64'], isNotEmpty);

    final jpegBytes = base64Decode(items.first['image_base64'] as String);
    expect(jpegBytes.length, greaterThan(2));
    expect(jpegBytes[0], 0xFF);
    expect(jpegBytes[1], 0xD8);
    client.close();
  });

  test('GET /slips truncates to 50 images when more are stored', () async {
    for (var i = 0; i < 55; i++) {
      final imageFile =
          await _createTestImage('${tempDir.path}/slip-$i.png');
      await store.save(_sampleEvent(
        eventId: 'evt-$i',
        capturedAt: '2026-07-22T${(10 + (i % 10)).toString().padLeft(2, '0')}:00:00+07:00',
        imagePath: imageFile.path,
      ));
    }

    final client = HttpClient();
    final request = await client.get(
      '127.0.0.1',
      port,
      '/slips?from=2026-07-22&to=2026-07-22',
    );
    request.headers.set('X-Auth', _authHeader(_sharedSecret));
    final response = await request.close();

    expect(response.statusCode, 200);
    final body = await response.transform(utf8.decoder).join();
    final items = jsonDecode(body) as List;

    expect(items.length, LocalSlipServer.maxImagesPerRequest);
    client.close();
  });

  test('WebSocket rejects client without auth message', () async {
    final channel = WebSocketChannel.connect(
      Uri.parse('ws://127.0.0.1:$port/'),
    );

    channel.sink.add(jsonEncode({'type': 'hello'}));

    await expectLater(
      channel.stream.first,
      completion(isA<String>()),
    );

    await channel.sink.close();
    await channel.ready;
  });

  test('WebSocket accepts client after auth message', () async {
    final channel = WebSocketChannel.connect(
      Uri.parse('ws://127.0.0.1:$port/'),
    );

    channel.sink.add(jsonEncode({
      'type': 'auth',
      'token': _authHeader(_sharedSecret),
    }));

    await expectLater(
      channel.stream.first,
      completion(
        jsonEncode({'type': 'auth_ok'}),
      ),
    );

    expect(server.wsClientCount, 1);

    await channel.sink.close();
    await channel.ready;
  });

  test('WebSocket slip_ack is forwarded to outbox.handleIncoming', () async {
    final imageFile =
        await _createTestImage('${tempDir.path}/slip-ack.png');
    final event = _sampleEvent(
      eventId: 'evt-ack',
      capturedAt: '2026-07-22T15:00:00+07:00',
      imagePath: imageFile.path,
    );
    await store.save(event, sent: false);

    final sent = <Map<String, dynamic>>[];
    final outbox = SlipOutbox(
      store: store,
      sharedSecret: _sharedSecret,
      send: (message) async {
        sent.add(Map<String, dynamic>.from(message));
      },
    );
    await server.stop();
    server = LocalSlipServer(store, _sharedSecret, outbox: outbox);
    await server.start(port: 0);
    port = server.port;

    final channel = WebSocketChannel.connect(
      Uri.parse('ws://127.0.0.1:$port/'),
    );
    channel.sink.add(jsonEncode({
      'type': 'auth',
      'token': _authHeader(_sharedSecret),
    }));
    await expectLater(
      channel.stream.first,
      completion(jsonEncode({'type': 'auth_ok'})),
    );

    channel.sink.add(jsonEncode({
      'type': 'slip_ack',
      'event_id': 'evt-ack',
    }));

    await Future<void>.delayed(const Duration(milliseconds: 100));
    expect(await store.unsent(), isEmpty);

    await channel.sink.close();
  });

  test('WebSocket auth triggers outbox.onReconnect', () async {
    final imageFile =
        await _createTestImage('${tempDir.path}/slip-reconnect.png');
    final event = _sampleEvent(
      eventId: 'evt-reconnect',
      capturedAt: '2026-07-22T16:00:00+07:00',
      imagePath: imageFile.path,
    );
    await store.save(event, sent: false);

    final sent = <Map<String, dynamic>>[];
    final outbox = SlipOutbox(
      store: store,
      sharedSecret: _sharedSecret,
      send: (message) async {
        sent.add(Map<String, dynamic>.from(message));
      },
    );
    await server.stop();
    server = LocalSlipServer(store, _sharedSecret, outbox: outbox);
    await server.start(port: 0);
    port = server.port;

    final channel = WebSocketChannel.connect(
      Uri.parse('ws://127.0.0.1:$port/'),
    );
    channel.sink.add(jsonEncode({
      'type': 'auth',
      'token': _authHeader(_sharedSecret),
    }));
    await expectLater(
      channel.stream.first,
      completion(jsonEncode({'type': 'auth_ok'})),
    );

    await Future<void>.delayed(const Duration(milliseconds: 100));
    expect(sent, isNotEmpty);
    expect(sent.first['type'], 'slip_event');
    expect(sent.first['payload']['event_id'], 'evt-reconnect');

    await channel.sink.close();
  });
}
