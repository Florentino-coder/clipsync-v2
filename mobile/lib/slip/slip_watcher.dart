import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

/// Watches MediaStore for new slip images saved by bank apps.
///
/// Events include `uri` (content://…), `path` (filesystem path when available,
/// otherwise the same content URI), `bucket`, `relative_path`, and `date_added`.
///
/// Manual hardware verification (Gate 1 checklist, remaining):
/// grant READ_MEDIA_IMAGES, start [watch], save/push an image into a bank
/// bucket, confirm logcat shows path/bucket/date_added.
class SlipWatcher {
  static const channelName = 'clipsync/slip_events';
  static const _channel = EventChannel(channelName);

  /// Maps a native EventChannel payload into a Dart map.
  @visibleForTesting
  static Map<String, dynamic> mapEvent(dynamic event) =>
      Map<String, dynamic>.from(event as Map);

  /// Emits slip image events from the native MediaStore observer.
  ///
  /// On Android 10+, [Map] values for `path` may be a `content://` URI when
  /// the legacy filesystem path is unavailable; prefer `uri` for content access.
  Stream<Map<String, dynamic>> watch() =>
      _channel.receiveBroadcastStream().map(mapEvent);

  /// Requests READ_MEDIA_IMAGES (photos) when the slip feature opens.
  Future<bool> requestPermission() async {
    final status = await Permission.photos.request();
    return status.isGranted;
  }
}
