import 'package:uuid/uuid.dart';

import 'parsers/parser_registry.dart';
import 'slip_event.dart';
import 'slip_ocr.dart';
import 'slip_store.dart';
import 'slip_watcher.dart';

/// Watcher event → OCR → parse → persist → optional outbox hook.
class SlipPipeline {
  SlipPipeline({
    required SlipOcr ocr,
    required SlipStore store,
    SlipWatcher? watcher,
    Uuid? uuid,
  })  : _ocr = ocr,
        _store = store,
        _watcher = watcher ?? SlipWatcher(),
        _uuid = uuid ?? const Uuid();

  final SlipOcr _ocr;
  final SlipStore _store;
  final SlipWatcher _watcher;
  final Uuid _uuid;

  /// Called after a slip is OCR'd, parsed, and saved (Task 2.4 outbox hook).
  Future<void> Function(SlipEvent event)? onSlipReady;

  SlipWatcher get watcher => _watcher;

  Stream<SlipEvent> watchAndProcess() {
    return _watcher.watch().asyncMap((event) async {
      final slip = await processWatcherEvent(event);
      if (slip == null) {
        throw StateError('Failed to process slip watcher event');
      }
      return slip;
    });
  }

  Future<SlipEvent?> processWatcherEvent(Map<String, dynamic> event) async {
    final imagePath = _resolveImagePath(event);
    if (imagePath == null || imagePath.isEmpty) {
      return null;
    }

    final ocrResult = await _ocr.run(imagePath);
    final (bank, parsed) = ParserRegistry.parseAny(ocrResult.rawText);

    final slipEvent = SlipEvent(
      eventId: _uuid.v4(),
      capturedAt: _resolveCapturedAt(event),
      bank: bank,
      amount: parsed.amount,
      senderName: parsed.senderName,
      receiverAccountLast4: parsed.receiverAccountLast4,
      refNumber: parsed.refNumber,
      ocrConfidence: ocrResult.confidence,
      parseFailed: !parsed.valid,
      localImagePath: imagePath,
    );

    await _store.save(slipEvent);
    await onSlipReady?.call(slipEvent);
    return slipEvent;
  }

  String? _resolveImagePath(Map<String, dynamic> event) {
    final path = event['path'];
    if (path is String && path.isNotEmpty) {
      return path;
    }

    final uri = event['uri'];
    if (uri is String && uri.isNotEmpty) {
      return uri;
    }

    return null;
  }

  String _resolveCapturedAt(Map<String, dynamic> event) {
    final dateAdded = event['date_added'];
    if (dateAdded is int) {
      return DateTime.fromMillisecondsSinceEpoch(
        dateAdded * 1000,
        isUtc: false,
      ).toIso8601String();
    }
    if (dateAdded is num) {
      return DateTime.fromMillisecondsSinceEpoch(
        dateAdded.toInt() * 1000,
        isUtc: false,
      ).toIso8601String();
    }

    return DateTime.now().toIso8601String();
  }
}
