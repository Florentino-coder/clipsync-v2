import 'package:flutter/services.dart';

/// Resolves Android `content://` image URIs to cache files for ML Kit OCR.
class SlipContentUri {
  static const methodChannelName = 'clipsync/slip_methods';
  static const _channel = MethodChannel(methodChannelName);

  /// Copies [uriString] into the app cache and returns an absolute file path.
  static Future<String> copyToCache(String uriString) async {
    final path = await _channel.invokeMethod<String>(
      'copyContentUriToCache',
      uriString,
    );
    if (path == null || path.isEmpty) {
      throw StateError('copyContentUriToCache returned empty path');
    }
    return path;
  }
}
