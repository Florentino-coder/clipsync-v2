import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

class SlipWatcher {
  static const channelName = 'clipsync/slip_events';
  static const _channel = EventChannel(channelName);

  Stream<Map<String, dynamic>> watch() => _channel
      .receiveBroadcastStream()
      .map((e) => Map<String, dynamic>.from(e as Map));

  Future<bool> requestPermission() async {
    final status = await Permission.photos.request();
    return status.isGranted;
  }
}
