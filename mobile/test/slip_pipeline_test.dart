import 'dart:io';

import 'package:clipsync_app/slip/slip_event.dart';
import 'package:clipsync_app/slip/slip_ocr.dart';
import 'package:clipsync_app/slip/slip_pipeline.dart';
import 'package:clipsync_app/slip/slip_store.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeSlipOcr implements SlipOcr {
  FakeSlipOcr(this.rawText, {this.confidence = 0.85});

  final String rawText;
  final double confidence;

  @override
  Future<SlipOcrResult> run(String imagePath) async {
    return SlipOcrResult(rawText: rawText, confidence: confidence);
  }
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

  test('falls back to uri when path missing', () async {
    final pipeline = SlipPipeline(
      ocr: FakeSlipOcr('random text'),
      store: store,
    );

    final result = await pipeline.processWatcherEvent({
      'uri': 'content://media/external/images/media/99',
    });

    expect(result!.localImagePath, 'content://media/external/images/media/99');
  });
}
