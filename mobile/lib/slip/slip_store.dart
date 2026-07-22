import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import 'slip_event.dart';

/// Persisted slip row stored in per-day JSON files.
class StoredSlip {
  final String eventId;
  final String? refNumber;
  final String imagePath;
  final String capturedAt;
  final bool sent;
  final String bank;
  final double? amount;
  final String? senderName;
  final String? receiverAccountLast4;
  final double ocrConfidence;
  final bool parseFailed;

  const StoredSlip({
    required this.eventId,
    required this.refNumber,
    required this.imagePath,
    required this.capturedAt,
    required this.sent,
    required this.bank,
    required this.amount,
    required this.senderName,
    required this.receiverAccountLast4,
    required this.ocrConfidence,
    required this.parseFailed,
  });

  factory StoredSlip.fromEvent(SlipEvent event, {bool sent = false}) {
    return StoredSlip(
      eventId: event.eventId,
      refNumber: event.refNumber,
      imagePath: event.localImagePath,
      capturedAt: event.capturedAt,
      sent: sent,
      bank: event.bank,
      amount: event.amount,
      senderName: event.senderName,
      receiverAccountLast4: event.receiverAccountLast4,
      ocrConfidence: event.ocrConfidence,
      parseFailed: event.parseFailed,
    );
  }

  factory StoredSlip.fromJson(Map<String, dynamic> json) {
    return StoredSlip(
      eventId: json['event_id'] as String,
      refNumber: json['ref_number'] as String?,
      imagePath: json['image_path'] as String,
      capturedAt: json['captured_at'] as String,
      sent: json['sent'] as bool? ?? false,
      bank: json['bank'] as String? ?? 'UNKNOWN',
      amount: (json['amount'] as num?)?.toDouble(),
      senderName: json['sender_name'] as String?,
      receiverAccountLast4: json['receiver_account_last4'] as String?,
      ocrConfidence: (json['ocr_confidence'] as num?)?.toDouble() ?? 0.0,
      parseFailed: json['parse_failed'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
        'event_id': eventId,
        'ref_number': refNumber,
        'image_path': imagePath,
        'captured_at': capturedAt,
        'sent': sent,
        'bank': bank,
        'amount': amount,
        'sender_name': senderName,
        'receiver_account_last4': receiverAccountLast4,
        'ocr_confidence': ocrConfidence,
        'parse_failed': parseFailed,
      };

  SlipEvent toSlipEvent() => SlipEvent(
        eventId: eventId,
        capturedAt: capturedAt,
        bank: bank,
        amount: amount,
        senderName: senderName,
        receiverAccountLast4: receiverAccountLast4,
        refNumber: refNumber,
        ocrConfidence: ocrConfidence,
        parseFailed: parseFailed,
        localImagePath: imagePath,
      );
}

/// JSON per-day slip store under the app documents directory.
///
/// Files live at `{docs}/slips/YYYY-MM-DD.json`. [init] runs a simple cleanup
/// that drops day files / entries older than [retentionDays] (default 90).
class SlipStore {
  SlipStore({
    required this.slipsDir,
    this.retentionDays = 90,
    DateTime Function()? now,
  }) : _now = now ?? DateTime.now;

  final Directory slipsDir;
  final int retentionDays;
  final DateTime Function() _now;

  static Future<SlipStore> open({int retentionDays = 90}) async {
    final docs = await getApplicationDocumentsDirectory();
    final store = SlipStore(
      slipsDir: Directory('${docs.path}/slips'),
      retentionDays: retentionDays,
    );
    await store.init();
    return store;
  }

  Future<void> init() async {
    await slipsDir.create(recursive: true);
    await _cleanupOldEntries();
  }

  Future<void> save(SlipEvent event, {bool sent = false}) async {
    final stored = StoredSlip.fromEvent(event, sent: sent);
    final dayFile = _dayFileFor(stored.capturedAt);
    final entries = await _readDayFile(dayFile);

    final index = entries.indexWhere((e) => e.eventId == stored.eventId);
    if (index >= 0) {
      entries[index] = stored;
    } else {
      entries.add(stored);
    }

    await _writeDayFile(dayFile, entries);
  }

  Future<List<StoredSlip>> byDateRange(DateTime start, DateTime end) async {
    final normalizedStart = _dateOnly(start);
    final normalizedEnd = _dateOnly(end);
    final results = <StoredSlip>[];

    for (final dayFile in await _dayFilesInRange(normalizedStart, normalizedEnd)) {
      for (final entry in await _readDayFile(dayFile)) {
        final captured = DateTime.parse(entry.capturedAt);
        final capturedDay = _dateOnly(captured);
        if (!capturedDay.isBefore(normalizedStart) &&
            !capturedDay.isAfter(normalizedEnd)) {
          results.add(entry);
        }
      }
    }

    results.sort((a, b) => a.capturedAt.compareTo(b.capturedAt));
    return results;
  }

  Future<List<StoredSlip>> unsent() async {
    final results = <StoredSlip>[];
    await for (final dayFile in _allDayFiles()) {
      for (final entry in await _readDayFile(dayFile)) {
        if (!entry.sent) {
          results.add(entry);
        }
      }
    }
    results.sort((a, b) => a.capturedAt.compareTo(b.capturedAt));
    return results;
  }

  Future<void> markSent(String eventId) async {
    await for (final dayFile in _allDayFiles()) {
      final entries = await _readDayFile(dayFile);
      final index = entries.indexWhere((e) => e.eventId == eventId);
      if (index < 0) {
        continue;
      }

      entries[index] = StoredSlip(
        eventId: entries[index].eventId,
        refNumber: entries[index].refNumber,
        imagePath: entries[index].imagePath,
        capturedAt: entries[index].capturedAt,
        sent: true,
        bank: entries[index].bank,
        amount: entries[index].amount,
        senderName: entries[index].senderName,
        receiverAccountLast4: entries[index].receiverAccountLast4,
        ocrConfidence: entries[index].ocrConfidence,
        parseFailed: entries[index].parseFailed,
      );
      await _writeDayFile(dayFile, entries);
      return;
    }
  }

  Future<void> _cleanupOldEntries() async {
    final cutoff = _dateOnly(_now().subtract(Duration(days: retentionDays)));

    await for (final dayFile in _allDayFiles()) {
      final day = _dayFromFileName(dayFile.path);
      if (day != null && day.isBefore(cutoff)) {
        final entries = await _readDayFile(dayFile);
        await _deleteImagesForEntries(entries);
        await dayFile.delete();
        continue;
      }

      final entries = await _readDayFile(dayFile);
      final kept = entries.where((entry) {
        final capturedDay = _dateOnly(DateTime.parse(entry.capturedAt));
        return !capturedDay.isBefore(cutoff);
      }).toList();

      if (kept.isEmpty) {
        await _deleteImagesForEntries(entries);
        await dayFile.delete();
      } else if (kept.length != entries.length) {
        final keptIds = kept.map((e) => e.eventId).toSet();
        final dropped =
            entries.where((entry) => !keptIds.contains(entry.eventId));
        await _deleteImagesForEntries(dropped);
        await _writeDayFile(dayFile, kept);
      }
    }
  }

  Future<void> _deleteImagesForEntries(Iterable<StoredSlip> entries) async {
    for (final entry in entries) {
      await _deleteImageIfExists(entry.imagePath);
    }
  }

  Future<void> _deleteImageIfExists(String imagePath) async {
    if (imagePath.isEmpty || imagePath.startsWith('content://')) {
      return;
    }

    try {
      final file = File(imagePath);
      if (await file.exists()) {
        await file.delete();
      }
    } catch (_) {
      // Ignore missing files or permission errors.
    }
  }

  File _dayFileFor(String capturedAt) {
    final day = _dateOnly(DateTime.parse(capturedAt));
    return File('${slipsDir.path}/${_formatDay(day)}.json');
  }

  Future<List<StoredSlip>> _readDayFile(File file) async {
    if (!await file.exists()) {
      return [];
    }

    final decoded = jsonDecode(await file.readAsString());
    if (decoded is! List) {
      return [];
    }

    return decoded
        .whereType<Map>()
        .map((entry) => StoredSlip.fromJson(Map<String, dynamic>.from(entry)))
        .toList();
  }

  Future<void> _writeDayFile(File file, List<StoredSlip> entries) async {
    final encoded = jsonEncode(entries.map((e) => e.toJson()).toList());
    await file.writeAsString(encoded);
  }

  Stream<File> _allDayFiles() async* {
    if (!await slipsDir.exists()) {
      return;
    }

    await for (final entity in slipsDir.list()) {
      if (entity is File && entity.path.endsWith('.json')) {
        yield entity;
      }
    }
  }

  Future<List<File>> _dayFilesInRange(DateTime start, DateTime end) async {
    final files = <File>[];
    var cursor = start;
    while (!cursor.isAfter(end)) {
      files.add(File('${slipsDir.path}/${_formatDay(cursor)}.json'));
      cursor = cursor.add(const Duration(days: 1));
    }
    return files;
  }

  static DateTime _dateOnly(DateTime value) =>
      DateTime(value.year, value.month, value.day);

  static String _formatDay(DateTime day) {
    final month = day.month.toString().padLeft(2, '0');
    final dayOfMonth = day.day.toString().padLeft(2, '0');
    return '${day.year}-$month-$dayOfMonth';
  }

  static DateTime? _dayFromFileName(String path) {
    final name = path.split(Platform.pathSeparator).last;
    if (!name.endsWith('.json')) {
      return null;
    }
    final datePart = name.substring(0, name.length - '.json'.length);
    final match = RegExp(r'^(\d{4})-(\d{2})-(\d{2})$').firstMatch(datePart);
    if (match == null) {
      return null;
    }
    return DateTime(
      int.parse(match.group(1)!),
      int.parse(match.group(2)!),
      int.parse(match.group(3)!),
    );
  }
}
