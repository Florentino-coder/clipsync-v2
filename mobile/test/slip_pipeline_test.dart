import 'dart:async';
import 'dart:io';

import 'package:clipsync_app/slip/outbox.dart';
import 'package:clipsync_app/slip/slip_event.dart';
import 'package:clipsync_app/slip/slip_ocr.dart';
import 'package:clipsync_app/slip/slip_pipeline.dart';
import 'package:clipsync_app/slip/slip_store.dart';
import 'package:clipsync_app/slip/slip_watcher.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeSlipOcr implements SlipOcr {
  FakeSlipOcr(this.rawText, {this.confidence = 0.85});

  final String rawText;
  final double confidence;
  final List<String> seenPaths = [];

  @override
  Future<SlipOcrResult> run(String imagePath) async {
    seenPaths.add(imagePath);
    return SlipOcrResult(rawText: rawText, confidence: confidence);
  }
}

class FakeSlipWatcher extends SlipWatcher {
  FakeSlipWatcher(this._controller);

  final StreamController<Map<String, dynamic>> _controller;

  @override
  Stream<Map<String, dynamic>> watch() => _controller.stream;
}

void main() {
  late Directory tempDir;
  late SlipStore store;

  setUp(() async {
    tempDir = await Directory.systemTemp.createTemp('slip_pipeline_test_');
    store = SlipStore(slipsDir: Directory('${tempDir.path}/slips'));
    await store.init();
  });

  tearDown(() async {
    if (await tempDir.exists()) {
      await tempDir.delete(recursive: true);
    }
  });

  test('processes watcher event through OCR parse and store', () async {
    final raw = File('test/fixtures/scb_01.txt').readAsStringSync();
    final pipeline = SlipPipeline(
      ocr: FakeSlipOcr(raw, confidence: 0.92),
      store: store,
    );

    SlipEvent? readyEvent;
    pipeline.onSlipReady = (event) async {
      readyEvent = event;
    };

    final result = await pipeline.processWatcherEvent({
      'path': '/data/user/0/com.clipsync/slip.jpg',
      'uri': 'content://media/external/images/media/1',
      'date_added': 1721638800,
    });

    expect(result, isNotNull);
    expect(result!.bank, 'SCB');
    expect(result.amount, 350.0);
    expect(result.refNumber, '202607221432001');
    expect(result.parseFailed, isFalse);
    expect(result.ocrConfidence, 0.92);
    expect(result.localImagePath, '/data/user/0/com.clipsync/slip.jpg');

    final unsent = await store.unsent();
    expect(unsent, hasLength(1));
    expect(unsent.first.eventId, result.eventId);

    expect(readyEvent, isNotNull);
    expect(readyEvent!.eventId, result.eventId);
  });

  test('marks parseFailed when OCR text is unrecognized', () async {
    final pipeline = SlipPipeline(
      ocr: FakeSlipOcr('random text no slip'),
      store: store,
    );

    final result = await pipeline.processWatcherEvent({
      'path': '/tmp/garbage.jpg',
    });

    expect(result, isNotNull);
    expect(result!.bank, 'UNKNOWN');
    expect(result.parseFailed, isTrue);
  });

  test('prefers path over uri for localImagePath', () async {
    final pipeline = SlipPipeline(
      ocr: FakeSlipOcr('random text'),
      store: store,
    );

    final result = await pipeline.processWatcherEvent({
      'uri': 'content://media/external/images/media/99',
      'path': '/storage/emulated/0/Pictures/slip.png',
    });

    expect(result!.localImagePath, '/storage/emulated/0/Pictures/slip.png');
  });

  test('resolves content:// uri to cache path before OCR', () async {
    final ocr = FakeSlipOcr('random text');
    final pipeline = SlipPipeline(
      ocr: ocr,
      store: store,
      contentUriCopier: (uri) async {
        expect(uri, 'content://media/external/images/media/99');
        return '/data/user/0/com.clipsync/cache/slip_copy.jpg';
      },
    );

    final result = await pipeline.processWatcherEvent({
      'uri': 'content://media/external/images/media/99',
    });

    expect(ocr.seenPaths, ['/data/user/0/com.clipsync/cache/slip_copy.jpg']);
    expect(
      result!.localImagePath,
      '/data/user/0/com.clipsync/cache/slip_copy.jpg',
    );
  });

  test('resolves content:// path to cache path before OCR', () async {
    final ocr = FakeSlipOcr('random text');
    final pipeline = SlipPipeline(
      ocr: ocr,
      store: store,
      contentUriCopier: (uri) async {
        expect(uri, 'content://media/external/images/media/7');
        return '/cache/from_path.jpg';
      },
    );

    final result = await pipeline.processWatcherEvent({
      'path': 'content://media/external/images/media/7',
      'uri': 'content://media/external/images/media/7',
    });

    expect(ocr.seenPaths, ['/cache/from_path.jpg']);
    expect(result!.localImagePath, '/cache/from_path.jpg');
  });

  test('does not copy when filesystem path is available', () async {
    var copied = false;
    final ocr = FakeSlipOcr('random text');
    final pipeline = SlipPipeline(
      ocr: ocr,
      store: store,
      contentUriCopier: (uri) async {
        copied = true;
        return '/should/not/be/used.jpg';
      },
    );

    final result = await pipeline.processWatcherEvent({
      'uri': 'content://media/external/images/media/99',
      'path': '/storage/emulated/0/Pictures/slip.png',
    });

    expect(copied, isFalse);
    expect(ocr.seenPaths, ['/storage/emulated/0/Pictures/slip.png']);
    expect(result!.localImagePath, '/storage/emulated/0/Pictures/slip.png');
  });

  test('watchAndProcess skips null results instead of throwing', () async {
    final controller = StreamController<Map<String, dynamic>>();
    final ocr = FakeSlipOcr(File('test/fixtures/scb_01.txt').readAsStringSync());
    final pipeline = SlipPipeline(
      ocr: ocr,
      store: store,
      watcher: FakeSlipWatcher(controller),
    );

    final received = <SlipEvent>[];
    Object? streamError;
    final sub = pipeline.watchAndProcess().listen(
      received.add,
      onError: (Object error) => streamError = error,
    );

    controller.add({}); // missing path/uri → null
    controller.add({
      'path': '/data/user/0/com.clipsync/good.jpg',
      'date_added': 1721638800,
    });
    await Future<void>.delayed(Duration.zero);
    await controller.close();
    await sub.cancel();

    expect(streamError, isNull);
    expect(received, hasLength(1));
    expect(received.single.bank, 'SCB');
    expect(ocr.seenPaths, ['/data/user/0/com.clipsync/good.jpg']);
  });

  test('watchAndProcess skips content URI copy failures', () async {
    final controller = StreamController<Map<String, dynamic>>();
    final ocr = FakeSlipOcr(File('test/fixtures/scb_01.txt').readAsStringSync());
    final pipeline = SlipPipeline(
      ocr: ocr,
      store: store,
      watcher: FakeSlipWatcher(controller),
      contentUriCopier: (_) async => throw StateError('copy failed'),
    );

    final received = <SlipEvent>[];
    Object? streamError;
    final sub = pipeline.watchAndProcess().listen(
      received.add,
      onError: (Object error) => streamError = error,
    );

    controller.add({'uri': 'content://media/external/images/media/bad'});
    controller.add({'path': '/data/user/0/com.clipsync/good.jpg'});
    await Future<void>.delayed(Duration.zero);
    await controller.close();
    await sub.cancel();

    expect(streamError, isNull);
    expect(received, hasLength(1));
    expect(ocr.seenPaths, ['/data/user/0/com.clipsync/good.jpg']);
  });

  test('optional outbox is enqueued when slip is ready', () async {
    final raw = File('test/fixtures/scb_01.txt').readAsStringSync();
    final sent = <Map<String, dynamic>>[];
    final outbox = SlipOutbox(
      store: store,
      sharedSecret: 'test-secret-key-32chars!!!!!!!!',
      send: (message) async {
        sent.add(Map<String, dynamic>.from(message));
      },
    );
    final pipeline = SlipPipeline(
      ocr: FakeSlipOcr(raw, confidence: 0.92),
      store: store,
      outbox: outbox,
    );

    final result = await pipeline.processWatcherEvent({
      'path': '/data/user/0/com.clipsync/slip.jpg',
      'date_added': 1721638800,
    });

    expect(result, isNotNull);
    expect(sent, hasLength(1));
    expect(sent.first['type'], 'slip_event');
    expect(sent.first['payload']['event_id'], result!.eventId);
    expect(
      (await store.unsent()).map((e) => e.eventId),
      [result.eventId],
    );
  });
}
