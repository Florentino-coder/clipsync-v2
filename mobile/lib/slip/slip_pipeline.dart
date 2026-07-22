import 'package:uuid/uuid.dart';

import 'parsers/parser_registry.dart';
import 'slip_event.dart';
import 'slip_ocr.dart';
import 'slip_store.dart';
import 'slip_watcher.dart';

/// Copies a `content://` URI to a local filesystem path ML Kit can read.
typedef ContentUriCopier = Future<String> Function(String contentUri);

/// Watcher event → OCR → parse → persist → optional outbox hook.
///
/// Layer 1 on phone = ML Kit Latin only. Thai name verification is optional
/// PC-side Layer 2 (EasyOCR) — see `pc/clipsync/thai_ocr.py` in a later task.
/// TODO: Do not block this mobile pipeline on Thai OCR / EasyOCR.
class SlipPipeline {
  SlipPipeline({
    required SlipOcr ocr,
    required SlipStore store,
    SlipWatcher? watcher,
    Uuid? uuid,
    ContentUriCopier? contentUriCopier,
  })  : _ocr = ocr,
        _store = store,
        _watcher = watcher ?? SlipWatcher(),
        _uuid = uuid ?? const Uuid(),
        _contentUriCopier = contentUriCopier;

  final SlipOcr _ocr;
  final SlipStore _store;
  final SlipWatcher _watcher;
  final Uuid _uuid;
  final ContentUriCopier? _contentUriCopier;

  /// Called after a slip is OCR'd, parsed, and saved.
  /// TODO(Task 2.4): wire this hook to the outbox / PC relay — not implemented yet.
  Future<void> Function(SlipEvent event)? onSlipReady;

  SlipWatcher get watcher => _watcher;

  /// Yields successfully processed slips; skips events that resolve to null.
  Stream<SlipEvent> watchAndProcess() async* {
    await for (final event in _watcher.watch()) {
      final slip = await processWatcherEvent(event);
      if (slip != null) {
        yield slip;
      }
    }
  }

  Future<SlipEvent?> processWatcherEvent(Map<String, dynamic> event) async {
    final imagePath = await _resolveReadableImagePath(event);
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

  /// Prefers a real filesystem [path]; copies `content://` URIs when needed.
  Future<String?> _resolveReadableImagePath(Map<String, dynamic> event) async {
    final path = event['path'];
    final uri = event['uri'];

    if (path is String && path.isNotEmpty && !_isContentUri(path)) {
      return path;
    }

    final contentUri = _pickContentUri(path, uri);
    if (contentUri != null) {
      final copier = _contentUriCopier;
      if (copier != null) {
        return copier(contentUri);
      }
      // TODO: provide a default Android content-resolver copy for production.
      return contentUri;
    }

    if (uri is String && uri.isNotEmpty) {
      return uri;
    }

    return null;
  }

  static String? _pickContentUri(dynamic path, dynamic uri) {
    if (path is String && _isContentUri(path)) {
      return path;
    }
    if (uri is String && _isContentUri(uri)) {
      return uri;
    }
    return null;
  }

  static bool _isContentUri(String value) => value.startsWith('content://');

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
