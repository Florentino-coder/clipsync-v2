import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

/// Watches MediaStore for new slip images saved by bank apps.
///
/// Events include `uri` (content://…), `path` (filesystem path when available,
/// otherwise the same content URI), `bucket`, `relative_path`, and `date_added`.
class SlipWatcher {
  static const channelName = 'clipsync/slip_events';
  static const _channel = EventChannel(channelName);

  /// Emits slip image events from the native MediaStore observer.
  ///
  /// On Android 10+, [Map] values for `path` may be a `content://` URI when
  /// the legacy filesystem path is unavailable; prefer `uri` for content access.
  Stream<Map<String, dynamic>> watch() => _channel
      .receiveBroadcastStream()
      .map((e) => Map<String, dynamic>.from(e as Map));

  Future<bool> requestPermission() async {
    final status = await Permission.photos.request();
    return status.isGranted;
  }
}
